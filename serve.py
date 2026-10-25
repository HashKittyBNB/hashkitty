#!/usr/bin/env python3
"""
HashKitty combined origin for hashkitty.fun:
  /         -> static site (web/)
  /api/*    -> puzzle API (GET /api/puzzle, POST /api/solve, /api/stats, /api/activity)
One port so the Cloudflare tunnel has a single service to point at.
"""
import os, threading
from flask import Flask, send_from_directory
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.serving import run_simple

import api as hk  # puzzle API (hk.app), config via env

WEBDIR = os.path.join(os.path.dirname(__file__), "web")

web = Flask(__name__, static_folder=None)

@web.route("/")
def index():
    return send_from_directory(WEBDIR, "index.html")

@web.route("/gallery")
def gallery():
    return send_from_directory(WEBDIR, "gallery.html")

@web.route("/market")
def market():
    return send_from_directory(WEBDIR, "market.html")

@web.route("/burn")
def burn():
    return send_from_directory(WEBDIR, "burn.html")

@web.route("/<path:p>")
def static_files(p):
    return send_from_directory(WEBDIR, p)

application = DispatcherMiddleware(web, {"/api": hk.app})

# start background workers at import time so they run under gunicorn too (guard double-start)
if not getattr(hk, "_gc_started", False):
    hk._gc_started = True
    threading.Thread(target=hk._gc, daemon=True).start()
hk._start_refresher()  # pulls on-chain reads into caches; request handlers never do RPC

if __name__ == "__main__":
    if os.environ.get("HK_DEMO") == "1":
        hk._seed_demo_activity()
    port = int(os.environ.get("HK_PORT", "8219"))
    print(f"HashKitty origin on 127.0.0.1:{port}  (/ = site, /api = puzzle API)  signer={hk.SIGNER.address}")
    run_simple("127.0.0.1", port, application, threaded=True)
