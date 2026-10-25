# HashKitty 🐱 — Contract Spec (v0)

Pure NFT (no token). Agent-gated puzzle-to-mint pixel cats on BNB Smart Chain.

## Params
- **MAX_SUPPLY** = 10,000 (immutable hard cap)
- **MINT_PRICE** = 0.5 U (USD-peg) → charge **0.5 USDT** flat on BSC (no oracle needed). [USDT BSC: 0x55d398326f99059fF775485246999027B3197955]
- **Royalty** = EIP-2981, 5% to treasury (this is the real revenue — primary cap is only ~$5k)
- **Gate** = agentic wallet only (Agent-ID), same mechanism as StockerPunk; plain EVM reverts
- **Max per wallet** = TBD (e.g. 10) to keep distribution wide

## Puzzles = EASY agent-gate (user directive: keep puzzles easy, high throughput)
Puzzles are intentionally EASY & fast so many agents can solve and mint — **target ≥20 mints per block**.
The puzzle is NOT the rarity selector; it is a quick proof that the caller is a capable agent doing work
(filters dumb bots / plain wallets, which can't compute a selector/keccak). One easy puzzle per mint.

## Rarity = RANDOM reveal (luck, provably-fair — NOT difficulty)
Since every puzzle is easy, rarity is rolled from the committed reveal seed, not from difficulty.
| Rarity | Target share of 10k |
|---|---|
| Common 🐱    | 60% (6000) |
| Rare 😺      | 25% (2500) |
| Epic 😾      | 12% (1200) |
| Legendary 😼 | 3%  (300)  |
Traits + rarity for tokenId = `keccak256(revealSeed, tokenId)` after reveal (no per-mint VRF gas → cheap → high throughput).

## Throughput (≥20 mint/block)
- No global mutex / no per-mint external call → mint is a cheap local SSTORE + _mint.
- Each wallet gets its own voucher + nonce; many wallets mint same block independently.
- Backend grades easy puzzles in <100ms; rate-limit is per-wallet (anti-brute) not global (no throughput cap).
- No VRF, no price oracle in the mint path (USDT flat) → nothing to serialize.

---

## Architecture: HYBRID verify

### Path B — Voucher mint (PRIMARY, ships first)
Covers ALL puzzle types (selector, keccak, standards, math, storage-slot, wallet-seeded). Reuses StockerPunk/Tally EIP-712 voucher-signer pattern.

```
agent → GET /puzzle?wallet=0x..&tier=hard      (backend serves wallet-seeded puzzle, stores expected)
agent → POST /solve {wallet, pid, answer}      (backend grades via puzzle_engine.grade)
   on correct → backend signs EIP-712 MintVoucher{wallet, tier, nonce, deadline}
agent → NFT.mintWithVoucher(tier, nonce, deadline, sig)   (contract verifies signer, mints, pulls 0.5 USDT)
```
Voucher struct (no tier — rarity is rolled at reveal, not chosen):
```solidity
struct MintVoucher { address wallet; uint256 nonce; uint256 deadline; }
// EIP-712 domain: name="HashKitty", version="1", chainId=56, verifyingContract
```
Contract checks: `ecrecover == trustedSigner`, `wallet == msg.sender`, `block.timestamp <= deadline`,
`!usedNonce[nonce]`, `totalMinted < MAX_SUPPLY`, then `USDT.transferFrom(msg.sender, treasury, 0.5e18)` and `_mint`.
Cheap path (no external calls except the USDT pull) → supports ≥20 mints/block.

### Path A — On-chain PoW (OPTIONAL, trustless flex, phase 2)
Fully trustless cryptic path, no signer. Agent submits a nonce; contract verifies on-chain:
```solidity
// difficulty = leading zero bytes required (scales with supply minted)
bytes32 h = keccak256(abi.encode(msg.sender, nonce));
require(uint256(h) >> (256 - 8*difficulty) == 0, "pow");
```
Marketed as "mine a Legendary the trustless way." Mutually exclusive per mint with Path B.

---

## Anti-cheat (critical)
- **Wallet-seeded puzzles** → every agent's answer unique; leaked answers in mempool are useless (no copy / no front-run).
- **Nonce + deadline** on voucher → one-time, expiring.
- **Cooldown + attempt rate-limit** per wallet in backend → no brute-force of knowledge puzzles.
- **Difficulty rises per batch minted** → "last cats = hardest puzzle."
- Signer key held like StockerPunk deploy key (chmod 600, never logged).

## Trait reveal (provably-fair, no VRF)
1. Deploy with `provenanceHash = keccak256(all_trait_metadata_in_order)` committed.
2. After sellout (or timer), owner sets a single `revealSeed`.
3. Each tokenId's traits = shuffle(index) derived from `keccak256(revealSeed, tokenId)`. Anyone can verify the shuffle against provenance. No per-mint VRF gas.

## Contracts
- `HashKittyNFT.sol` — ERC-721 + ERC-2981 + voucher mint + tier caps + reveal. (optional ERC-6551 TBA later if cats get utility)
- Reuse OpenZeppelin ERC721, EIP712, ECDSA.

## Revenue (honest)
- Primary: 10k × $0.5 = **$5k max** (low — puzzle-gating caps bot-farming, that's the point).
- **Upside = 5% secondary royalty** on every resale + strong floor from scarcity/merit narrative. Real money is secondary, not mint.

## Build order
1. `HashKittyNFT.sol` (voucher path) + Foundry tests
2. Backend API wrapping `puzzle_engine.py` (request/solve/sign-voucher) + rate-limit
3. Pixel art (4 tiers + trait layers) + metadata + IPFS
4. Website (agent-gated mint UI showing puzzle) + launch
5. (phase 2) on-chain PoW path, ERC-6551 utility
