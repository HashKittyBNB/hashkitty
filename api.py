#!/usr/bin/env python3
"""
HashKitty mint API — agent-gated puzzle-to-mint.
Flow:  GET /puzzle?wallet=0x..   -> serve an EASY, wallet-seeded puzzle (anti-copy)
       POST /solve {wallet,pid,answer} -> grade; if correct, return a signed EIP-712 MintVoucher
Puzzles are intentionally EASY & fast (target >=20 mints/block). The puzzle only proves the
caller is a capable AGENT doing a little work; rarity is rolled later at on-chain reveal.

Security: wallet-seeded puzzles (leaked answers useless), one-time nonce + deadline in the voucher,
per-wallet cooldown + attempt cap (anti-brute). Signer key is read from a chmod-600 file and NEVER logged.

NOT on-chain: nothing here broadcasts. It only SIGNS vouchers. Deploy the contract later, set
HK_CONTRACT + HK_CHAIN_ID, and point the frontend here.
"""
import os, time, json, secrets, random, threading
from flask import Flask, request, jsonify
from eth_account import Account
from eth_account.messages import encode_typed_data

import puzzle_engine as pe

# ---------------- config ----------------
CONTRACT = os.environ.get("HK_CONTRACT", "0x0000000000000000000000000000000000000000")
BURNER = os.environ.get("HK_BURNER", "")        # KittyBurner contract (set after deploy)
KITTY = os.environ.get("HK_KITTY", "")          # $KITTY token (set after deploy)
CHAIN_ID = int(os.environ.get("HK_CHAIN_ID", "56"))              # BSC mainnet

# rarity tiers -> burn payout ($KITTY, 18 decimals). gap 6x (Common..Legendary)
TIER_INDEX = {"Common": 0, "Rare": 1, "Epic": 2, "Legendary": 3}
TIER_KITTY = [15000, 30000, 50000, 90000]
_RARITY = {}  # tokenId -> tier string, from the committed provenance mapping
try:
    _pv = json.load(open(os.path.join(os.path.dirname(__file__), "build_onchain", "provenance.json")))
    _RARITY = {int(m["tokenId"]): m["tier"] for m in _pv["mapping"]}
except Exception:
    _RARITY = {}
SIGNER_KEYFILE = os.environ.get("HK_SIGNER_KEY", "/root/hashkitty/.signer_key")
PUZZLE_TTL = 180          # seconds a served puzzle stays valid
VOUCHER_TTL = 600         # seconds the signed voucher stays valid on-chain
COOLDOWN = 3              # seconds between a wallet's solve attempts (anti-brute, still allows bursts across wallets)
MAX_ATTEMPTS = 8         # wrong attempts per puzzle before it's burned

app = Flask(__name__)
_lock = threading.Lock()
_puzzles = {}   # pid -> {"puzzle": Puzzle, "wallet": w, "exp": t, "tries": n}
_last_try = {}  # wallet -> ts
from collections import deque
_activity = deque(maxlen=200)   # recent mints: {tokenId, wallet, batch, ts}

def record_mint(token_id, wallet, batch):
    """Called by the on-chain Minted-event watcher (added at deploy). Buffers a row for /activity."""
    with _lock:
        _activity.append({"tokenId": int(token_id), "wallet": wallet,
                          "batch": int(batch), "ts": int(time.time())})

# -------- BATCH / difficulty curve --------
# 5 batches of 2,000 each (10,000 total). Difficulty ramps every batch:
# early batches are EASY (mass-mint, >=20/block); later batches get harder (prestige, slower).
BATCH_SIZE = 2000
NUM_BATCHES = 5

def _chainid_puzzle():
    return pe.Puzzle(pid="", tier="easy", category="chainid",
                     prompt="What is the mainnet chainId of BNB Smart Chain? (number only)",
                     answer_kind="int", answer=str(CHAIN_ID))

