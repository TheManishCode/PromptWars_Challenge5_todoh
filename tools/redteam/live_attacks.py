"""Socket-level attacks against the real dev server (the one `python -m eventmate.web` starts). Needs the venv from envb.sh."""
import json, socket, sys, tempfile, threading, time, resource
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
from werkzeug.serving import make_server
from eventmate.service import EventMate
from eventmate.web.app import create_app

import logging; logging.getLogger("werkzeug").setLevel(logging.ERROR)
resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096))
tmp = Path(tempfile.mkdtemp())
em = EventMate(log_dir=tmp / "l", plan_dir=tmp / "p", run_id="pt")
app = create_app(em, today_fn=lambda: date(2026, 10, 30), rate_limit_per_minute=10**6)
srv = make_server("127.0.0.1", 0, app, threaded=True)
PORT = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()

def raw(data: bytes, read=4096, timeout=5, hold=False):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=timeout)
    s.sendall(data)
    out = b""
    try:
        while len(out) < read:
            c = s.recv(65536)
            if not c:
                break
            out += c
            if b"\r\n\r\n" in out and not hold and read <= 4096:
                break
    except Exception as e:
        out += repr(e).encode()
    if not hold:
        s.close()
    return out, s

def status(b): return b.split(b"\r\n", 1)[0].decode(errors="replace")
def legit():
    t = time.perf_counter()
    out, _ = raw(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n", timeout=10)
    return status(out), round(time.perf_counter() - t, 3)

print("baseline", legit())

# 1 host header attacks
for h in ["evil.com", "127.0.0.1.evil.com", "localhost:80@evil.com", "LOCALHOST", "localhost.", "127.0.0.1:99999", "[::1]", "127.0.0.1\tx", ""]:
    out, _ = raw(f"GET /api/session HTTP/1.1\r\nHost: {h}\r\nConnection: close\r\n\r\n".encode())
    print("host", repr(h), status(out))
out, _ = raw(b"GET /api/session HTTP/1.1\r\nHost: evil.com\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
print("dup host (evil first)", status(out))
out, _ = raw(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nHost: evil.com\r\nConnection: close\r\n\r\n")
print("dup host (good first)", status(out))
out, _ = raw(b"GET http://evil.com/api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
print("absolute-form URI with evil authority", status(out))

# 2 chunked oversize body
body = b"{\"x\":\"" + b"A" * (10 * 1024 * 1024)
s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
tok = json.loads(raw(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n", 8192)[0].split(b"\r\n\r\n", 1)[1])["csrf"]
hdr = f"POST /api/assistant/ask HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nX-EventMate-CSRF: {tok}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n".encode()
t = time.perf_counter()
sent = 0
try:
    s.sendall(hdr)
    for i in range(0, len(body), 65536):
        chunk = body[i:i + 65536]
        s.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        sent += len(chunk)
    s.sendall(b"0\r\n\r\n")
except Exception as e:
    print("chunked send stopped:", type(e).__name__, "after", sent, "bytes")
try:
    print("chunked oversize ->", status(s.recv(4096)), round(time.perf_counter() - t, 2), "s")
except Exception as e:
    print("chunked recv", repr(e))
s.close()

# 3 header bombs
out, _ = raw(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nX: " + b"A" * 2_000_000 + b"\r\n\r\n")
print("2MB header line ->", status(out))
out, _ = raw(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\n" + b"".join(b"H%d: v\r\n" % i for i in range(50000)) + b"\r\n")
print("50k headers ->", status(out))
out, _ = raw(b"GET /" + b"A" * 1_000_000 + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
print("1MB URL ->", status(out))

# 4 smuggling-style CL+TE
out, _ = raw(f"POST /api/inbox/poll HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nX-EventMate-CSRF: {tok}\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\nGET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode(), read=65536)
print("CL+TE ->", status(out), out.count(b"HTTP/1.") , "responses")

# 5 slowloris
socks = []
t0 = time.perf_counter()
n = 0
try:
    for i in range(1500):
        s = socket.create_connection(("127.0.0.1", PORT), timeout=3)
        s.sendall(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nX-a: ")
        socks.append(s); n += 1
except Exception as e:
    print("slowloris stopped at", n, repr(e))
print(f"opened {n} half-open connections in {time.perf_counter()-t0:.1f}s; legit request now:", legit())
time.sleep(20)
print("20s later, legit:", legit(), "(connections still held:", sum(1 for s in socks if s.fileno() != -1), ")")
for s in socks: s.close()
print("after release", legit())
