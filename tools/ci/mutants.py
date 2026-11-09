#!/usr/bin/env python3
"""Negative controls, run by the gate (G7): prove that the guards' tests can fail.

Every line of tools/ci/mutants/manifest.txt names a patch that re-introduces a real bug from our reviews (a cap off, a hash not checked, a quote that prices another event ...) and the tests that
must FAIL when it is applied. For each mutant, in a throwaway worktree of HEAD:
  1. the patch must still apply (a refactor that moved the line must update the patch: "update this mutant", never a silent retirement);
  2. the named tests must PASS without it (otherwise a failure means nothing);
  3. with it applied the named tests must FAIL (pytest exit 1: tests failed; an import or collection error is not a kill and is reported as a broken mutant).
A mutant that survives, does not apply, or breaks the interpreter fails the gate.
Usage: mutants.py [--only NAME]... [--list] [--keep]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "tools" / "ci" / "mutants"


def parse_manifest() -> list[dict]:
    rows = []
    for raw in (DIR / "manifest.txt").read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        name, patch, tests, why = (x.strip() for x in raw.split("|", 3))
        rows.append({"name": name, "patch": DIR / patch, "tests": tests.split(), "why": why})
    return rows


def sh(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)


def pytest(wt: Path, tests: list[str], env: dict) -> subprocess.CompletedProcess:
    return sh([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests], wt, env)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the worktree (to look at a survivor)")
    a = ap.parse_args()
    rows = [r for r in parse_manifest() if not a.only or r["name"] in a.only]
    if a.list:
        for r in rows:
            print(f"{r['name']:38s} {r['why']}")
        return 0
    if not rows:
        print("no mutants selected")
        return 2
    wt = Path(tempfile.mkdtemp(prefix="todoh-mutants-")) / "wt"
    sh(["git", "worktree", "add", "-q", "--detach", str(wt), "HEAD"], ROOT)
    solvers = Path(os.environ.get("CI_SOLVERS", ROOT / "solvers"))
    if solvers.exists() and not (wt / "solvers").exists():
        (wt / "solvers").symlink_to(solvers)
    env = {**os.environ, "PYTHONPATH": str(wt), "HYPOTHESIS_PROFILE": os.environ.get("HYPOTHESIS_PROFILE", "ci")}
    env.pop("EVENTMATE_ENV_FILE", None)
    problems: list[str] = []
    try:
        dirty = sh(["git", "status", "--porcelain", "--untracked-files=no"], ROOT).stdout.strip()
        if dirty:
            print("note: the working tree has uncommitted changes; mutants run against HEAD (commit first to test them)")
        # 1. every patch applies to HEAD
        for r in rows:
            chk = sh(["git", "apply", "--check", str(r["patch"])], wt)
            if chk.returncode != 0:
                problems.append(f"{r['name']}: the patch no longer applies ({chk.stderr.strip().splitlines()[-1] if chk.stderr.strip() else 'unknown'}): update this mutant: the code it breaks has moved")
        # 2. the named tests pass on the unmodified code
        every = sorted({t for r in rows for t in r["tests"]})
        t0 = time.time()
        base = pytest(wt, every, env)
        if base.returncode != 0:
            problems.append("baseline: the named tests do not all pass on the unmodified code, so a failure under a mutant would mean nothing:\n" + "\n".join(base.stdout.splitlines()[-15:]))
        print(f"baseline: {len(every)} test target(s) pass on HEAD ({time.time() - t0:.0f}s)" if base.returncode == 0 else "baseline: FAIL")
        # 3. each mutant is killed
        if not problems:
            for r in rows:
                sh(["git", "checkout", "-q", "--", "."], wt)
                app = sh(["git", "apply", str(r["patch"])], wt)
                if app.returncode != 0:
                    problems.append(f"{r['name']}: could not apply: update this mutant")
                    continue
                t0 = time.time()
                res = pytest(wt, r["tests"], env)
                took = time.time() - t0
                if res.returncode == 1:
                    print(f"  killed    {r['name']:38s} ({took:.0f}s)")
                elif res.returncode == 0:
                    print(f"  SURVIVED  {r['name']:38s} ({r['why']})")
                    problems.append(f"{r['name']}: SURVIVED: the tests {' '.join(r['tests'])} still pass with this bug in: {r['why']}")
                else:
                    print(f"  BROKEN    {r['name']:38s} (pytest exit {res.returncode})")
                    problems.append(f"{r['name']}: pytest exited {res.returncode} (not 'tests failed'): the mutant broke collection or the interpreter: update this mutant\n" + "\n".join(res.stdout.splitlines()[-8:]))
    finally:
        if not a.keep:
            sh(["git", "worktree", "remove", "--force", str(wt)], ROOT)
            shutil.rmtree(wt.parent, ignore_errors=True)
    if problems:
        print(f"\nmutants: {len(problems)} problem(s):")
        for p in problems:
            print(" -", p)
        return 1
    print(f"\nmutants: all {len(rows)} killed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
