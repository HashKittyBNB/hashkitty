// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {HashKittyNFT} from "../src/HashKittyNFT.sol";

/// a "dumb" smart-contract wallet that does NOT implement onERC721Received.
/// Proves plain _mint does not block smart-contract agent wallets (safeMint would revert here).
contract DumbAgentWallet {
    function doMint(HashKittyNFT nft, uint256 nonce, uint256 deadline, bytes calldata sig) external payable {
        nft.mintWithVoucher{value: msg.value}(nonce, deadline, sig);
    }
    receive() external payable {}
}

contract HashKittyNFTTest is Test {
    HashKittyNFT nft;

    uint256 signerPk = 0xA11CE;
    address signer;
    address treasury = address(0xBEEF);
    address alice = address(0xA1);

    // [GENESIS free, CIPHER, LATTICE, FORGE, SINGULARITY]
    uint256[5] prices = [uint256(0), 0.25 ether, 0.5 ether, 0.75 ether, 1 ether];

    function setUp() public {
        signer = vm.addr(signerPk);
        nft = new HashKittyNFT(
            treasury, signer, keccak256("provenance"), "ipfs://unrevealed.json", prices
        );
        vm.deal(alice, 100 ether);
    }

    function _sign(address wallet, uint256 nonce, uint256 deadline) internal view returns (bytes memory) {
        bytes32 digest = nft.voucherDigest(wallet, nonce, deadline);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(signerPk, digest);
        return abi.encodePacked(r, s, v);
    }

    function test_HappyMintBatch0Free() public {
        bytes memory sig = _sign(alice, 1, block.timestamp + 1 hours);
        vm.prank(alice);
        nft.mintWithVoucher(1, block.timestamp + 1 hours, sig); // batch 0 => free, value 0
        assertEq(nft.ownerOf(1), alice);
        assertEq(nft.totalMinted(), 1);
        assertEq(treasury.balance, 0); // GENESIS batch is free
        assertEq(nft.currentBatch(), 0);
        assertEq(nft.currentPrice(), 0);
    }

    function test_BatchPricesStored() public view {
        assertEq(nft.batchPriceWei(0), 0);
        assertEq(nft.batchPriceWei(1), 0.25 ether);
        assertEq(nft.batchPriceWei(2), 0.5 ether);
        assertEq(nft.batchPriceWei(3), 0.75 ether);
        assertEq(nft.batchPriceWei(4), 1 ether);
    }

    function test_PaidMintForwardsAndRefunds() public {
        // make the current batch cost 0.3 BNB
        nft.setBatchPrice(0, 0.3 ether);
        assertEq(nft.currentPrice(), 0.3 ether);
        uint256 t0 = treasury.balance;
        uint256 a0 = alice.balance;
        bytes memory sig = _sign(alice, 1, block.timestamp + 1 hours);
        vm.prank(alice);
        nft.mintWithVoucher{value: 0.5 ether}(1, block.timestamp + 1 hours, sig); // overpay 0.2
        assertEq(nft.ownerOf(1), alice);
        assertEq(treasury.balance, t0 + 0.3 ether);        // price forwarded
        assertEq(alice.balance, a0 - 0.3 ether);           // only price spent, 0.2 refunded
    }

    function test_InsufficientBNBReverts() public {
        nft.setBatchPrice(0, 0.3 ether);
        bytes memory sig = _sign(alice, 1, block.timestamp + 1 hours);
        vm.prank(alice);
        vm.expectRevert("insufficient BNB");
        nft.mintWithVoucher{value: 0.1 ether}(1, block.timestamp + 1 hours, sig);
    }

    function test_SetAllBatchPrices() public {
        uint256[5] memory np = [uint256(0), 1 ether, 2 ether, 3 ether, 4 ether];
        nft.setAllBatchPrices(np);
        assertEq(nft.batchPriceWei(4), 4 ether);
    }

    function test_BadSignerReverts() public {
        uint256 dl = block.timestamp + 1 hours;
        bytes32 digest = nft.voucherDigest(alice, 1, dl);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, digest);
        bytes memory sig = abi.encodePacked(r, s, v);
        vm.prank(alice);
        vm.expectRevert("bad voucher");
        nft.mintWithVoucher(1, dl, sig);
    }

    function test_NonceReuseReverts() public {
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 7, dl);
        vm.prank(alice);
        nft.mintWithVoucher(7, dl, sig);
        vm.prank(alice);
        vm.expectRevert("nonce used");
        nft.mintWithVoucher(7, dl, sig);
    }

    function test_ExpiredReverts() public {
        uint256 dl = block.timestamp + 10;
        bytes memory sig = _sign(alice, 1, dl);
        vm.warp(dl + 1);
        vm.prank(alice);
        vm.expectRevert("voucher expired");
        nft.mintWithVoucher(1, dl, sig);
    }

    function test_VoucherBoundToWallet() public {
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 1, dl);
        vm.deal(address(0xB0B), 10 ether);
        vm.prank(address(0xB0B));
        vm.expectRevert("bad voucher");
        nft.mintWithVoucher(1, dl, sig);
    }

    function test_WalletCap() public {
        uint256 dl = block.timestamp + 1 hours;
        for (uint256 i = 1; i <= 10; i++) {
            bytes memory sig = _sign(alice, i, dl);
            vm.prank(alice);
            nft.mintWithVoucher(i, dl, sig); // batch 0 free
        }
        bytes memory sig11 = _sign(alice, 11, dl);
        vm.prank(alice);
        vm.expectRevert("wallet cap");
        nft.mintWithVoucher(11, dl, sig11);
    }

    function test_SmartContractWalletCanMint() public {
        DumbAgentWallet w = new DumbAgentWallet();
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(address(w), 1, dl);
        w.doMint(nft, 1, dl, sig); // batch 0 free, value 0
        assertEq(nft.ownerOf(1), address(w));
    }

    function test_RevealFlowAndTokenURI() public {
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 1, dl);
        vm.prank(alice);
        nft.mintWithVoucher(1, dl, sig);

        assertEq(nft.tokenURI(1), "ipfs://unrevealed.json");
        nft.reveal(keccak256("seed"), "ipfs://base/");
        assertEq(nft.tokenURI(1), "ipfs://base/1.json");
        assertEq(nft.revealSeed(), keccak256("seed"));

        vm.expectRevert("revealed");
        nft.reveal(keccak256("seed2"), "ipfs://x/");
    }

    function test_Royalty() public view {
        (address r, uint256 amt) = nft.royaltyInfo(1, 1e18);
        assertEq(r, treasury);
        assertEq(amt, 5e16); // 5%
    }

    function test_AgentRegistryGate() public {
        MockRegistry reg = new MockRegistry();
        nft.setAgentRegistry(address(reg));
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 1, dl);

        vm.prank(alice);
        vm.expectRevert("not agent");
        nft.mintWithVoucher(1, dl, sig);

        reg.setAgent(alice, true);
        vm.prank(alice);
        nft.mintWithVoucher(1, dl, sig);
        assertEq(nft.ownerOf(1), alice);
    }
}

contract MockRegistry {
    mapping(address => bool) public agents;
    function isAgent(address a) external view returns (bool) {
        return agents[a];
    }
    function setAgent(address a, bool v) external {
        agents[a] = v;
    }
}
