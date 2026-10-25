#!/usr/bin/env python3
"""Pin a folder of files to IPFS via Pinata as a single directory.
Usage: JWT read from /root/.stockerpunk_deploy/pinata.jwt
  python3 pin_folder.py <dir> <pin_name>
Prints the directory CID. Files are accessed as ipfs://<CID>/<filename>.
"""
import os, sys, json, urllib.request, urllib.error, mimetypes, uuid

DIR = sys.argv[1]
NAME = sys.argv[2]
JWT = open("/root/.stockerpunk_deploy/pinata.jwt").read().strip()
URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"

files = sorted(os.listdir(DIR))
boundary = "----stpunk" + uuid.uuid4().hex
CRLF = b"\r\n"
body = []

def field(name, value):
    body.append(b"--" + boundary.encode())
    body.append(('Content-Disposition: form-data; name="%s"' % name).encode())
    body.append(b"")
    body.append(value.encode() if isinstance(value, str) else value)

# each file, wrapped under a common root folder so Pinata returns one directory CID
root = NAME
for fn in files:
    p = os.path.join(DIR, fn)
    if not os.path.isfile(p):
        continue
    ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
    with open(p, "rb") as fh:
        data = fh.read()
    body.append(b"--" + boundary.encode())
    body.append(('Content-Disposition: form-data; name="file"; filename="%s/%s"' % (root, fn)).encode())
    body.append(("Content-Type: %s" % ctype).encode())
    body.append(b"")
    body.append(data)

field("pinataOptions", json.dumps({"cidVersion": 1}))
field("pinataMetadata", json.dumps({"name": NAME}))
body.append(b"--" + boundary.encode() + b"--")
body.append(b"")
payload = CRLF.join(body)

req = urllib.request.Request(URL, data=payload, method="POST")
req.add_header("Authorization", "Bearer " + JWT)
req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
sys.stderr.write("uploading %d files (%.1f MB) as '%s'...\n" % (len(files), len(payload) / 1e6, NAME))
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        out = json.loads(r.read())
        print(out["IpfsHash"])
except urllib.error.HTTPError as e:
    sys.stderr.write("HTTP %s: %s\n" % (e.code, e.read().decode()[:500]))
    sys.exit(1)
