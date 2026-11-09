#!/usr/bin/env bash
# Install the flights library the way pyproject.toml documents it (the `flights` extra: a git commit of fli 0.10.0), on top of the hash-pinned lock, without touching any locked package.
# requirements.lock itself pins PyPI flights 0.9.0, which the pyproject comment says gets no fares: the tests must run against the documented one (docs/DECISIONS.md D-023).
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
spec="$("$PY" - <<'PY'
import tomllib
print(tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]["flights"][0])
PY
)"
"$PY" -m pip install -q --no-deps "$spec" && echo "installed: $spec"
