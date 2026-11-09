#!/usr/bin/env bash
# The tests that need nginx, openssl, Chromium, Firefox or the flights library SKIP when the tool is missing. In CI a missing tool must be a failure, not a green run with tests gone
# (R-018 was 40 tests skipped for a missing extra). This runs first and says exactly what is missing.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
missing=()
for t in nginx openssl; do command -v "$t" >/dev/null || missing+=("$t"); done
"$PY" - <<'PY' || missing+=("python-modules")
import importlib, sys
bad = []
for m in ("fli", "playwright.sync_api", "waitress", "pytest_cov"):
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{m} ({type(e).__name__})")
import json, tomllib  # noqa: E401
from importlib.metadata import distribution
want = tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]["flights"][0].rsplit("@", 1)[-1].strip()
try:
    got = json.loads(distribution("flights").read_text("direct_url.json") or "{}").get("vcs_info", {}).get("commit_id", "")
except Exception:  # noqa: BLE001
    got = ""
if got != want:
    bad.append(f"flights from the pyproject extra (commit {want[:12]}; installed: {got[:12] or 'the PyPI 0.9.0 from the lock'}): run tools/ci/install_flights.sh")
import subprocess  # noqa: E402
r = subprocess.run([sys.executable, "tools/ci/check_fli.py"], capture_output=True, text=True)
if r.returncode:
    bad.append(r.stdout.strip() or "fli (the usability check crashed)")
from tests.browser_helpers import CHROMIUM  # noqa: E402
if CHROMIUM is None:
    bad.append("chromium or google-chrome-stable on PATH")
else:
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.firefox.launch(); b.close()
    except Exception as e:  # noqa: BLE001
        bad.append(f"the Playwright Firefox build ({str(e)[:60]})")
if bad:
    print("missing: " + "; ".join(bad)); sys.exit(1)
PY
if [ "${#missing[@]}" -gt 0 ]; then echo "required tools missing: ${missing[*]}  (see docs/CI.md: the tests would silently skip)"; exit 1; fi
echo "nginx, openssl, chromium, firefox, fli, waitress, playwright: present"
