"""The documented deployment, run for real on this machine: the application in shared mode behind nginx with TLS, using deploy/nginx.conf.example as written.

It is a repeatable check to run before a real host, not a replacement for one: the certificate is self-signed, the client and the proxy are the same machine, and nothing is under load.
Skipped when nginx or openssl is missing. It says nothing about any other proxy or nginx version (tests/pentest/test_proxy_smuggling.py has the request-smuggling shapes)."""

import http.client
import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import pytest

from eventmate.web import serve
from eventmate.web.app import create_app
from eventmate.workspaces import WorkspaceRegistry

NGINX, OPENSSL = shutil.which("nginx"), shutil.which("openssl")
pytestmark = pytest.mark.skipif(NGINX is None or OPENSSL is None, reason="nginx and openssl are needed")
EXAMPLE = Path(__file__).resolve().parent.parent / "deploy" / "nginx.conf.example"
NAME, TOKEN = "todoh.example", "owner-token-0123456789"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Deployment:
    def __init__(self, https_port, tmp):
        self.port, self.tmp = https_port, tmp

    def request(self, method, path, body=None, headers=None, host=NAME, timeout=10):
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # the certificate is self-signed
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=ctx, timeout=timeout)
        h = {"Host": host, **(headers or {})}
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, data, resp.msg.get_all("Set-Cookie") or [])
        conn.close()
        return out

    def csrf(self):
        status, headers, data, cookies = self.request("GET", "/api/session")
        return json.loads(data)["csrf"], "; ".join(c.split(";")[0] for c in cookies)


@pytest.fixture(scope="module")
def deployed(tmp_path_factory, monkeypatch_module):
    tmp = tmp_path_factory.mktemp("proxy")
    back, front = free_port(), free_port()
    for k, v in {"EVENTMATE_TRUSTED_PROXY": "127.0.0.1", "EVENTMATE_OWNER_TOKEN": TOKEN, "EVENTMATE_PUBLIC_URL": f"https://{NAME}", "EVENTMATE_ENABLE_LLM": "0", "EVENTMATE_SECRET": "s" * 40}.items():
        monkeypatch_module.setenv(k, v)
    app = create_app(registry=WorkspaceRegistry(tmp / "data", "s" * 40), allowed_hosts={NAME}, rate_limit_per_minute=12)
    srv = serve.make_waitress(app, "127.0.0.1", back, channel_timeout=15)
    threading.Thread(target=srv.run, daemon=True).start()
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", f"/CN={NAME}"], check=True, capture_output=True)
    server = EXAMPLE.read_text()
    for placeholder, value in {"server_name": NAME, "https_listen": f"127.0.0.1:{front}", "cert": cert, "key": key, "backend": f"127.0.0.1:{back}"}.items():
        server = server.replace("{{" + placeholder + "}}", str(value))
    assert "{{" not in server.replace("{{placeholders}}", ""), "an unfilled placeholder is left in deploy/nginx.conf.example"
    (tmp / "nginx.conf").write_text(f"""worker_processes 1; pid {tmp}/nginx.pid; error_log {tmp}/err.log;
events {{ worker_connections 64; }}
http {{ access_log off; client_body_temp_path {tmp}/b; proxy_temp_path {tmp}/p; fastcgi_temp_path {tmp}/f; uwsgi_temp_path {tmp}/u; scgi_temp_path {tmp}/s;
{server}
}}
""")
    check = subprocess.run([NGINX, "-t", "-c", str(tmp / "nginx.conf"), "-p", str(tmp)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    proc = subprocess.Popen([NGINX, "-c", str(tmp / "nginx.conf"), "-p", str(tmp), "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", front), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield Deployment(front, tmp)
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_the_probes_answer_through_tls(deployed):
    for path in ("/healthz", "/readyz"):
        assert deployed.request("GET", path)[0] == 200, path


def test_the_pages_carry_the_security_headers_and_hsts_because_the_proxy_says_https(deployed):
    status, h, _, _ = deployed.request("GET", "/")
    assert status == 200 and "default-src" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"
    assert h["strict-transport-security"].startswith("max-age=")  # believed only because the request came from the trusted proxy


def test_another_host_name_is_refused_by_the_proxy(deployed):
    assert deployed.request("GET", "/", host="evil.example")[0] == 421


def test_the_session_cookie_is_secure_http_only_and_strict(deployed):
    cookies = deployed.request("GET", "/api/session")[3]
    assert cookies and all(("Secure" in c and "HttpOnly" in c and "SameSite=Strict" in c) for c in cookies), cookies


def test_a_write_without_the_csrf_header_is_refused(deployed):
    _, cookie = deployed.csrf()
    assert deployed.request("POST", "/api/workspace/recovery", body="{}", headers={"Cookie": cookie, "Content-Type": "application/json"})[0] == 403


def test_a_body_over_the_applications_limit_is_refused_by_the_application_not_cut_by_the_proxy(deployed):
    token, cookie = deployed.csrf()
    status, *_ = deployed.request("POST", "/api/intake", body=json.dumps({"messages": ["x" * 100_000]}), headers={"X-EventMate-CSRF": token, "Cookie": cookie, "Content-Type": "application/json"})
    assert status == 413  # 100 KB is under nginx's 6 MB and over the application's 64 KB


def test_the_owner_status_route_is_invisible_without_the_token(deployed):
    assert deployed.request("GET", "/api/owner/status")[0] == 404
    assert deployed.request("GET", "/api/owner/status", headers={"X-EventMate-Owner": "wrong-wrong-wrong-wrong"})[0] == 404
    status, _, data, _ = deployed.request("GET", "/api/owner/status", headers={"X-EventMate-Owner": TOKEN})
    assert status == 200 and "uptime" in json.dumps(json.loads(data)).lower()


def test_the_progress_stream_reaches_the_browser_as_it_is_written_not_when_it_ends(deployed):
    """The stream sends its first event at once and closes after about 5 s with nothing running. A buffering proxy would hand over the first byte only at the close."""
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    token, cookie = deployed.csrf()
    conn = http.client.HTTPSConnection("127.0.0.1", deployed.port, context=ctx, timeout=15)
    started = time.monotonic()
    conn.request("GET", "/api/pulse/stream", headers={"Host": NAME, "Cookie": cookie})
    resp = conn.getresponse()
    first = resp.read(20)  # chunked: read() decodes the chunk, so this returns when the first event arrives
    elapsed = time.monotonic() - started
    conn.close()
    assert resp.status == 200 and b"event" in first and elapsed < 2.5, (resp.status, first, elapsed)


def test_a_client_cannot_make_the_application_believe_it_is_https_or_someone_else(deployed):
    """Last in the file on purpose: it uses up the per-address write limit. The proxy sets X-Forwarded-Proto and X-Forwarded-For itself, so what the client sends is dropped: a spoofed address does not escape the per-address write limit."""
    token, cookie = deployed.csrf()
    codes = []
    for i in range(30):
        status, *_ = deployed.request("POST", "/api/workspace/recovery", body="{}", headers={"X-EventMate-CSRF": token, "Cookie": cookie, "Content-Type": "application/json", "X-Forwarded-For": f"203.0.113.{i}", "X-Forwarded-Proto": "http"})
        codes.append(status)
    # the per-address limit is 12 a minute and the per-workspace limit 24: a 429 by the 13th request is the address limit, which only trips if the address was not taken from the client
    assert 429 in codes[:13], f"the address limit was never reached, so a spoofed X-Forwarded-For was believed: {codes}"
