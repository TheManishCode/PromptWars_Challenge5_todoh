#!/usr/bin/env python3
"""Name the failures (CI-4). Reads a junit XML, writes the node ids of every failed or errored test to FAILED_TXT, prints them, and appends them to the GitHub step summary when there is one,
so a red build names its failures without anyone downloading the junit. Exit 0 always: it reports, the test step itself decides.
Usage: failed_tests.py JUNIT_XML FAILED_TXT
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def failures(junit: Path) -> list[str]:
    out = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is not None:
            why = " ".join((bad.get("message") or "").split())[:160]
            out.append(f"{case.get('classname', '')}::{case.get('name', '')}  [{bad.tag}] {why}")
    return out


def main(argv: list[str]) -> int:
    junit, target = Path(argv[1]), Path(argv[2])
    if not junit.exists():
        target.write_text("no junit file: the suite did not finish (see pytest.log)\n")
        print(target.read_text().strip())
        return 0
    found = failures(junit)
    target.write_text("".join(f"{f}\n" for f in found))
    if found:
        print(f"{len(found)} test(s) failed or errored:")
        for f in found:
            print("  -", f)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"### {len(found)} failed or errored test(s)\n\n" + "".join(f"- `{f}`\n" for f in found[:200]))
    else:
        print("no failed or errored tests")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
