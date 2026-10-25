// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Burnable} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title $KITTY — the HashKitty token.
/// @notice Fixed supply 420,000,000. Earned by BURNING a HashKitty NFT (rarity = amount),
///         the rest seeds liquidity + treasury. No mint after deploy.
contract KittyToken is ERC20, ERC20Burnable, Ownable {
    uint256 public constant MAX_SUPPLY = 420_000_000 ether; // 420M, 18 decimals

    constructor(address treasury) ERC20("HashKitty", "KITTY") Ownable(msg.sender) {
        require(treasury != address(0), "zero addr");
        _mint(treasury, MAX_SUPPLY); // all minted once, to the distributor/treasury wallet
    }
}
