"""A stranger plans an event on the hosted site: shared mode, waitress behind deploy/nginx.conf.example with TLS, a real Chromium, and only what a visitor can do there.

A visitor's workspace cannot send or receive mail, so the only road to a plan is the one the page offers for that: add a business you know, write the request,
send it yourself, paste the reply, confirm it line by line, choose the vendor, build. Nothing leaves the machine: the vendors are '.test' addresses typed by the
visitor and nobody is mailed. Skipped without nginx, openssl or Chromium."""

import shutil
import socket
import subprocess
import threading
import time

import pytest

from eventmate.web import serve
from eventmate.web.app import create_app
from eventmate.workspaces import WorkspaceRegistry
from tests.browser_helpers import BROWSER_ARGS, CHROMIUM, NOISE
from tests.test_proxy_deploy import EXAMPLE, NAME, TOKEN, free_port

pw = pytest.importorskip("playwright.sync_api")
NGINX, OPENSSL = shutil.which("nginx"), shutil.which("openssl")
pytestmark = pytest.mark.skipif(NGINX is None or OPENSSL is None or CHROMIUM is None, reason="nginx, openssl and Chromium are needed")

BRIEF = "birthday for 40 guests in Mysuru on 14 February 2027, veg, budget 60000"
REPLIES = {
    "venue": """Available on the date (yes/no): yes
Price for the day (INR): 20,000
Taxes (GST) included (yes/no): yes
Capacity (guests): 120
Deposit to book (INR): 5,000
Quote valid for (days): 10
Included and extra: tables included""",
    "caterer": """Available on the date (yes/no): yes
Price per plate (INR): 650
Taxes (GST) included (yes/no): yes
Minimum guests: 20
Maximum guests: 300
Food options (vegetarian / jain / vegan / non-veg): vegetarian, jain
Deposit to book (INR): 10,000
Quote valid for (days): 10
Included and extra: staff and crockery included""",
}
LABEL = {"venue": "Venue", "caterer": "Caterer"}


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The documented deployment with the application's own default limits (the proxy test lowers them to provoke a 429; a person's visit must not hit them)."""
    tmp = tmp_path_factory.mktemp("hosted")
    back, front = free_port(), free_port()
    origin = f"https://{NAME}:{front}"  # what the browser sends as Origin; on a real host this is the public URL on 443
    mp = pytest.MonkeyPatch()
    for k, v in {"EVENTMATE_TRUSTED_PROXY": "127.0.0.1", "EVENTMATE_OWNER_TOKEN": TOKEN, "EVENTMATE_PUBLIC_URL": origin, "EVENTMATE_ENABLE_LLM": "0", "EVENTMATE_SECRET": "s" * 40,
                 "EVENTMATE_INTAKE_MODEL": "off"}.items():
        mp.setenv(k, v)
    app = create_app(registry=WorkspaceRegistry(tmp / "data", "s" * 40), allowed_hosts={NAME})
    srv = serve.make_waitress(app, "127.0.0.1", back, channel_timeout=15)
    threading.Thread(target=srv.run, daemon=True).start()
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run([OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", f"/CN={NAME}"], check=True, capture_output=True)
    server = EXAMPLE.read_text()
    for placeholder, value in {"server_name": NAME, "https_listen": f"127.0.0.1:{front}", "cert": cert, "key": key, "backend": f"127.0.0.1:{back}"}.items():
        server = server.replace("{{" + placeholder + "}}", str(value))
    # R-011: the shipped file forwards `Host $http_host`, which keeps the port the browser used; `$host` dropped it, so on any port but 443 the Origin check refused every write
    assert "proxy_set_header Host $http_host;" in server and "proxy_set_header Host $host;" not in server
    (tmp / "nginx.conf").write_text(f"""worker_processes 1; pid {tmp}/nginx.pid; error_log {tmp}/err.log;
