#!/usr/bin/env python3
"""
HashKitty collection generator + rarity scoring.
Layer order (bottom -> top): Background(1) -> Base(2) -> Clothes -> Neck -> Mouth -> Eyes -> Accesories
Background is ALWAYS layer #1 (artist Background.png, BNB gold).

Run: python3 -I generate.py --n 100 --art /root/hashkitty/art_src/HasKitty --out /root/hashkitty/web/gallery
Outputs: <out>/images/<id>.png (600px), <out>/meta/<id>.json, <out>/manifest.json
         manifest items carry rarity_score + rank + tier (computed from actual trait frequencies).
"""
import os, json, argparse, random, hashlib
from PIL import Image

CANVAS = 1500
THUMB = 600
BG_FILE = "Background.png"
BG_NAME = "BNB Gold"

# broken artist files to skip until re-exported (eyes misplaced below the eye band)
EXCLUDE = {"Opened.png"}

# layer composite order after Background+Base
LAYER_ORDER = ["Clothes", "Neck", "Mouth", "Eyes", "Accesories"]

# weights per trait (smaller = rarer). "none" = chance the (optional) trait is absent.
# Eyes & Mouth have no "none" (always present). default weight for any unlisted file = 8.
WEIGHTS = {
    "Eyes": {  # always present
        "Eyes.png": 20, "Closed.png": 16, "Happy.png": 14, "Mad.png": 14,
        "Cute.png": 10, "Wide open.png": 10, "Wink.png": 8, "Weird.png": 6,
        "Glasses.png": 5, "Sunglasses.png": 4,
    },
    "Mouth": {  # always present
        "Mouth.png": 20, "Smiling.png": 18, "Sad.png": 14, "Tired.png": 14,
        "Confused.png": 8, "Tongue.png": 5,
    },
    "Neck": {
        "none": 60,
        "Scarf.png": 14, "Dotted.png": 12, "Winter scarf.png": 12, "Double scarf.png": 10,
        "Gang scarf.png": 10, "Triangle necklace.png": 10, "White hoodie2.png": 8,
        "Goldchain.png": 5, "Diamond.png": 4, "2chainz.png": 4, "Zephyr.png": 3,
    },
    "Clothes": {
        "none": 95,  # naked = LOW rarity (common)
        "Black tshirt.png": 14, "White tshirt.png": 14, "Form polo.png": 10, "Jumper.png": 10,
        "Flannel pattern.png": 8, "Waist bag tshirt.png": 8,
        # premium / HIGH rarity:
        "White hoodie.png": 3, "Black hoodie.png": 3,
        "White tuxedo.png": 3, "Black tuxedo.png": 3, "Sire coat.png": 3, "Monkey.png": 3,
    },
    "Accesories": {
        "none": 55,
        "Cap.png": 16, "Headphone.png": 14, "One side headphone.png": 10, "Wireless.png": 8,
        # HIGH rarity accessories:
        "Black headphone.png": 4, "Devil horn.png": 4, "Halo.png": 3, "Wide halo.png": 2,
    },
}
DEFAULT_W = 8

# rarity tiers by rank percentile (rarest first). label -> top fraction.
TIERS = [("Legendary", 0.03), ("Epic", 0.15), ("Rare", 0.40), ("Common", 1.0)]

def w_of(folder, fname):
    return WEIGHTS.get(folder, {}).get(fname, DEFAULT_W)

def weighted(items):
    tot = sum(w for _, w in items); r = random.uniform(0, tot); up = 0
    for it, w in items:
        up += w
        if r <= up:
            return it
    return items[-1][0]

def trait_name(f):
    return os.path.splitext(f)[0]

def opts_for(art, folder):
    out = []
    for f in sorted(os.listdir(os.path.join(art, folder))):
        if f.lower().endswith(".png") and f not in EXCLUDE:
            out.append((f, w_of(folder, f)))
    none_w = WEIGHTS.get(folder, {}).get("none", 0)
    if none_w > 0:
        out.append(("__none__", none_w))
    return out