# builder per level: (rng, wallet, round_id) -> Puzzle
BATCHES = [
    {"level": 1, "name": "GENESIS",   "difficulty": "easy",
     "builders": [lambda r,w,i: pe.g_selector(r, tier="easy"),
                  lambda r,w,i: pe.g_standard(r, tier="easy"),
                  lambda r,w,i: _chainid_puzzle(),
                  lambda r,w,i: pe.g_keccak_str(r, tier="easy"),
                  lambda r,w,i: pe.g_wallet_bytes(r, w, i, tier="easy")]},
    {"level": 2, "name": "CIPHER",    "difficulty": "medium",
     "builders": [lambda r,w,i: pe.g_topic0(r, tier="medium"),
                  lambda r,w,i: pe.g_storage_slot(r, tier="medium"),
                  lambda r,w,i: pe.g_keccak_str(r, tier="medium"),
                  lambda r,w,i: pe.g_standard(r, tier="medium")]},
    {"level": 3, "name": "LATTICE",   "difficulty": "hard",
     "builders": [lambda r,w,i: pe.g_math_modinv(r, tier="hard"),
                  lambda r,w,i: pe.g_const(r),
                  lambda r,w,i: pe.g_standard(r, tier="hard"),
                  lambda r,w,i: pe.g_wallet_chain(r, w, i, tier="hard")]},
    {"level": 4, "name": "FORGE",     "difficulty": "harder",
     "builders": [lambda r,w,i: pe.g_math_modexp(r, tier="hard"),
                  lambda r,w,i: pe.g_wallet_chain(r, w, i, tier="hard"),
                  lambda r,w,i: pe.g_wallet_pow(r, w, i, difficulty=2)]},
    {"level": 5, "name": "SINGULARITY","difficulty": "hardest",
     "builders": [lambda r,w,i: pe.g_math_modexp(r, tier="hard"),
                  lambda r,w,i: pe.g_wallet_pow(r, w, i, difficulty=3),
                  lambda r,w,i: pe.g_wallet_chain(r, w, i, tier="hard")]},
]

import urllib.request, random as _rnd
RPCS = ([os.environ["HK_RPC"]] if os.environ.get("HK_RPC") else []) + [
    "https://rpc-bsc.48.club",
    "https://1rpc.io/bnb",
    "https://bsc.blockrazor.xyz",
    "https://bsc-mainnet.public.blastapi.io",
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed1.defibit.io",
]
_SEL_TOTAL = "0x" + pe.keccak256(b"totalMinted()")[:4].hex()
_TOPIC_MINTED = "0x" + pe.keccak256(b"Minted(address,uint256,uint256,uint256)").hex()
_chain = {"minted": (0.0, None), "activity": (0.0, [])}

def _contract_live():
    return CONTRACT and CONTRACT.startswith("0x") and len(CONTRACT) == 42 and int(CONTRACT, 16) != 0

