#!/usr/bin/env bash
# Download the pinned binaries the gate uses into .ci-tools/bin (git-ignored), each verified against a checksum written here. Nothing is installed system-wide.
#   tools/ci/bootstrap_tools.sh           actionlint, shellcheck, gitleaks
#   tools/ci/bootstrap_tools.sh --act     also nektos/act (to run workflow jobs locally)
# zizmor, uv and pip-audit are Python packages: pip install -r tools/ci/requirements-ci.txt (and requirements.lock).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BIN="${CI_TOOLS:-$ROOT/.ci-tools}/bin"
mkdir -p "$BIN"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

fetch() {  # fetch NAME URL SHA256 MEMBER...  (a tarball: only the named members are extracted)
  local name="$1" url="$2" sum="$3"; shift 3
  if [ -x "$BIN/$name" ]; then return 0; fi
  curl -fsSL --retry 6 --retry-delay 10 --retry-all-errors --retry-max-time 300 -o "$tmp/$name.pkg" "$url"
  echo "$sum  $tmp/$name.pkg" | sha256sum -c - >/dev/null || { echo "checksum mismatch for $name" >&2; exit 1; }
  mkdir -p "$tmp/x-$name"; tar -xf "$tmp/$name.pkg" -C "$tmp/x-$name"
  cp "$(find "$tmp/x-$name" -type f -name "$1" | head -1)" "$BIN/$name"; chmod +x "$BIN/$name"
}

fetch actionlint https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz 8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8 actionlint
fetch shellcheck https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz 8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198 shellcheck
fetch gitleaks https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb gitleaks
if [ "${1:-}" = "--act" ]; then
  fetch act https://github.com/nektos/act/releases/download/v0.2.97/act_Linux_x86_64.tar.gz 67b4976a56f33ea7870b798912e738ec44eef38e7578ae7b532f2def1c73a5ad act
fi
echo "tools in $BIN: $(ls "$BIN" | tr '\n' ' ')"
