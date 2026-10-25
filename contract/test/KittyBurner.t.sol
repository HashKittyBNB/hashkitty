// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Test} from "forge-std/Test.sol";
import {KittyToken} from "../src/KittyToken.sol";
import {KittyBurner} from "../src/KittyBurner.sol";
import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";

contract MockCats is ERC721 {
    constructor() ERC721("HashKitty", "HKITTY") {}
    function mint(address to, uint256 id) external { _mint(to, id); }
}

contract KittyBurnerTest is Test {
    KittyToken kitty;
    KittyBurner burner;
    MockCats nft;

    uint256 signerPk = 0xA11CE;
    address signer;
    address treasury = address(0xBEEF);
    address alice = address(0xA1);
    address constant DEAD = 0x000000000000000000000000000000000000dEaD;

    // Common, Rare, Epic, Legendary
    uint256[4] amts = [uint256(15_000 ether), 30_000 ether, 50_000 ether, 90_000 ether];

    function setUp() public {
        signer = vm.addr(signerPk);
        kitty = new KittyToken(treasury);        // 420M -> treasury
        nft = new MockCats();
        burner = new KittyBurner(address(nft), address(kitty), signer, amts);
        // fund burner with the 252M burn allocation
        vm.prank(treasury);
        kitty.transfer(address(burner), 252_000_000 ether);
    }

    function _sign(address w, uint256 id, uint8 tier, uint256 nonce, uint256 dl) internal view returns (bytes memory) {
        bytes32 d = burner.burnDigest(w, id, tier, nonce, dl);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(signerPk, d);
        return abi.encodePacked(r, s, v);
    }

    function test_SupplyMinted() public view {
        assertEq(kitty.totalSupply(), 420_000_000 ether);
        assertEq(kitty.balanceOf(address(burner)), 252_000_000 ether);
    }

    function test_BurnLegendaryPaysMost() public {
        nft.mint(alice, 7);
        vm.prank(alice);
        nft.approve(address(burner), 7);
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 7, 3, 1, dl); // tier 3 = Legendary
        vm.prank(alice);
        burner.burn(7, 3, 1, dl, sig);

        assertEq(nft.ownerOf(7), DEAD);                 // NFT burned
        assertEq(kitty.balanceOf(alice), 90_000 ether); // Legendary payout
        assertTrue(burner.burned(7));
    }

    function test_RarityAmounts() public {
        uint256[4] memory expect = [uint256(15_000 ether), 30_000 ether, 50_000 ether, 90_000 ether];
        for (uint8 tier = 0; tier < 4; tier++) {
            uint256 id = 100 + tier;
            nft.mint(alice, id);
            vm.prank(alice);
            nft.approve(address(burner), id);
            uint256 dl = block.timestamp + 1 hours;
            bytes memory sig = _sign(alice, id, tier, 10 + tier, dl);
            uint256 bal = kitty.balanceOf(alice);
            vm.prank(alice);
            burner.burn(id, tier, 10 + tier, dl, sig);
            assertEq(kitty.balanceOf(alice) - bal, expect[tier]);
        }
    }

    function test_DoubleBurnReverts() public {
        nft.mint(alice, 1);
        vm.prank(alice);
        nft.approve(address(burner), 1);
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(alice, 1, 0, 1, dl);
        vm.prank(alice);
        burner.burn(1, 0, 1, dl, sig);
        // token now at DEAD; a second attempt (new nonce) fails on ownerOf check anyway, but burned flag guards too
        bytes memory sig2 = _sign(alice, 1, 0, 2, dl);
        vm.prank(alice);
        vm.expectRevert("already burned");
        burner.burn(1, 0, 2, dl, sig2);
    }

    function test_BadSignerReverts() public {
        nft.mint(alice, 1);
        vm.prank(alice);
        nft.approve(address(burner), 1);
        uint256 dl = block.timestamp + 1 hours;
        bytes32 d = burner.burnDigest(alice, 1, 3, 1, dl);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBAD, d); // wrong key -> would pay Legendary if it passed
        bytes memory sig = abi.encodePacked(r, s, v);
        vm.prank(alice);
        vm.expectRevert("bad voucher");
        burner.burn(1, 3, 1, dl, sig);
    }

    function test_NotOwnerReverts() public {
        nft.mint(alice, 1);
        // bob tries to burn alice's token with a voucher signed for bob
        address bob = address(0xB0B);
        uint256 dl = block.timestamp + 1 hours;
        bytes memory sig = _sign(bob, 1, 0, 1, dl);
        vm.prank(bob);
        vm.expectRevert("not owner");
        burner.burn(1, 0, 1, dl, sig);
    }

    function test_NonceReuseReverts() public {
        nft.mint(alice, 1);
        nft.mint(alice, 2);
        vm.startPrank(alice);
        nft.approve(address(burner), 1);
        nft.approve(address(burner), 2);
        uint256 dl = block.timestamp + 1 hours;
        bytes memory s1 = _signP(alice, 1, 0, 5, dl);
        bytes memory s2 = _signP(alice, 2, 0, 5, dl);
        burner.burn(1, 0, 5, dl, s1);
        vm.expectRevert("nonce used");
        burner.burn(2, 0, 5, dl, s2);
        vm.stopPrank();
    }

    function test_TierTunable() public {
        burner.setTierAmount(0, 20_000 ether);
        assertEq(burner.tierAmount(0), 20_000 ether);
    }

    // helper usable inside startPrank (no vm.prank)
    function _signP(address w, uint256 id, uint8 tier, uint256 nonce, uint256 dl) internal view returns (bytes memory) {
        return _sign(w, id, tier, nonce, dl);
    }
}