def _rpc(method, params):
    """Try RPC mirrors until one answers (spread load + survive per-IP blocks/rate-limits)."""
    last = None
    urls = RPCS[:1] + _rnd.sample(RPCS[1:], len(RPCS) - 1) if len(RPCS) > 1 else RPCS
    for url in urls:
        try:
            req = urllib.request.Request(url,
                data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (HashKitty)"})
            j = json.load(urllib.request.urlopen(req, timeout=6))
            if "result" in j:
                return j["result"]
            last = j.get("error")
        except Exception as e:
            last = e
            continue
    raise RuntimeError(f"all RPCs failed: {last}")

_SEL_OWNEROF = "0x" + pe.keccak256(b"ownerOf(uint256)").hex()[:8]
_SEL_BALANCEOF = "0x" + pe.keccak256(b"balanceOf(address)").hex()[:8]
_DEAD = "0x000000000000000000000000000000000000dEaD"
BURN_POOL_TOTAL = 252_000_000
MAX_SCAN = 500  # first-run ownerOf scan cap for the market list (then incremental); keeps warm-up light

_mints_index = {}       # tokenId -> owner (persistent, maintained by the background refresher)
_last_scanned = 0
_refresher_started = False

def _owner_of(tid):
    try:
        data = _SEL_OWNEROF + hex(tid)[2:].rjust(64, "0")
        r = _rpc("eth_call", [{"to": CONTRACT, "data": data}, "latest"])
        return "0x" + r[-40:]
    except Exception:
        return None

def _refresh_chain():
    """Background only: pull every on-chain read into caches so NO request handler ever does RPC
    (prevents the worker thread-pool from stalling on slow/rate-limited nodes)."""
    global _last_scanned
    if not _contract_live():
        return
    now = time.time()
    # totalMinted
    try:
        n = int(_rpc("eth_call", [{"to": CONTRACT, "data": _SEL_TOTAL}, "latest"]), 16)
        _chain["minted"] = (now, n)
    except Exception:
        n = _chain["minted"][1] or 0
    n = n or 0
    # burn stats (3 calls)
    try:
        burned = int(_rpc("eth_call", [{"to": CONTRACT,
                      "data": _SEL_BALANCEOF + _DEAD[2:].rjust(64, "0")}, "latest"]), 16)
        remaining = None
        if KITTY and len(KITTY) == 42 and BURNER and len(BURNER) == 42:
            r = int(_rpc("eth_call", [{"to": KITTY,
                      "data": _SEL_BALANCEOF + BURNER[2:].rjust(64, "0")}, "latest"]), 16)
            remaining = r // (10 ** 18)
        dist = (BURN_POOL_TOTAL - remaining) if remaining is not None else 0
        _chain["burnstats"] = (now, {"burned": burned, "kitty_distributed": dist,
                                     "pool_remaining": remaining if remaining is not None else BURN_POOL_TOTAL,
                                     "pool_total": BURN_POOL_TOTAL})
    except Exception:
        pass
    if n <= 0:
        return
    # activity FIRST (cheap: last 10 owners) so the feed populates fast, decoupled from the big scan
    try:
        act = []
        for tid in range(max(1, n - 9), n + 1):
            ow = _mints_index.get(tid) or _owner_of(tid)
            if ow:
                _mints_index[tid] = ow
                act.append({"tokenId": tid, "owner": ow, "wallet": ow,
                            "batch": _batch_for(tid - 1)["level"], "ts": int(now)})
        if act:
            _chain["activity"] = (now, act)
    except Exception:
        pass
    # market index: first run scans the last MAX_SCAN; after that only NEW tokens + re-scan last 50
    try:
        lo = max(1, n - MAX_SCAN + 1) if _last_scanned == 0 else _last_scanned + 1
        to_scan = set(range(lo, n + 1)) | set(range(max(1, n - 50), n + 1))
        for tid in sorted(to_scan):
            ow = _owner_of(tid)
            if ow:
                _mints_index[tid] = ow
        _last_scanned = max(_last_scanned, n)
        rows = [{"tokenId": t, "owner": o, "wallet": o, "batch": _batch_for(t - 1)["level"], "ts": int(now)}
                for t, o in sorted(_mints_index.items())]
        _chain["allmints"] = (now, rows)
    except Exception:
        pass

def _refresher_loop():
    while True:
        try:
            _refresh_chain()
        except Exception:
            pass
        time.sleep(30)

def _start_refresher():
    """Start the background cache-filler. NON-blocking: the first scan runs inside the thread,
    so the web worker boots and serves immediately (cache fills within a cycle)."""
    global _refresher_started
    if _refresher_started or not _contract_live():
        return
    _refresher_started = True
    threading.Thread(target=_refresher_loop, daemon=True).start()

# ---- request-facing readers: cache ONLY, never RPC ----
def _onchain_minted():
    return _chain["minted"][1]

def _onchain_all_mints():
    return _chain.get("allmints", (0.0, []))[1]

def _onchain_activity():
    return _chain.get("activity", (0.0, []))[1]

def _minted():
    """Live on-chain totalMinted() when the contract is set; else env fallback for dev."""
    m = _onchain_minted()
    if m is not None:
        return max(0, min(BATCH_SIZE * NUM_BATCHES, m))
    try:
        return max(0, min(BATCH_SIZE * NUM_BATCHES, int(os.environ.get("HK_MINTED", "0"))))
    except Exception:
        return 0

def _batch_for(minted):
    idx = min(NUM_BATCHES - 1, minted // BATCH_SIZE)
    return BATCHES[idx]

def _new_puzzle(rng, wallet, round_id):
    b = _batch_for(_minted())
    return rng.choice(b["builders"])(rng, wallet, round_id)


def _load_signer():
    if not os.path.exists(SIGNER_KEYFILE):
        acct = Account.create()
        old = os.umask(0o077)
        try:
            with open(SIGNER_KEYFILE, "w") as f:
                f.write(acct.key.hex())
        finally:
            os.umask(old)
        os.chmod(SIGNER_KEYFILE, 0o600)
        return acct
    with open(SIGNER_KEYFILE) as f:
        return Account.from_key(f.read().strip())


SIGNER = _load_signer()


def _sign_voucher(wallet, nonce, deadline):
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "MintVoucher": [
                {"name": "wallet", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "MintVoucher",
        "domain": {"name": "HashKitty", "version": "1", "chainId": CHAIN_ID, "verifyingContract": CONTRACT},
        "message": {"wallet": wallet, "nonce": nonce, "deadline": deadline},
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), SIGNER.key)
    return signed.signature.hex()


def _valid_addr(a):
    return isinstance(a, str) and a.startswith("0x") and len(a) == 42


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/solve", methods=["OPTIONS"])
def solve_preflight():
    return ("", 204)


@app.get("/health")
def health():
    return jsonify(ok=True, contract=CONTRACT, chainId=CHAIN_ID, signer=SIGNER.address,
                   burner=BURNER, kitty=KITTY)


BATCH_PRICE_USD = [0.0, 0.25, 0.50, 0.75, 1.00]  # paid in BNB (native), free-first batch

@app.get("/stats")
def stats():
    m = _minted()
    b = _batch_for(m)
    total = BATCH_SIZE * NUM_BATCHES
    return jsonify(
        minted=m, total=total,
        batch=b["level"], batch_name=b["name"], difficulty=b["difficulty"],
        price_usd=BATCH_PRICE_USD[b["level"] - 1],
        batch_size=BATCH_SIZE, num_batches=NUM_BATCHES,
        in_batch=m % BATCH_SIZE if m < total else BATCH_SIZE,
        batches=[{"level": x["level"], "name": x["name"], "difficulty": x["difficulty"],
                  "size": BATCH_SIZE, "price_usd": BATCH_PRICE_USD[x["level"] - 1],
                  "range": [x["level"] * BATCH_SIZE - BATCH_SIZE + 1, x["level"] * BATCH_SIZE]}
                 for x in BATCHES],
    )


def _sign_burn_voucher(wallet, token_id, tier, nonce, deadline):
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "BurnVoucher": [
                {"name": "wallet", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "tier", "type": "uint8"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "BurnVoucher",
        "domain": {"name": "KittyBurner", "version": "1", "chainId": CHAIN_ID, "verifyingContract": BURNER},
        "message": {"wallet": wallet, "tokenId": token_id, "tier": tier, "nonce": nonce, "deadline": deadline},
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), SIGNER.key)
    return signed.signature.hex()


@app.get("/owned")
def owned():
    """HashKitties owned by a wallet, with rarity + $KITTY burn reward (preview)."""
    wallet = (request.args.get("wallet") or "").strip()
    if not _valid_addr(wallet):
        return jsonify(error="bad wallet"), 400
    rows = [r for r in _onchain_all_mints() if r["owner"].lower() == wallet.lower()]
    cats = []
    for r in rows:
        tier = _RARITY.get(r["tokenId"], "Common")
        cats.append({"tokenId": r["tokenId"], "tier": tier,
                     "reward": TIER_KITTY[TIER_INDEX.get(tier, 0)]})
    cats.sort(key=lambda c: -c["reward"])
    return jsonify(wallet=wallet, cats=cats, count=len(cats),
                   burner=BURNER, kitty=KITTY, tier_kitty=TIER_KITTY,
                   imagesCID="bafybeiacflrmasxrmrw3fhwp3u2qxs3cltmbhwifribimpd7hxa6m35q7y")


@app.get("/burn-stats")
def burn_stats():
    """How many HashKitties burned + how much $KITTY distributed (served from the background cache)."""
    val = _chain.get("burnstats", (0.0, None))[1]
    return jsonify(val or {"burned": 0, "kitty_distributed": 0,
                           "pool_remaining": BURN_POOL_TOTAL, "pool_total": BURN_POOL_TOTAL})


@app.post("/burn-voucher")
def burn_voucher():
    """Sign an EIP-712 BurnVoucher certifying a token's rarity tier, so the burner pays the right $KITTY."""
    data = request.get_json(force=True, silent=True) or {}
    wallet = (data.get("wallet") or "").strip()
    try:
        token_id = int(data.get("tokenId"))
    except Exception:
        return jsonify(error="bad tokenId"), 400
    if not _valid_addr(wallet):
        return jsonify(error="bad wallet"), 400
    if not (BURNER and BURNER.startswith("0x") and len(BURNER) == 42):
        return jsonify(error="burn not live yet"), 503
    # ownership from the background cache (the KittyBurner also enforces ownerOf on-chain)
    ow = _mints_index.get(token_id)
    if not ow or ow.lower() != wallet.lower():
        return jsonify(error="you don't own this token"), 403
    tier_name = _RARITY.get(token_id, "Common")
    tier = TIER_INDEX.get(tier_name, 0)
    nonce = secrets.randbits(256)
    deadline = int(time.time()) + VOUCHER_TTL
    sig = _sign_burn_voucher(wallet, token_id, tier, nonce, deadline)
    return jsonify(ok=True, voucher={
        "wallet": wallet, "tokenId": token_id, "tier": tier, "tierName": tier_name,
        "amount": TIER_KITTY[tier], "nonce": str(nonce), "deadline": deadline,
        "signature": sig if sig.startswith("0x") else "0x" + sig, "burner": BURNER})


@app.get("/minted")
def minted():
    """All minted HashKitties (on-chain) — for the market/listed view."""
    rows = _onchain_all_mints()
    return jsonify(tokens=rows, count=len(rows),
                   imagesCID="bafybeiacflrmasxrmrw3fhwp3u2qxs3cltmbhwifribimpd7hxa6m35q7y",
                   contract=CONTRACT)


@app.get("/activity")
def activity():
    """Recently minted cats — live from on-chain Minted events when the contract is set;
    else the in-memory demo buffer (HK_DEMO=1 to preview)."""
    if _contract_live():
        rows = _onchain_activity()[::-1]
        return jsonify(mints=rows, count=len(rows))
    with _lock:
        rows = list(_activity)[-24:][::-1]
    return jsonify(mints=rows, count=len(rows))


@app.get("/puzzle")
def get_puzzle():
    wallet = (request.args.get("wallet") or "").strip()
    if not _valid_addr(wallet):
        return jsonify(error="bad wallet"), 400
    rng = random.Random(secrets.randbits(64))
    round_id = secrets.randbelow(2**32)
    p = _new_puzzle(rng, wallet, round_id)
    p.pid = pe.make_pid(f"{wallet}-{round_id}-{secrets.token_hex(6)}")
    with _lock:
        _puzzles[p.pid] = {"puzzle": p, "wallet": wallet.lower(), "exp": time.time() + PUZZLE_TTL, "tries": 0}
    return jsonify(pid=p.pid, category=p.category, prompt=p.prompt,
                   answer_kind=p.answer_kind, expires_in=PUZZLE_TTL)


@app.post("/solve")
def solve():
    data = request.get_json(force=True, silent=True) or {}
    wallet = (data.get("wallet") or "").strip()
    pid = (data.get("pid") or "").strip()
    answer = str(data.get("answer", ""))
    if not _valid_addr(wallet):
        return jsonify(error="bad wallet"), 400

    now = time.time()
    with _lock:
        last = _last_try.get(wallet.lower(), 0)
        if now - last < COOLDOWN:
            return jsonify(error="cooldown", retry_in=round(COOLDOWN - (now - last), 2)), 429
        _last_try[wallet.lower()] = now

        rec = _puzzles.get(pid)
        if not rec:
            return jsonify(error="unknown or expired puzzle"), 404
        if rec["wallet"] != wallet.lower():
            return jsonify(error="puzzle not for this wallet"), 403
        if now > rec["exp"]:
            _puzzles.pop(pid, None)
            return jsonify(error="puzzle expired"), 410
        if rec["tries"] >= MAX_ATTEMPTS:
            _puzzles.pop(pid, None)
            return jsonify(error="too many attempts"), 429
        rec["tries"] += 1
        ok = rec["puzzle"].grade(answer)
        if not ok:
            return jsonify(correct=False, tries_left=MAX_ATTEMPTS - rec["tries"]), 200
        _puzzles.pop(pid, None)  # one-shot

    nonce = secrets.randbits(256)
    deadline = int(now) + VOUCHER_TTL
    sig = _sign_voucher(wallet, nonce, deadline)
    return jsonify(correct=True, voucher={
        "wallet": wallet, "nonce": str(nonce), "deadline": deadline,
        "signature": sig if sig.startswith("0x") else "0x" + sig,
        "contract": CONTRACT, "chainId": CHAIN_ID,
    })


# housekeeping: drop expired puzzles
def _gc():
    while True:
        time.sleep(60)
        now = time.time()
        with _lock:
            for k in [k for k, v in _puzzles.items() if now > v["exp"]]:
                _puzzles.pop(k, None)


def _seed_demo_activity():
    """HK_DEMO=1 only: fake recent mints so the Activity feed renders populated in previews.
    NEVER enabled in production — real rows come from on-chain Minted events."""
    import random as _r
    m = _minted()
    for k in range(18):
        tid = max(1, m - k) if m else (18 - k)
        w = "0x" + "".join(_r.choice("0123456789abcdef") for _ in range(40))
        _activity.append({"tokenId": tid, "wallet": w,
                          "batch": _batch_for(tid - 1)["level"], "ts": int(time.time()) - k * 37})


if __name__ == "__main__":
    if os.environ.get("HK_DEMO") == "1":
        _seed_demo_activity()
    threading.Thread(target=_gc, daemon=True).start()
    port = int(os.environ.get("HK_API_PORT", "8211"))
    print(f"HashKitty API on :{port}  signer={SIGNER.address}  contract={CONTRACT}  chain={CHAIN_ID}")
    app.run(host="127.0.0.1", port=port, threaded=True)