def build(art, n, out):
    random.seed(1337)
    imdir = os.path.join(out, "images"); os.makedirs(imdir, exist_ok=True)
    mdir = os.path.join(out, "meta"); os.makedirs(mdir, exist_ok=True)

    bg = Image.open(os.path.join(art, BG_FILE)).convert("RGBA")
    base = Image.open(os.path.join(art, "Base", "Base.png")).convert("RGBA")
    cache = {}
    def layer(folder, fname):
        k = (folder, fname)
        if k not in cache:
            cache[k] = Image.open(os.path.join(art, folder, fname)).convert("RGBA")
        return cache[k]

    seen = set(); tokens = []; attempts = 0
    while len(tokens) < n and attempts < n * 300:
        attempts += 1
        attrs = {"Background": BG_NAME}
        picks = {}
        for folder in LAYER_ORDER:
            choice = weighted(opts_for(art, folder))
            if choice == "__none__":
                attrs[folder] = "None"
            else:
                picks[folder] = choice
                attrs[folder] = trait_name(choice)
        sig = json.dumps(attrs, sort_keys=True)
        h = hashlib.sha256(sig.encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        tokens.append({"attrs": attrs, "picks": picks})

    # ---- rarity scoring from ACTUAL frequencies ----
    from collections import Counter
    dims = ["Clothes", "Neck", "Mouth", "Eyes", "Accesories"]
    freq = {d: Counter(t["attrs"][d] for t in tokens) for d in dims}
    N = len(tokens)
    for t in tokens:
        score = 0.0
        breakdown = {}
        for d in dims:
            v = t["attrs"][d]
            p = freq[d][v] / N
            s = 1.0 / p  # rarer value -> higher score
            breakdown[d] = round(s, 2)
            score += s
        t["score"] = round(score, 2)
        t["breakdown"] = breakdown

    # rank: highest score = rarest = rank 1
    tokens.sort(key=lambda t: t["score"], reverse=True)
    for i, t in enumerate(tokens):
        t["rank"] = i + 1
        pct = (i + 1) / N
        t["tier"] = next(label for label, frac in TIERS if pct <= frac)

    # assign ids by rank (so #1 = rarest) — comment out if you want random ids
    manifest = []
    for t in tokens:
        tid = t["rank"]
        img = bg.copy()
        img = Image.alpha_composite(img, base)
        for folder in LAYER_ORDER:
            if folder in t["picks"]:
                img = Image.alpha_composite(img, layer(folder, t["picks"][folder]))
        img.convert("RGB").resize((THUMB, THUMB), Image.NEAREST).save(os.path.join(imdir, f"{tid}.png"))
        meta = {
            "name": f"HashKitty #{tid}",
            "description": "A HashKitty — minted only by an agent that solved the hash. BNB Chain. No token.",
            "image": f"images/{tid}.png",
            "rarity_score": t["score"], "rank": t["rank"], "tier": t["tier"],
            "attributes": [{"trait_type": k, "value": v} for k, v in t["attrs"].items()],
        }
        json.dump(meta, open(os.path.join(mdir, f"{tid}.json"), "w"), indent=2)
        manifest.append({"id": tid, "image": meta["image"], "rarity_score": t["score"],
                         "rank": t["rank"], "tier": t["tier"], "attributes": t["attrs"]})

    manifest.sort(key=lambda m: m["id"])
    json.dump({"count": len(manifest), "items": manifest}, open(os.path.join(out, "manifest.json"), "w"))
    print(f"generated {len(tokens)} unique HashKitties -> {out}  ({attempts} attempts)")
    for d in dims:
        c = freq[d]
        print(f"  {d:11s} {len(c)} variants | " + ", ".join(f"{k}:{v}" for k, v in c.most_common(5)))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--art", default="/root/hashkitty/art_src/HasKitty")
    ap.add_argument("--out", default="/root/hashkitty/web/gallery")
    a = ap.parse_args()
    build(a.art, a.n, a.out)
