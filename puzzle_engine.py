#!/usr/bin/env python3
"""
Nekomigos puzzle engine — agent-gated puzzle-to-mint.
Generates deterministic, verifiable puzzles across crypto / math / EVM-standards.
All prompts in ENGLISH. Many puzzles are WALLET-SEEDED so each agent's answer is
unique (leaked answers in the mempool are useless -> no copy / no front-run).

Ground truth is COMPUTED here (keccak etc.), so the bank can never ship a wrong answer.

Tiers -> rarity:   easy->Common  medium->Rare  hard->Epic  cryptic->Legendary
Answer kinds:
  'hex'      -> compare normalized hex (0x-prefixed, lowercase)
  'int'      -> compare integer value
  'property' -> no fixed answer; grader runs a verifier on the submission (PoW-style)
"""
import json, hashlib, random
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

from Crypto.Hash import keccak as _keccak


# ---------- primitives ----------
def keccak256(b: bytes) -> bytes:
    h = _keccak.new(digest_bits=256)
    h.update(b)
    return h.digest()

def selector(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]

def topic0(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()

def pad32_int(x: int) -> bytes:
    return x.to_bytes(32, "big")

def pad32_addr(addr: str) -> bytes:
    a = int(addr, 16)
    return a.to_bytes(32, "big")

def abi_encode(*items) -> bytes:
    """Minimal ABI-encode for static 32-byte words (int / address-as-str)."""
    out = b""
    for it in items:
        if isinstance(it, str) and it.startswith("0x"):
            out += pad32_addr(it)
        else:
            out += pad32_int(int(it))
    return out

def abi_encode_packed(*items) -> bytes:
    out = b""
    for it in items:
        if isinstance(it, str) and it.startswith("0x"):
            out += bytes.fromhex(it[2:])
        elif isinstance(it, int):
            out += it.to_bytes(32, "big")
        else:
            out += str(it).encode()
    return out

# secp256k1 constants
SECP_P = 2**256 - 2**32 - 977
SECP_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


# ---------- answer normalization / grading ----------
def norm_hex(s: str) -> str:
    s = s.strip().lower().replace(" ", "")
    if s.startswith("0x"):
        s = s[2:]
    return "0x" + s.lstrip("0") if s.lstrip("0") else "0x0"

def norm_hex_fixed(s: str) -> str:
    """keep full-width hex (for 32-byte hashes / addresses) just lowercased + 0x."""
    s = s.strip().lower().replace(" ", "")
    if not s.startswith("0x"):
        s = "0x" + s
    return s

def norm_int(s) -> int:
    s = str(s).strip().lower().replace(" ", "").replace(",", "")
    if s.startswith("0x"):
        return int(s, 16)
    return int(s)


@dataclass
class Puzzle:
    pid: str
    tier: str
    category: str
    prompt: str
    answer_kind: str                 # 'hex' | 'hex32' | 'int' | 'property'
    answer: Optional[str] = None     # ground truth (server-side only)
    verify: Optional[Callable] = field(default=None, repr=False)  # for 'property'
    meta: dict = field(default_factory=dict)

    def grade(self, submission: str) -> bool:
        try:
            if self.answer_kind == "int":
                return norm_int(submission) == norm_int(self.answer)
            if self.answer_kind == "hex":
                return norm_hex(submission) == norm_hex(self.answer)
            if self.answer_kind == "hex32":
                return norm_hex_fixed(submission) == norm_hex_fixed(self.answer)
            if self.answer_kind == "property":
                return bool(self.verify(submission))
        except Exception:
            return False
        return False

    def public(self) -> dict:
        d = {"pid": self.pid, "tier": self.tier, "category": self.category,
             "prompt": self.prompt, "answer_kind": self.answer_kind}
        if self.meta:
            d["meta"] = self.meta
        return d


# ---------- content pools ----------
FUNC_SIGS = [
    "transfer(address,uint256)", "transferFrom(address,address,uint256)",
    "approve(address,uint256)", "balanceOf(address)", "allowance(address,address)",
    "totalSupply()", "ownerOf(uint256)", "safeTransferFrom(address,address,uint256)",
    "setApprovalForAll(address,bool)", "isApprovedForAll(address,address)",
    "mint(address,uint256)", "burn(uint256)", "name()", "symbol()", "decimals()",
    "tokenURI(uint256)", "getApproved(uint256)", "supportsInterface(bytes4)",
    "permit(address,address,uint256,uint256,uint8,bytes32,bytes32)",
    "deposit(uint256,address)", "withdraw(uint256,address,address)",
]
EVENT_SIGS = [
    "Transfer(address,address,uint256)", "Approval(address,address,uint256)",
    "ApprovalForAll(address,address,bool)", "OwnershipTransferred(address,address)",
    "Deposit(address,uint256)", "Withdrawal(address,uint256)",
    "Transfer(address,address,uint256,uint256)",  # 1155 single-ish variant
]
# curated standards knowledge (English prompt -> number). All verified.
STANDARDS = [
    ("the standard interface for fungible tokens", 20, "easy"),
    ("the standard interface for non-fungible tokens (NFTs)", 721, "easy"),
    ("the multi-token standard (fungible + non-fungible in one contract)", 1155, "easy"),
    ("token-bound accounts (a smart-contract account for every NFT)", 6551, "easy"),
    ("the tokenized-vault standard", 4626, "medium"),
    ("the NFT royalty-info standard", 2981, "medium"),
    ("standard interface detection (supportsInterface)", 165, "easy"),
    ("the fee-market change that introduced the base fee", 1559, "easy"),
    ("the standard proxy storage slots (implementation / admin / beacon)", 1967, "medium"),
    ("typed structured data hashing and signing", 712, "medium"),
    ("the CREATE2 opcode", 1014, "medium"),
    ("the Diamond / multi-facet proxy standard", 2535, "hard"),
    ("simple replay attack protection (chainId in the signed tx)", 155, "medium"),
    ("the signed data standard (\\x19 prefix)", 191, "hard"),
    ("account abstraction using an alt mempool and EntryPoint", 4337, "medium"),
    ("standard signature validation for contracts (isValidSignature)", 1271, "hard"),
    ("the permit extension (approvals via signatures) for ERC-20", 2612, "hard"),
]


# ---------- generators ----------
def g_selector(rng, tier="easy"):
    sig = rng.choice(FUNC_SIGS)
    return Puzzle(
        pid="", tier=tier, category="selector",
        prompt=f"What is the 4-byte function selector of `{sig}`? (0x-prefixed)",
        answer_kind="hex32", answer=selector(sig), meta={"sig": sig})

def g_topic0(rng, tier="medium"):
    sig = rng.choice(EVENT_SIGS)
    return Puzzle(
        pid="", tier=tier, category="topic0",
        prompt=f"What is topic0 (the event signature hash) of `{sig}`? (full 32-byte hash, 0x-prefixed)",
        answer_kind="hex32", answer=topic0(sig), meta={"sig": sig})

def g_keccak_str(rng, tier="medium"):
    words = ["hashkitty", "meow", "agent", "catnip", "pixel", "bsc", "whisker", "paw"]
    s = rng.choice(words) + str(rng.randint(0, 9999))
    return Puzzle(
        pid="", tier=tier, category="keccak",
        prompt=f'Compute keccak256 of the ASCII string "{s}" (no quotes, no null terminator). Give the 32-byte hash, 0x-prefixed.',
        answer_kind="hex32", answer="0x" + keccak256(s.encode()).hex(), meta={"string": s})

def g_standard(rng, tier=None):
    pool = STANDARDS if tier is None else [s for s in STANDARDS if s[2] == tier]
    desc, num, t = rng.choice(pool)
    return Puzzle(
        pid="", tier=t, category="standard",
        prompt=f"Which ERC/EIP number defines {desc}? (answer with the number only)",
        answer_kind="int", answer=str(num))

def g_storage_slot(rng, tier="medium"):
    slot = rng.randint(0, 12)
    # address key
    key = "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))
    ans = "0x" + keccak256(abi_encode(key, slot)).hex()
    return Puzzle(
        pid="", tier=tier, category="storage_slot",
        prompt=(f"In Solidity, a `mapping(address => uint256)` lives at storage slot {slot}. "
                f"What is the storage slot of the value for key {key}? "
                f"(hint: keccak256(abi.encode(key, slot)); give the 32-byte slot, 0x-prefixed)"),
        answer_kind="hex32", answer=ans, meta={"slot": slot, "key": key})

def g_math_modinv(rng, tier="hard"):
    a = rng.randint(2, 10**6)
    inv = pow(a, -1, SECP_N)
    return Puzzle(
        pid="", tier=tier, category="math_modinv",
        prompt=(f"Let n be the secp256k1 curve order "
                f"(0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141). "
                f"Compute the modular inverse of {a} modulo n. (decimal or 0x-hex)"),
        answer_kind="int", answer=str(inv), meta={"a": a})

def g_math_modexp(rng, tier="hard"):
    base = rng.randint(2, 10**4); exp = rng.randint(2, 256)
    ans = pow(base, exp, SECP_P)
    return Puzzle(
        pid="", tier=tier, category="math_modexp",
        prompt=(f"Let p be the secp256k1 field prime (2^256 - 2^32 - 977). "
                f"Compute {base}^{exp} mod p. (decimal or 0x-hex)"),
        answer_kind="int", answer=str(ans), meta={"base": base, "exp": exp})

def g_const(rng, tier="hard"):
    choice = rng.choice(["p", "n", "eip1967impl"])
    if choice == "p":
        return Puzzle(pid="", tier="medium", category="const",
            prompt="Give the secp256k1 field prime p as a decimal or 0x-hex integer.",
            answer_kind="int", answer=str(SECP_P))
    if choice == "n":
        return Puzzle(pid="", tier="hard", category="const",
            prompt="Give the secp256k1 curve order n as a decimal or 0x-hex integer.",
            answer_kind="int", answer=str(SECP_N))
    slot = (int.from_bytes(keccak256(b"eip1967.proxy.implementation"), "big") - 1)
    return Puzzle(pid="", tier="hard", category="const",
        prompt="What is the EIP-1967 implementation slot? (keccak256('eip1967.proxy.implementation') - 1, 0x-prefixed)",
        answer_kind="hex32", answer="0x" + slot.to_bytes(32, "big").hex())

# ----- CRYPTIC / wallet-seeded (unique per wallet) -----
def g_wallet_bytes(rng, wallet, round_id, tier="cryptic"):
    x = keccak256(abi_encode_packed(wallet, round_id))
    ans = "0x" + x[12:32].hex()   # last 20 bytes as an address
    return Puzzle(
        pid="", tier=tier, category="wallet_bytes",
        prompt=(f"Let x = keccak256(abi.encodePacked(yourWallet, uint256({round_id}))), "
                f"where yourWallet = {wallet}. "
                f"Report bytes 12..31 of x as a 20-byte address, 0x-prefixed."),
        answer_kind="hex32", answer=ans, meta={"wallet": wallet, "round": round_id})

def g_wallet_chain(rng, wallet, round_id, tier="cryptic"):
    sig = rng.choice(FUNC_SIGS)
    sel = int(selector(sig), 16)                       # 4 bytes as int
    step = sel ^ 56                                     # xor chainId
    ans = "0x" + keccak256(abi_encode(step, int(wallet, 16), round_id)).hex()
    return Puzzle(
        pid="", tier=tier, category="wallet_chain",
        prompt=(f"Multi-step (yourWallet = {wallet}): "
                f"(1) s = 4-byte selector of `{sig}` as a uint. "
                f"(2) x = s XOR 56 (the BSC chainId). "
                f"(3) answer = keccak256(abi.encode(x, uint256(yourWallet), uint256({round_id}))). "
                f"Give the 32-byte result, 0x-prefixed."),
        answer_kind="hex32", answer=ans, meta={"wallet": wallet, "round": round_id, "sig": sig})

def g_wallet_pow(rng, wallet, round_id, difficulty=2, tier="cryptic"):
    """Property-verified: submission is a nonce n such that
    keccak256(abi.encode(yourWallet, n)) has >= `difficulty` leading zero bytes."""
    w = int(wallet, 16)
    def verify(sub):
        n = norm_int(sub)
        if n < 0:
            return False
        h = keccak256(abi_encode(w, n))
        return h[:difficulty] == b"\x00" * difficulty
    return Puzzle(
        pid="", tier=tier, category="wallet_pow",
        prompt=(f"Proof-of-work (yourWallet = {wallet}): find ANY nonce n (uint256) such that "
                f"keccak256(abi.encode(yourWallet, n)) begins with {difficulty} zero byte(s) "
                f"(i.e. the hash starts with {'00'*difficulty}...). Submit the nonce n."),
        answer_kind="property", verify=verify, meta={"wallet": wallet, "difficulty": difficulty})


# ---------- bank builder ----------
TIER_GENERATORS = {
    "easy":    [g_selector, g_standard, lambda r: g_const(r)],
    "medium":  [g_topic0, g_storage_slot, g_keccak_str, g_standard],
    "hard":    [g_math_modinv, g_math_modexp, g_const, g_standard],
    "cryptic": [],  # wallet-seeded, built separately per wallet
}

def make_pid(seed: str) -> str:
    return "NKM-" + hashlib.sha256(seed.encode()).hexdigest()[:10]

def build_bank(n_per_tier=15, seed=1337, sample_wallet=None):
    rng = random.Random(seed)
    bank = []
    for tier in ("easy", "medium", "hard"):
        gens = TIER_GENERATORS[tier]
        for i in range(n_per_tier):
            g = rng.choice(gens)
            p = g(rng) if g is not g_standard else g(rng, tier=(tier if any(s[2]==tier for s in STANDARDS) else None))
            p.tier = p.tier or tier
            p.pid = make_pid(f"{tier}-{i}-{p.prompt}")
            bank.append(p)
    # cryptic are wallet-seeded; show for a sample wallet
    w = sample_wallet or ("0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40)))
    for i in range(n_per_tier):
        pick = rng.choice([g_wallet_bytes, g_wallet_chain, g_wallet_pow])
        p = pick(rng, w, round_id=i) if pick is not g_wallet_pow else pick(rng, w, round_id=i, difficulty=2)
        p.pid = make_pid(f"cryptic-{i}-{w}-{p.prompt}")
        bank.append(p)
    return bank


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15, help="puzzles per tier")
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--wallet", default="0xB999194cad2E626689bc78206E13B47C6b623792")
    ap.add_argument("--json", action="store_true", help="dump public bank as JSON")
    ap.add_argument("--selftest", action="store_true", help="grade every puzzle with its own answer")
    args = ap.parse_args()

    bank = build_bank(args.n, args.seed, args.wallet)

    if args.selftest:
        ok = 0
        for p in bank:
            if p.answer_kind == "property":
                # brute a tiny nonce to prove the verifier works
                import itertools
                w = int(p.meta["wallet"], 16); d = p.meta["difficulty"]
                found = None
                for n in range(0, 5_000_000):
                    if keccak256(abi_encode(w, n))[:d] == b"\x00"*d:
                        found = n; break
                res = p.grade(str(found)) and not p.grade(str(found + 1) if found is not None else "x")
            else:
                res = p.grade(p.answer) and not p.grade("0xdeadbeef")
            ok += 1 if res else 0
            if not res:
                print("FAIL", p.pid, p.category, p.prompt[:60])
        print(f"self-test: {ok}/{len(bank)} puzzles grade correctly")
    elif args.json:
        print(json.dumps([p.public() for p in bank], indent=2))
    else:
        by = {}
        for p in bank:
            by.setdefault(p.tier, []).append(p)
        for tier in ("easy", "medium", "hard", "cryptic"):
            print(f"\n===== {tier.upper()} ({len(by.get(tier,[]))}) =====")
            for p in by.get(tier, []):
                print(f"\n[{p.pid}] ({p.category})")
                print("Q:", p.prompt)
                if p.answer_kind == "property":
                    print("A: <property-verified: any valid nonce>")
                else:
                    a = p.answer if len(p.answer) < 80 else p.answer[:76] + "..."
                    print("A:", a)
