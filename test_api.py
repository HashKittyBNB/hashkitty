#!/usr/bin/env python3
"""In-process smoke test for the HashKitty mint API (no network, no chain)."""
import os
os.environ.setdefault("HK_SIGNER_KEY", "/tmp/hk_test_signer.key")
os.environ.setdefault("HK_CONTRACT", "0x1111111111111111111111111111111111111111")

import api
from eth_account import Account
from eth_account.messages import encode_typed_data

c = api.app.test_client()
W1 = "0xB999194cad2E626689bc78206E13B47C6b623792"
W2 = "0x701a825417a540f67dbb901807af4276546d2df5"

def recover(voucher):
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
            ],
            "MintVoucher": [
                {"name": "wallet", "type": "address"}, {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "MintVoucher",
        "domain": {"name": "HashKitty", "version": "1", "chainId": voucher["chainId"],
                   "verifyingContract": voucher["contract"]},
        "message": {"wallet": voucher["wallet"], "nonce": int(voucher["nonce"]), "deadline": voucher["deadline"]},
    }
    return Account.recover_message(encode_typed_data(full_message=typed), signature=voucher["signature"])

# 1. health
h = c.get("/health").get_json()
assert h["ok"] and h["chainId"] == 56, h
print("health OK  signer=", h["signer"])

# 2. happy path: get puzzle, read server-side expected answer, solve
r = c.get("/puzzle?wallet=" + W1).get_json()
pid = r["pid"]
expected = api._puzzles[pid]["puzzle"].answer   # inspect server state (test only)
print("puzzle:", r["category"], "->", r["prompt"][:70])
s = c.post("/solve", json={"wallet": W1, "pid": pid, "answer": expected}).get_json()
assert s.get("correct") is True, s
v = s["voucher"]
rec = recover(v)
assert rec.lower() == h["signer"].lower(), (rec, h["signer"])
print("solve OK -> voucher nonce", v["nonce"][:12], "... sig recovers to signer ✅")

# 3. wrong answer (different wallet to dodge cooldown)
r2 = c.get("/puzzle?wallet=" + W2).get_json()
s2 = c.post("/solve", json={"wallet": W2, "pid": r2["pid"], "answer": "0xdeadbeef"}).get_json()
assert s2.get("correct") is False, s2
print("wrong answer rejected ✅  tries_left=", s2["tries_left"])

# 4. cooldown fires on rapid second attempt from same wallet
s3 = c.post("/solve", json={"wallet": W2, "pid": r2["pid"], "answer": "x"})
assert s3.status_code == 429, s3.get_json()
print("cooldown enforced ✅ (429)")

# 5. puzzle bound to wallet: W1's fresh puzzle can't be solved by W2
r4 = c.get("/puzzle?wallet=" + W1).get_json()
import time; time.sleep(api.COOLDOWN + 0.1)
s5 = c.post("/solve", json={"wallet": W2, "pid": r4["pid"], "answer": "x"})
assert s5.status_code == 403, (s5.status_code, s5.get_json())
print("puzzle wallet-binding enforced ✅ (403)")

print("\nAPI smoke test: ALL PASS")
