#!/usr/bin/env python3
"""fli the way the app uses it (CI-5). `import fli` succeeds on a half-installed library (fli's own dependencies, babel and others, missing), and then tests/test_travel.py fails or skips. This imports every
fli module that eventmate/integrations/travel.py imports, and the file's `from fli... import NAMES` names too. Prints the problems and the fix; exit 1 if any.
Usage: check_fli.py [TRAVEL_PY]
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

FIX = "make ci-tools (it installs requirements.lock first, then the pyproject flights extra); or: pip install --require-hashes -r requirements.lock && tools/ci/install_flights.sh"


def main(argv: list[str]) -> int:
    travel = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[2] / "eventmate/integrations/travel.py"
    wanted = sorted(set(re.findall(r"^\s*from (fli[\w.]*) import ([\w, ]+)$", travel.read_text(encoding="utf-8"), re.M)))
    if not wanted:
        print(f"no `from fli... import` found in {travel}: this check is out of date")
        return 1
    bad = []
    for module, names in wanted:
        try:
            mod = importlib.import_module(module)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{module} ({type(e).__name__}: {str(e)[:80]})")
            continue
        bad += [f"{module}.{n} (missing)" for n in (x.strip() for x in names.split(",")) if n and not hasattr(mod, n)]
    if bad:
        print("fli is not usable the way eventmate/integrations/travel.py uses it: " + "; ".join(bad) + f"\n  fix: {FIX}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
