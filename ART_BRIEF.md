# HashKitty 🐱 — Pixel Art Brief (for artist)

Pixel-style cat NFTs, 10,000 supply. Art pending from MRX's artist. This doc = the spec so the
art drops straight into the metadata + reveal pipeline.

## Style
- **True pixel art.** Crisp pixels, `image-rendering: pixelated`. No anti-alias, no gradients inside sprites.
- **Canvas:** 32×32 or 48×48 native grid, exported upscaled to 1024×1024 PNG (nearest-neighbor).
- **Palette:** tight, cohesive (16–24 colors max across the whole collection). Pick one house palette so 10k cats feel like ONE collection, not noise.
- **Subject:** a cat "broker/agent" character — same framing/pose per cat, traits layered on. Think CryptoPunks grid discipline but cats.
- **Background:** flat 1-color per rarity band (keeps focus on the cat + signals rarity subtly).

## Rarity bands (visual weight — rolled at reveal, provably-fair)
| Rarity | Share | Visual cue |
|---|---|---|
| Common 🐱    | 60% (6000) | plain fur, simple bg |
| Rare 😺      | 25% (2500) | 1 standout trait (hat/glasses), richer bg |
| Epic 😾      | 12% (1200) | 2+ rare traits, animated-feel palette |
| Legendary 😼 | 3%  (300)  | unique 1/1-ish treatments, gold/glow accents |

## Trait layers (so art = composable, metadata = generatable)
Deliver as separate transparent PNG layers on the same grid, z-ordered:
1. `background` (flat color variants)
2. `body/fur` (base coat colors)
3. `eyes`
4. `mouth/expression`
5. `headwear` (none / cap / crown / headset …)
6. `eyewear` (none / shades / monocle …)
7. `accessory` (none / scarf / chain / coffee …)
8. `overlay` (legendary glow / effects)

Give each layer a **name + rarity weight**; we composite + assign per-tokenId from `keccak256(revealSeed, tokenId)`.

## Deliverables
1. Layer PNGs (named, with weights) OR 10k pre-composited PNGs if you'd rather composite yourself.
2. One **unrevealed placeholder** PNG (a "mystery box"/sleeping cat) — shown for ALL tokens pre-reveal.
3. A small **logo / wordmark** in the same pixel style (for the site + socials).

## Pipeline after art arrives (we handle)
1. Composite + assign traits per tokenId using the committed reveal seed.
2. Generate 10k metadata JSON (`name`, `image`, `attributes[]` incl. `Rarity`).
3. Pin images + metadata to IPFS (Pinata), set `baseURI` via `reveal()`.
4. Publish the provenance manifest so anyone can verify the shuffle was fair.

## Metadata shape (per token)
```json
{
  "name": "HashKitty #1234",
  "description": "An agent-minted pixel cat on BNB Chain. Minted by solving a puzzle.",
  "image": "ipfs://<CID>/1234.png",
  "attributes": [
    {"trait_type": "Rarity", "value": "Rare"},
    {"trait_type": "Background", "value": "Teal"},
    {"trait_type": "Fur", "value": "Calico"},
    {"trait_type": "Eyes", "value": "Sharp"},
    {"trait_type": "Headwear", "value": "Headset"},
    {"trait_type": "Eyewear", "value": "Shades"},
    {"trait_type": "Accessory", "value": "Coffee"}
  ]
}
```
