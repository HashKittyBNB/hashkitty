// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC2981} from "@openzeppelin/contracts/token/common/ERC2981.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

interface IAgentRegistry {
    function isAgent(address account) external view returns (bool);
}

/// @title HashKitty — agent-gated, puzzle-to-mint pixel cats on BNB Smart Chain.
/// @notice Pure NFT (no token). A backend grades an EASY crypto/EVM puzzle solved by the
///         caller's AGENT, then signs an EIP-712 voucher. Mint is cheap (no VRF, no oracle,
///         plain `_mint`) so >=20 mints/block are possible. Rarity is rolled at REVEAL
///         (provably-fair via committed provenanceHash + revealSeed), NOT by puzzle difficulty.
contract HashKittyNFT is ERC721, ERC2981, Ownable, EIP712 {
    using Strings for uint256;
    using SafeERC20 for IERC20;

    uint256 public constant MAX_SUPPLY = 10_000;
    uint256 public constant MAX_PER_WALLET = 10;
    uint256 public constant BATCH_SIZE = 2_000;   // 5 batches of 2,000
    uint256 public constant NUM_BATCHES = 5;

    // Price per batch in BNB wei (native). Batch 0 = FREE. Owner-tunable (track USD as BNB moves).
    // index 0..4 -> GENESIS(free) · CIPHER · LATTICE · FORGE · SINGULARITY
    uint256[5] public batchPriceWei;

    address public treasury;               // receives mint fees (BNB) + royalties
    address public trustedSigner;          // backend puzzle-grader (issues vouchers)
    IAgentRegistry public agentRegistry;   // optional on-chain agent gate (0x0 => rely on voucher)

    uint256 public totalMinted;
    mapping(uint256 => bool) public usedNonce;
    mapping(address => uint256) public mintedBy;

    // ---- provably-fair reveal ----
    bytes32 public immutable provenanceHash; // keccak of the ordered trait manifest, committed at deploy
    bytes32 public revealSeed;               // set ONCE after sellout/timer
    bool public revealed;
    string private _unrevealedURI;           // placeholder (same for all pre-reveal)
    string private _baseTokenURI;            // set at reveal

    bytes32 private constant VOUCHER_TYPEHASH =
        keccak256("MintVoucher(address wallet,uint256 nonce,uint256 deadline)");

    event Minted(address indexed to, uint256 indexed tokenId, uint256 nonce, uint256 pricePaid);
    event Revealed(bytes32 seed, string baseURI);
    event SignerUpdated(address signer);
    event BatchPriceUpdated(uint256 indexed batch, uint256 priceWei);

    constructor(
        address _treasury,
        address _signer,
        bytes32 _provenanceHash,
        string memory unrevealedURI_,
        uint256[5] memory _batchPriceWei
    ) ERC721("HashKitty", "HKITTY") Ownable(msg.sender) EIP712("HashKitty", "1") {
        require(_treasury != address(0) && _signer != address(0), "zero addr");
        treasury = _treasury;
        trustedSigner = _signer;
        provenanceHash = _provenanceHash;
        _unrevealedURI = unrevealedURI_;
        batchPriceWei = _batchPriceWei;   // [free, 0.25, 0.5, 0.75, 1.0 USD-in-BNB] — owner-tunable
        _setDefaultRoyalty(_treasury, 500); // 5%
    }

    /// @notice Current batch index (0..4) based on how many are minted.
    function currentBatch() public view returns (uint256) {
        uint256 i = totalMinted / BATCH_SIZE;
        return i >= NUM_BATCHES ? NUM_BATCHES - 1 : i;
    }

    /// @notice Price (BNB wei) for the NEXT mint, given the current batch.
    function currentPrice() public view returns (uint256) {
        return batchPriceWei[currentBatch()];
    }

    // ------------------------------------------------------------------ mint
    /// @notice Mint one cat with a backend-signed voucher (proof the agent solved the puzzle).
    /// @dev Checks-Effects-Interactions: all state written before the USDT pull + _mint.
    function mintWithVoucher(uint256 nonce, uint256 deadline, bytes calldata sig) external payable {
        require(block.timestamp <= deadline, "voucher expired");
        require(!usedNonce[nonce], "nonce used");
        require(totalMinted < MAX_SUPPLY, "sold out");
        require(mintedBy[msg.sender] < MAX_PER_WALLET, "wallet cap");
        if (address(agentRegistry) != address(0)) {
            require(agentRegistry.isAgent(msg.sender), "not agent");
        }

        bytes32 structHash = keccak256(abi.encode(VOUCHER_TYPEHASH, msg.sender, nonce, deadline));
        address rec = ECDSA.recover(_hashTypedDataV4(structHash), sig);
        require(rec == trustedSigner, "bad voucher");

        // price is set by the batch this token falls into (batch 0 = free)
        uint256 priceNow = batchPriceWei[totalMinted / BATCH_SIZE >= NUM_BATCHES ? NUM_BATCHES - 1 : totalMinted / BATCH_SIZE];
        require(msg.value >= priceNow, "insufficient BNB");

        // effects
        usedNonce[nonce] = true;
        unchecked {
            mintedBy[msg.sender] += 1;
        }
        uint256 tokenId = ++totalMinted; // ids 1..10000
        _mint(msg.sender, tokenId); // plain _mint (no callback): never block a smart-contract agent wallet

        // interactions: forward price to treasury, refund any excess
        if (priceNow > 0) {
            (bool paid, ) = treasury.call{value: priceNow}("");
            require(paid, "pay failed");
        }
        if (msg.value > priceNow) {
            (bool refunded, ) = msg.sender.call{value: msg.value - priceNow}("");
            require(refunded, "refund failed");
        }

        emit Minted(msg.sender, tokenId, nonce, priceNow);
    }

    /// @notice Digest a voucher signer must sign (handy for the backend + tests).
    function voucherDigest(address wallet, uint256 nonce, uint256 deadline)
        external
        view
        returns (bytes32)
    {
        return _hashTypedDataV4(keccak256(abi.encode(VOUCHER_TYPEHASH, wallet, nonce, deadline)));
    }

    // ------------------------------------------------------------------ reveal
    function reveal(bytes32 seed, string calldata baseURI_) external onlyOwner {
        require(!revealed, "revealed");
        require(seed != bytes32(0), "zero seed");
        revealSeed = seed;
        _baseTokenURI = baseURI_;
        revealed = true;
        emit Revealed(seed, baseURI_);
    }

    function tokenURI(uint256 id) public view override returns (string memory) {
        _requireOwned(id);
        if (!revealed) return _unrevealedURI;
        return string(abi.encodePacked(_baseTokenURI, id.toString(), ".json"));
    }

    // ------------------------------------------------------------------ admin
    /// @notice Set one batch's price in BNB wei (e.g. re-peg to USD as BNB moves). batch 0..4.
    function setBatchPrice(uint256 batch, uint256 priceWei) external onlyOwner {
        require(batch < NUM_BATCHES, "bad batch");
        batchPriceWei[batch] = priceWei;
        emit BatchPriceUpdated(batch, priceWei);
    }

    /// @notice Set all five batch prices at once (BNB wei).
    function setAllBatchPrices(uint256[5] calldata prices) external onlyOwner {
        batchPriceWei = prices;
        for (uint256 i = 0; i < NUM_BATCHES; i++) emit BatchPriceUpdated(i, prices[i]);
    }

    function setTreasury(address t) external onlyOwner {
        require(t != address(0), "zero addr");
        treasury = t;
    }

    function setTrustedSigner(address s) external onlyOwner {
        require(s != address(0), "zero addr");
        trustedSigner = s;
        emit SignerUpdated(s);
    }

    function setAgentRegistry(address r) external onlyOwner {
        agentRegistry = IAgentRegistry(r);
    }

    function setUnrevealedURI(string calldata uri) external onlyOwner {
        _unrevealedURI = uri;
    }

    function setBaseURI(string calldata uri) external onlyOwner {
        _baseTokenURI = uri;
    }

    function setDefaultRoyalty(address receiver, uint96 feeNumerator) external onlyOwner {
        _setDefaultRoyalty(receiver, feeNumerator);
    }

    /// @notice Rescue any ERC20 accidentally sent to the contract (mint fees go straight to treasury).
    function rescueERC20(address token, address to, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(to, amount);
    }

    // ------------------------------------------------------------------ views
    function remaining() external view returns (uint256) {
        return MAX_SUPPLY - totalMinted;
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721, ERC2981)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