events {{ worker_connections 64; }}
http {{ access_log off; client_body_temp_path {tmp}/b; proxy_temp_path {tmp}/p; fastcgi_temp_path {tmp}/f; uwsgi_temp_path {tmp}/u; scgi_temp_path {tmp}/s;
{server}
}}
""")
    proc = subprocess.Popen([NGINX, "-c", str(tmp / "nginx.conf"), "-p", str(tmp), "-g", "daemon off;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", front), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield origin, front, tmp
    proc.terminate()
    proc.wait(5)
    mp.undo()


def _browser(p, front):
    b = p.chromium.launch(executable_path=CHROMIUM, args=[*BROWSER_ARGS, f"--host-resolver-rules=MAP {NAME} 127.0.0.1"])
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)  # the certificate is self-signed
    return b, ctx


def _watch(page):
    problems = []
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" and not any(n in m.text for n in NOISE) else None)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on("response", lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 400 else None)
    return problems


def _step(page, origin, step):
    page.goto(f"{origin}/plan#findings/{step}", wait_until="networkidle")
    page.wait_for_selector(".finding-room:not([inert])")


def test_a_visitor_reaches_a_validated_plan_through_the_proxy_with_nothing_sent(site):
    origin, front, tmp = site
    with pw.sync_playwright() as p:
        b, ctx = _browser(p, front)
        page = ctx.new_page()
        problems = _watch(page)
        try:
            page.goto(origin + "/", wait_until="networkidle")
            page.get_by_role("link", name="Start planning").first.click()
            page.wait_for_url("**/plan*")
            page.click(".skip")
            page.wait_for_selector("#idea:focus")
            page.fill("#idea", BRIEF)
            page.press("#idea", "Enter")
            page.wait_for_selector(".review-room:not([inert])")
            page.click("#answer-yes")
            page.click("#review-done")
            page.wait_for_selector(".finding-room:not([inert])")

            _step(page, origin, "vendors")
            page.fill("input[placeholder^='Recorded']", "Asha")
            for cat, label in LABEL.items():
                note = page.locator(f".note:has-text('{label}')").filter(has=page.get_by_role("button", name="Add one you know")).first
                note.get_by_role("button", name="Add one you know").click()
                form = page.locator(".inline-form").first
                form.locator("input[type=text]").fill(f"Our {cat} (Mysuru)")
                form.locator("input[type=email]").fill(f"{cat}@vendor.test")
                form.locator("button[type=submit]").click()
                page.wait_for_selector(f"[data-vendor]:has-text('Our {cat}')")

            _step(page, origin, "message")
            page.get_by_role("button", name="Write one request per business").click()
            page.wait_for_selector("text=Send it yourself instead")
            for _ in range(len(LABEL)):  # each click re-renders the list: find the next one afresh
                closed = page.locator("details:not([open]) > summary:has-text('Send it yourself instead')")
                if closed.count():
                    closed.first.click()
                sent = page.get_by_role("button", name="I sent it")
                if not sent.count():
                    break
                sent.first.click()
                page.wait_for_timeout(600)
            assert page.get_by_role("button", name="I sent it").count() == 0, "every request is marked as sent by the visitor"

            _step(page, origin, "replies")
            for cat in LABEL:
                form = page.locator("form.paste-reply")
                form.locator("select").first.select_option(label=f"Our {cat} (Mysuru)") if form.locator("select").count() else None
                form.locator("textarea").fill(REPLIES[cat])
                form.get_by_role("button", name="Read this reply").click()
                page.wait_for_timeout(800)
            for _ in range(40):  # confirm every line, the money lines one by one, as a person would
                btn = page.locator("button:has-text('Confirm price and validity'), button.chip--strong:has-text('Confirm'), button:has-text('other lines as read'), button.chip:text-is('Confirm')").first
                if not btn.count():
                    break
                btn.click()
                page.wait_for_timeout(300)

            # R-012: with every line confirmed but nobody chosen yet, Build is off and says what to do; and a single quote can be chosen without pressing "done waiting"
            _step(page, origin, "plan")
            assert page.get_by_role("button", name="Build the plan").is_disabled()
            notice = page.locator(".finding-room").inner_text()
            assert "under “Compare”" in notice and "Plan with this vendor" in notice and "No confirmed quote" not in notice, notice[:800]
            _step(page, origin, "compare")
            assert page.get_by_text("Only one quote so far").count() >= 1
            assert page.get_by_role("button", name="Plan with this vendor").count() == len(LABEL)
            for _ in range(len(LABEL)):
                choose = page.get_by_role("button", name="Plan with this vendor")
                if not choose.count():
                    break
                sel = page.locator("select[aria-label='Indoor or outdoor']")
                if sel.count():
                    sel.first.select_option(index=1)
                with page.expect_response(lambda r: "/promote" in r.url) as chosen:
                    choose.first.click()
                assert chosen.value.status < 300, f"{chosen.value.status} {chosen.value.text()[:300]}"
                page.wait_for_timeout(600)

            _step(page, origin, "plan")
            build = page.get_by_role("button", name="Build the plan")
            assert build.is_enabled(), page.locator(".finding-room").inner_text()[:1500]
            with page.expect_response(lambda r: r.url.endswith("/commit")) as commit:
                build.click()
            assert commit.value.status < 300, f"{commit.value.status} {commit.value.text()[:500]}"
            page.locator("a:text-is('Open it in the dashboard')").or_(page.get_by_text("The plan could not be built")).first.wait_for(timeout=180_000)
            text = page.locator(".finding-room").inner_text()
            assert "could not be built" not in text, text[:1500]
            assert "The timeline" in text and "Nothing has been booked or paid" in text, text[:1500]
            plan = page.locator(".note:has-text('THE PLAN')").first.inner_text()
            assert "Mysuru" in plan and "Our venue" in text and "Our caterer" in text, plan
        except Exception:
            page.screenshot(path=str(tmp / "failure.png"), full_page=True)
            print("screenshot:", tmp / "failure.png")
            raise
        finally:
            b.close()
    assert not [x for x in problems if "favicon" not in x], problems
