#!/usr/bin/env python3
"""
Build the on-chain / IPFS set from the generated gallery.
- Randomly permutes which cat each tokenId gets (so mint order != rarity: fair).
- Copies images renamed by tokenId, writes per-token metadata.
- Computes provenanceHash = keccak256( concat sha256(image_tokenId) for tokenId 1..N ).
The tokenId->rarity mapping is SECRET until reveal; provenanceHash commits to it publicly.

Run in two phases:
  phase 1 (before pin):   python3 -I build_onchain.py images
  -> builds build_onchain/images/<id>.png + provenance, prints nothing to pin yet
  phase 2 (after image CID): python3 -I build_onchain.py meta <IMAGES_CID>
  -> writes build_onchain/meta/<id>.json referencing ipfs://<IMAGES_CID>/<id>.png
"""
import os, sys, json, random, shutil, hashlib
from Crypto.Hash import keccak

SRC = "/root/hashkitty/web/gallery"
OUT = "/root/hashkitty/build_onchain"
SEED = 20261025  # permutation seed (recorded for transparency)

def keccak256(b):
    h = keccak.new(digest_bits=256); h.update(b); return h.digest()

def load_items():
    return json.load(open(os.path.join(SRC, "manifest.json")))["items"]

def phase_images():
    items = load_items()
    n = len(items)
    rng = random.Random(SEED)
    order = items[:]                 # source cats (id = rank)
    rng.shuffle(order)               # tokenId i+1 gets order[i]
    os.makedirs(os.path.join(OUT, "images"), exist_ok=True)
    mapping = []
    concat = b""
    for i, src in enumerate(order):
        tid = i + 1
        srcimg = os.path.join(SRC, src["image"])  # images/<rank>.png
        dst = os.path.join(OUT, "images", f"{tid}.png")
        shutil.copyfile(srcimg, dst)
        concat += hashlib.sha256(open(dst, "rb").read()).digest()
        mapping.append({"tokenId": tid, "src_id": src["id"], "rank": src["rank"],
                        "tier": src["tier"], "rarity_score": src["rarity_score"],
                        "attributes": src["attributes"]})
    prov = "0x" + keccak256(concat).hex()
    json.dump({"seed": SEED, "provenanceHash": prov, "count": n, "mapping": mapping},
              open(os.path.join(OUT, "provenance.json"), "w"))
    print("phase1 done. images:", n)
    print("PROVENANCE_HASH:", prov)

def phase_meta(images_cid):
    p = json.load(open(os.path.join(OUT, "provenance.json")))
    os.makedirs(os.path.join(OUT, "meta"), exist_ok=True)
    for m in p["mapping"]:
        tid = m["tokenId"]
        attrs = [{"trait_type": k, "value": v} for k, v in m["attributes"].items()]
        attrs.append({"trait_type": "Rarity Tier", "value": m["tier"]})
        attrs.append({"trait_type": "Rarity Rank", "value": m["rank"]})
        meta = {
            "name": f"HashKitty #{tid}",
            "description": "A HashKitty — 1 of 10,000 cats minted only by an agent that solved the hash. Agent-gated, BNB Chain, no token.",
            "image": f"ipfs://{images_cid}/{tid}.png",
            "attributes": attrs,
        }
        json.dump(meta, open(os.path.join(OUT, "meta", f"{tid}.json"), "w"))
    print("phase2 done. metadata written for", p["count"], "tokens, image CID", images_cid)

if __name__ == "__main__":
    if sys.argv[1] == "images":
        phase_images()
    elif sys.argv[1] == "meta":
        phase_meta(sys.argv[2])
