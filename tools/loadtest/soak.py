"""Sustained-load test against the REAL server (waitress, shared mode) started in a child process.

N visitors, each with its own address and workspace, loop through realistic work for ``--seconds``. Two kinds: the dashboard visitor (open the room, plan an event, read it, add ledger entries,
ask the assistant, export) and the /plan visitor (``--plan-share``, half by default), who walks what "Start planning" leads to through the API: read a brief (staging), add a venue and a caterer they
know, write the requests, mark them sent, paste the replies, confirm every line, choose the vendors, commit with from_quotes and poll the commit queue until the plan is done. Reported: requests, errors by class, latency p50/p95/p99 per route, and the server's memory, open files and threads at the start (after warm-up) and the end.
The run FAILS (exit 1) on any 5xx, a p95 over ``--p95-ms``, or growth beyond the bounds below, which is what a leak looks like.
    python tools/loadtest/soak.py --seconds 60 --visitors 8            # CI smoke
    python tools/loadtest/soak.py --seconds 1800 --visitors 20          # a soak before a release
Needs the solvers (EVENTMATE_FD, EVENTMATE_VAL) like the test suite. Everything is synthetic; the data directory is a temporary one that is removed at the end.
"""
import argparse
import http.client
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

HOST = "soak.test"
TEXT = "Plan a 120-person engagement party in Pune on December 12 under INR 200,000. Vegetarian catering, indoor venue, photographer, and reminders for invitations and vendor confirmation."


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.2", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def stats(pid):
    out = {}
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmRSS:"):
            out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
        if line.startswith("Threads:"):
            out["threads"] = int(line.split()[1])
    out["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    return out


class Client:
    def __init__(self, port, xff):
        self.port, self.xff, self.cookie, self.csrf = port, xff, None, None

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.2", self.port, timeout=90)
        h = {"Host": HOST, "X-Forwarded-For": self.xff, "X-Forwarded-Proto": "https", "Connection": "close"}
        if self.cookie:
            h["Cookie"] = "todoh_ws=" + self.cookie
        if self.csrf and (method != "GET" or path == "/api/workspace/export"):  # the export is a GET that still needs the token
            h["X-EventMate-CSRF"] = self.csrf
        data = None
        if body is not None or method in ("POST", "DELETE"):
            data = json.dumps(body if body is not None else {}).encode()
            h["Content-Type"] = "application/json"
            h["Content-Length"] = str(len(data))
        t0 = time.perf_counter()
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        ms = (time.perf_counter() - t0) * 1000
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and "todoh_ws=" in v:
                self.cookie = v.split("todoh_ws=")[1].split(";")[0]
        c.close()
        try:
            return r.status, json.loads(raw), ms
        except ValueError:
            return r.status, raw, ms


def visitor(i, port, deadline, record, rng):
    cl = Client(port, f"198.51.100.{10 + i % 200}")
    st, j, ms = cl.call("GET", "/api/session")
    record("GET /api/session", st, ms)
    if st != 200:
        return
    cl.csrf = j["csrf"]
    pid = None
    while time.time() < deadline:
        step = rng.random()
        if pid is None or step < 0.08:
            st, j, ms = cl.call("POST", "/api/plans", {"request": TEXT})
            record("POST /api/plans", st, ms)
            if st == 201:
                pid = j["plan"]["plan_id"]
        elif step < 0.35:
            st, _, ms = cl.call("POST", f"/api/plans/{pid}/ledger", {"kind": "payment", "category": "caterer", "amount": rng.randint(100, 5000), "note": "soak"})
            record("POST ledger", st, ms)
        elif step < 0.55:
            st, _, ms = cl.call("POST", "/api/assistant/ask", {"plan_id": pid, "question": "how much is left in the budget?"})
            record("POST assistant/ask", st, ms)
        elif step < 0.62:
            st, _, ms = cl.call("GET", "/api/workspace/export")
            record("GET export", st, ms)
        elif step < 0.80:
            st, _, ms = cl.call("GET", f"/api/plans/{pid}")
            record("GET plan", st, ms)
        else:
            st, _, ms = cl.call("GET", "/api/plans")
            record("GET plans", st, ms)
        time.sleep(rng.uniform(0.8, 2.5))  # a person reads between actions: about 30 requests a minute, under the 60 writes a minute the server allows per address


BRIEF = "birthday for 40 guests in Mysuru on 14 February 2027, veg, budget 60000"
REPLIES = {
    "venue": "Available on the date (yes/no): yes\nPrice for the day (INR): 20,000\nTaxes (GST) included (yes/no): yes\nCapacity (guests): 120\nDeposit to book (INR): 5,000\nQuote valid for (days): 10\nIncluded and extra: tables included",
    "caterer": "Available on the date (yes/no): yes\nPrice per plate (INR): 650\nTaxes (GST) included (yes/no): yes\nMinimum guests: 20\nMaximum guests: 300\nFood options (vegetarian / jain / vegan / non-veg): vegetarian, jain\nDeposit to book (INR): 10,000\nQuote valid for (days): 10\nIncluded and extra: staff and crockery included",
}
PRICE_KEYS = ("price", "price_total", "package")
COMMIT_WAIT_S = 240


def plan_journey(i, n, cl, record, fail, rng):
    """One /plan visitor, start to finish, through the API. Anything unexpected (a non-2xx, a build that never ends) is a failure of the run, not a skipped step."""
    def step(label, method, path, body=None, expect=(200, 201, 202)):
        st, j, ms = cl.call(method, path, body)
        record(label, st, ms)
        if st not in expect:
            fail(f"{label} -> {st} {str(j)[:160]}")
            return None
        time.sleep(rng.uniform(0.3, 1.2))
        return j

    turn = step("POST intake", "POST", "/api/intake", {"messages": [BRIEF]})
    if not turn:
        return
    staged, pid, ids = turn["staged_id"], "brief_" + turn["staged_id"], {}
    for cat in REPLIES:
        item = step("POST user-vendor", "POST", f"/api/plans/{pid}/shortlist/user-vendor", {"name": f"Our {cat} {i}-{n}", "category": cat, "by": "Soak", "email": f"{cat}{i}x{n}@vendor.test"})
        if not item:
            return
        ids[cat] = item["business"]["vendor_id"]
    made = step("POST request-all", "POST", f"/api/plans/{pid}/outreach/request-all", {"sender_name": "Soak"})
    if made is None:
        return
    for d in made["drafts"]:
        if step("POST outreach sent", "POST", f"/api/plans/{pid}/outreach/{d['draft_id']}/sent", {}) is None:
            return
    for cat, vid in ids.items():
        if step("POST replies/paste", "POST", f"/api/plans/{pid}/replies/paste", {"vendor_id": vid, "text": REPLIES[cat], "by": "Soak", "channel": "email"}) is None:
            return
    for _ in range(40):  # confirm every line, the money lines one by one, as a person would
        readings = step("GET quote-readings", "GET", f"/api/plans/{pid}/quote-readings")
        todo = [(a, f) for a in (readings or {}).get("quotes", []) if not a["comparable"] for f in a["fields"] if f["state"] == "read"]
        if not todo:
            break
        a, f = todo[0]
        body = {"fields": {f["key"]: None}, "by": "Soak", **({"valid_days": "10"} if f["key"] in PRICE_KEYS else {})}
        if step("POST quote confirm", "POST", f"/api/plans/{pid}/quote-readings/{a['message_id']}/confirm", body) is None:
            return
    readings = step("GET quote-readings", "GET", f"/api/plans/{pid}/quote-readings")
    comparable = [a for a in (readings or {}).get("quotes", []) if a["comparable"]]
    if len(comparable) != len(REPLIES):
        fail(f"{len(comparable)} of {len(REPLIES)} replies became comparable after every line was confirmed")
        return
    for a in comparable:
        body = {"by": "Soak", **({"setting": "indoor"} if a["category"] == "venue" else {})}
        if step("POST promote", "POST", f"/api/plans/{pid}/quote-readings/{a['message_id']}/promote", body) is None:
            return
    for attempt in range(6):  # the commit queue has a bounded number of places: "busy" is an honest answer, and a person presses the button again
        status = step("GET intake status", "GET", f"/api/intake/{staged}/status")
        if not status:
            return
        st, j, ms = cl.call("POST", f"/api/intake/{staged}/commit", {"version": status["version"], "draft_hash": status["draft_hash"], "from_quotes": True})
        record("POST commit", st, ms)
        if st in (200, 202):
            break
        if st != 429:
            fail(f"POST commit -> {st} {str(j)[:200]}")
            return
        time.sleep(3 + attempt * 2)
    else:
        fail("POST commit stayed busy (429) for six tries")
        return
    t0 = time.time()
    while time.time() - t0 < COMMIT_WAIT_S:
        time.sleep(1.5)
        status = step("GET intake status", "GET", f"/api/intake/{staged}/status")
        if not status:
            return
        if status["state"] in ("done", "failed"):
            record("commit to done", 200 if status["state"] == "done" else 500, (time.time() - t0) * 1000)
            if status["state"] == "failed":
                fail(f"the build failed: {status.get('error')}")
            return
    fail(f"a commit never reached done or failed within {COMMIT_WAIT_S} s")


def plan_visitor(i, port, deadline, record, fail, rng):
    cl = Client(port, f"198.51.100.{10 + i % 200}")
    st, j, ms = cl.call("GET", "/api/session")
    record("GET /api/session", st, ms)
    if st != 200:
        fail("a /plan visitor could not open a session")
        return
    cl.csrf = j["csrf"]
    n = 0
    while time.time() < deadline:
        n += 1
        plan_journey(i, n, cl, record, fail, rng)
        time.sleep(rng.uniform(1, 3))


def pct(values, p):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(len(values) * p))], 1) if values else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--visitors", type=int, default=8)
    ap.add_argument("--p95-ms", type=float, default=3000)
    ap.add_argument("--build-p95-s", type=float, default=60, help="the longest a commit may take to reach done, at the 95th percentile")
    ap.add_argument("--plan-share", type=float, default=0.5, help="the share of visitors that walk the /plan journey (the rest do the dashboard work)")
    ap.add_argument("--max-rss-growth-pct", type=float, default=35)
    ap.add_argument("--max-fd-growth", type=int, default=25)
    ap.add_argument("--max-thread-growth", type=int, default=10)
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    tmp = Path(tempfile.mkdtemp(prefix="soak-"))
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(root), "EVENTMATE_MODE": "shared", "EVENTMATE_DATA_DIR": str(tmp / "data"), "EVENTMATE_SECRET": "s" * 40,
           "EVENTMATE_TRUSTED_PROXY": "127.0.0.1,127.0.0.2", "EVENTMATE_PUBLIC_URL": "https://" + HOST, "EVENTMATE_OWNER_TOKEN": "owner-token-0123456789abcdef",
           "EVENTMATE_ENABLE_LLM": "0", "EVENTMATE_ENV_FILE": "/nonexistent", "EVENTMATE_ACCESS_LOG": "off", "EVENTMATE_MAX_WORKSPACES": "5000", "EVENTMATE_CATALOG": "full",
           "EVENTMATE_WS_LOOKUPS_PER_HOUR": "100000"}
    (tmp / "data").mkdir()
    proc = subprocess.Popen([sys.executable, "-m", "eventmate.web", "--host", "127.0.0.2", "--port", str(port), "--allow-remote", "--allowed-host", HOST], cwd=str(root), env=env,
                            stdout=subprocess.DEVNULL, stderr=open(tmp / "server.log", "wb"))
    try:
        for _ in range(150):
            try:
                if Client(port, "203.0.113.1").call("GET", "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            print("server did not start:\n" + (tmp / "server.log").read_text()[-800:])
            return 2
        lat, codes, lock = defaultdict(list), defaultdict(int), threading.Lock()

        bad = defaultdict(int)

        def record(route, st, ms):
            with lock:
                lat[route].append(ms)
                codes[f"{st // 100}xx" if st != 429 else "429"] += 1
                if st >= 300:
                    bad[f"{route} -> {st}"] += 1

        problems: list[str] = []

        def fail(msg):
            with lock:
                if len(problems) < 20:
                    problems.append(msg)

        warm = time.time() + min(10, a.seconds / 4)
        deadline = time.time() + a.seconds
        n_plan = round(a.visitors * a.plan_share)
        threads = [threading.Thread(target=plan_visitor, args=(i, port, deadline, record, fail, random.Random(i))) if i < n_plan
                   else threading.Thread(target=visitor, args=(i, port, deadline, record, random.Random(i))) for i in range(a.visitors)]
        [t.start() for t in threads]
        while time.time() < warm:
            time.sleep(0.2)
        start = stats(proc.pid)
        peak = dict(start)
        while time.time() < deadline:
            time.sleep(1)
            s = stats(proc.pid)
            peak = {k: max(peak[k], s[k]) for k in s}
        [t.join(120) for t in threads]
        end = stats(proc.pid)
        up = proc.poll() is None
        fails = []
        if not up:
            fails.append("the server process died during the run")
        fails.extend(problems)
        if codes.get("5xx"):
            fails.append(f"{codes['5xx']} responses were 5xx")
        allms = [v for k, vs in lat.items() if k != "commit to done" for v in vs]  # a build's wait is judged on its own, not as a request
        builds = lat.get("commit to done", [])
        if builds and pct(builds, 0.95) > a.build_p95_s * 1000:
            fails.append(f"builds: p95 {pct(builds, 0.95)} ms to done is over {a.build_p95_s} s")
        if allms and codes.get("429", 0) / len(allms) > 0.05:
            fails.append(f"{codes['429']} of {len(allms)} requests were rate limited: the test is not exercising real work (or the limits are too tight for a normal visitor)")
        p95 = pct(allms, 0.95)
        if p95 is not None and p95 > a.p95_ms:
            fails.append(f"p95 {p95} ms is over {a.p95_ms}")
        if start["rss_mb"] and (end["rss_mb"] - start["rss_mb"]) / start["rss_mb"] * 100 > a.max_rss_growth_pct:
            fails.append(f"memory grew {start['rss_mb']} -> {end['rss_mb']} MB")
        if end["fds"] - start["fds"] > a.max_fd_growth:
            fails.append(f"open files grew {start['fds']} -> {end['fds']}")
        if end["threads"] - start["threads"] > a.max_thread_growth:
            fails.append(f"threads grew {start['threads']} -> {end['threads']}")
        report = {"seconds": a.seconds, "visitors": a.visitors, "plan_visitors": n_plan, "requests": len(allms), "by_class": dict(codes), "non_2xx": dict(bad), "builds": len(builds), "build_p50_ms": pct(builds, 0.5), "build_p95_ms": pct(builds, 0.95), "p50_ms": pct(allms, 0.5), "p95_ms": p95, "p99_ms": pct(allms, 0.99),
                  "per_route_p95_ms": {k: pct(v, 0.95) for k, v in sorted(lat.items())}, "server_start": start, "server_peak": peak, "server_end": end, "failures": fails}
        print(json.dumps(report, indent=1))
        if a.report:
            Path(a.report).write_text(json.dumps(report, indent=1))
        return 1 if fails else 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
