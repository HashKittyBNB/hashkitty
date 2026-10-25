// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/// @title KittyBurner — burn a HashKitty NFT, get $KITTY by rarity.
/// @notice The NFT has no rarity on-chain, so a backend signs an EIP-712 voucher certifying the
///         token's rarity tier (read from the committed provenance mapping). The burner verifies it,
///         sends the NFT to a dead address (permanent burn), and pays out tierAmount[tier] in $KITTY.
///         Rarity = payout: Common < Rare < Epic < Legendary.
contract KittyBurner is Ownable, EIP712 {
    using SafeERC20 for IERC20;

    address public constant DEAD = 0x000000000000000000000000000000000000dEaD;

    IERC721 public immutable nft;     // HashKitty NFT
    IERC20 public immutable kitty;    // $KITTY token
    address public trustedSigner;     // backend that certifies rarity tiers

    // tier 0..3 => Common, Rare, Epic, Legendary (18-decimals $KITTY), owner-tunable
    uint256[4] public tierAmount;

    mapping(uint256 => bool) public burned;   // tokenId => burned
    mapping(uint256 => bool) public usedNonce;

    bytes32 private constant BURN_TYPEHASH =
        keccak256("BurnVoucher(address wallet,uint256 tokenId,uint8 tier,uint256 nonce,uint256 deadline)");

    event Burned(address indexed burner, uint256 indexed tokenId, uint8 tier, uint256 amount);
    event TierAmountUpdated(uint8 tier, uint256 amount);
    event SignerUpdated(address signer);

    constructor(address _nft, address _kitty, address _signer, uint256[4] memory _tierAmount)
        EIP712("KittyBurner", "1") Ownable(msg.sender)
    {
        require(_nft != address(0) && _kitty != address(0) && _signer != address(0), "zero addr");
        nft = IERC721(_nft);
        kitty = IERC20(_kitty);
        trustedSigner = _signer;
        tierAmount = _tierAmount;
    }

    /// @notice Burn one HashKitty (must be approved to this contract) and receive $KITTY by rarity.
    function burn(uint256 tokenId, uint8 tier, uint256 nonce, uint256 deadline, bytes calldata sig) external {
        require(block.timestamp <= deadline, "voucher expired");
        require(tier < 4, "bad tier");
        require(!burned[tokenId], "already burned");
        require(!usedNonce[nonce], "nonce used");
        require(nft.ownerOf(tokenId) == msg.sender, "not owner");

        bytes32 structHash = keccak256(abi.encode(BURN_TYPEHASH, msg.sender, tokenId, tier, nonce, deadline));
        require(ECDSA.recover(_hashTypedDataV4(structHash), sig) == trustedSigner, "bad voucher");

        uint256 amount = tierAmount[tier];

        // effects
        burned[tokenId] = true;
        usedNonce[nonce] = true;

        // interactions: burn the NFT (send to dead), pay the token
        nft.transferFrom(msg.sender, DEAD, tokenId);
        kitty.safeTransfer(msg.sender, amount);

        emit Burned(msg.sender, tokenId, tier, amount);
    }

    /// @notice EIP-712 digest a signer must sign (handy for the backend + tests).
    function burnDigest(address wallet, uint256 tokenId, uint8 tier, uint256 nonce, uint256 deadline)
        external view returns (bytes32)
    {
        return _hashTypedDataV4(keccak256(abi.encode(BURN_TYPEHASH, wallet, tokenId, tier, nonce, deadline)));
    }

    // -------- admin --------
    function setTierAmount(uint8 tier, uint256 amount) external onlyOwner {
        require(tier < 4, "bad tier");
        tierAmount[tier] = amount;
        emit TierAmountUpdated(tier, amount);
    }

    function setTrustedSigner(address s) external onlyOwner {
        require(s != address(0), "zero addr");
        trustedSigner = s;
        emit SignerUpdated(s);
    }

    /// @notice Withdraw leftover $KITTY (e.g. unclaimed burn allocation) — owner only.
    function withdrawKitty(address to, uint256 amount) external onlyOwner {
        kitty.safeTransfer(to, amount);
    }
}
