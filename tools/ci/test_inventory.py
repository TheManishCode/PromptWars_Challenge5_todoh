#!/usr/bin/env python3
"""The collected set (CI-1). Tests can vanish without a single skip: a `collect_ignore` line in a conftest, a deleted file, an import that fails quietly. `tools/ci/test_inventory.txt` records how many tests
each file collects (from `pytest --collect-only -q`); the gate fails if a file disappears or its count drops, and tells you to update the inventory in the same commit if that was intended.
  test_inventory.py            check: nothing disappeared or shrank (growth is reported, not a failure)    [the tests job]
  test_inventory.py --exact    the inventory equals the tree: any difference, growth included, fails        [the static job: it must be regenerated in the commit that adds tests]
  test_inventory.py --write    regenerate the inventory
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = Path(__file__).with_name("test_inventory.txt")
HEADER = "# Tests collected per file (pytest --collect-only -q). Regenerate: tools/ci/test_inventory.py --write. A file that disappears or shrinks fails the gate (CI-1)."


def collected() -> Counter:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    counts: Counter = Counter()
    for line in out.stdout.splitlines():
        m = re.match(r"^(tests/[\w/.-]+\.py)::", line)
        if m:
            counts[m.group(1)] += 1
    if out.returncode not in (0, 5) or not counts:
        print("collection failed:\n" + "\n".join((out.stdout + out.stderr).splitlines()[-15:]))
        raise SystemExit(2)
    errors = [ln for ln in out.stdout.splitlines() if ln.startswith("ERROR ")]
    if errors:
        print("collection errors (a test module that does not import is a test module that does not run):\n  " + "\n  ".join(errors[:10]))
        raise SystemExit(2)
    return counts


def read() -> dict[str, int]:
    rows = {}
    for raw in INVENTORY.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            path, _, n = raw.rpartition(" ")
            rows[path] = int(n)
    return rows


def write(counts: Counter) -> None:
    INVENTORY.write_text(HEADER + "\n" + "".join(f"{p} {n}\n" for p, n in sorted(counts.items())), encoding="utf-8")


def main(argv: list[str]) -> int:
    now = collected()
    if "--write" in argv:
        write(now)
        print(f"inventory written: {len(now)} files, {sum(now.values())} tests")
        return 0
    have = read()
    gone = sorted(p for p in have if p not in now)
    shrunk = sorted((p, have[p], now[p]) for p in have if p in now and now[p] < have[p])
    grown = sorted((p, have[p], now[p]) for p in have if p in now and now[p] > have[p])
    new = sorted(p for p in now if p not in have)
    if gone or shrunk:
        print("tests have DISAPPEARED from the collected set (no skip, they are simply not there):")
        for p in gone:
            print(f"  - {p}: {have[p]} tests, now not collected at all")
        for p, a, b in shrunk:
            print(f"  - {p}: {a} -> {b} tests")
        print("If this removal is intended, update tools/ci/test_inventory.txt in the same commit (tools/ci/test_inventory.py --write).")
        return 1
    if grown or new:
        print(f"collected set grew: +{sum(b - a for _, a, b in grown) + sum(now[p] for p in new)} tests ({len(new)} new file(s), {len(grown)} grown): " + ", ".join([f"{p} +{b - a}" for p, a, b in grown] + [f"{p} (new, {now[p]})" for p in new])[:400])
        if "--exact" in argv:
            print("the inventory is out of date: run tools/ci/test_inventory.py --write and commit it with the tests.")
            return 1
    print(f"test inventory: {sum(now.values())} tests in {len(now)} files; nothing disappeared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
