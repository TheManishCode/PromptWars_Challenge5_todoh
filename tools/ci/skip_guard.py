"""The skip guard (G1). Reads the junit XML of a pytest run and fails if any test was skipped that is not on tools/ci/allowed_skips.txt.

A skipped test is a test that did not run. The suite skips when nginx, Chromium, Firefox or the flights library is missing, so a runner without them would stay green with the hosted journey
and the browser matrix gone. The allow-list is the exact set of opt-in tests that need outside software or live services (Docling, live flights, the voice engine); everything else
must run. A new skip fails with its reason: either fix the cause or add a line to the allow-list on purpose, in review.

Each non-comment line of allowed_skips.txt is `<test id glob> | <reason must contain>`. The test id is `<junit classname>::<name>`, for a module-level skip `<module>::<module>`.
Usage: skip_guard.py JUNIT_XML [ALLOWLIST]
"""
from __future__ import annotations

import fnmatch
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def load_allowed(path: Path) -> list[tuple[str, str]]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        glob, _, reason = line.partition("|")
        out.append((glob.strip(), reason.strip()))
    return out


def skipped(junit: Path) -> list[tuple[str, str]]:
    found = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        sk = case.find("skipped")
        if sk is not None:
            found.append((f"{case.get('classname', '')}::{case.get('name', '')}", (sk.get("message") or sk.text or "").strip()))
    return found


def check(junit: Path, allow: Path) -> tuple[list[str], list[str]]:
    """(unexpected skips, allow-list lines that matched nothing)"""
    allowed = load_allowed(allow)
    used: set[int] = set()
    bad = []
    for test_id, why in skipped(junit):
        hit = next((i for i, (g, r) in enumerate(allowed) if fnmatch.fnmatchcase(test_id, g) and r in why), None)
        if hit is None:
            bad.append(f"{test_id}: {why[:160]}")
        else:
            used.add(hit)
    return bad, [f"{g} | {r}" for i, (g, r) in enumerate(allowed) if i not in used]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    junit = Path(argv[1])
    allow = Path(argv[2]) if len(argv) > 2 else Path(__file__).with_name("allowed_skips.txt")
    bad, unused = check(junit, allow)
    total = len(skipped(junit))
    if bad:
        print(f"skip guard: {len(bad)} unexpected skip(s): these tests did NOT run and are not on tools/ci/allowed_skips.txt:")
        for b in bad:
            print("  -", b)
        print("Fix the cause (install the missing tool: docs/CI.md) or, if the skip is truly opt-in, add it to the allow-list in review.")
        return 1
    print(f"skip guard: {total} skip(s), all on the allow-list")
    if unused:  # not a failure: an opt-in test may be removed. Said so the list does not rot.
        print("note: allow-list lines that matched no skip this run (remove them if the test is gone):", *unused, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
