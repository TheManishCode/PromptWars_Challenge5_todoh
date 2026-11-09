#!/usr/bin/env bash
# Run a workflow job locally with nektos/act, from a CLEAN export of HEAD and with no env file: act reads ./.env by default and prints it when it cannot parse it, which puts real keys in a log.
# That happened once (an unparsable .env was echoed in full); this wrapper never lets act see the repository's .env.
#   tools/ci/act.sh JOB [WORKFLOW_FILE]      e.g. tools/ci/act.sh ui   or   tools/ci/act.sh static ci.yml
# Heavy jobs (the full suite, browsers, docker-in-docker) are not meant to run under act: see docs/CI.md for which did.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
JOB="${1:?usage: act.sh JOB [workflow.yml]}"; WF="${2:-ci.yml}"
# Refuse to start if a .env exists where act could find it: here, or in any parent directory up to $HOME. (Only its existence is looked at; it is never read.) Run from a directory that holds none:
#   (cd /tmp && /path/to/repo/tools/ci/act.sh ui)
dir="$PWD"
while :; do
  [ ! -e "$dir/.env" ] || { echo "refusing to start: $dir/.env exists. act reads a .env it finds and prints it in full when it cannot parse it. Run this from a directory with no .env, e.g. (cd /tmp && $0 $JOB)" >&2; exit 1; }
  [ "$dir" = "$HOME" ] || [ "$dir" = / ] && break
  dir="$(dirname "$dir")"
done
ACT="$ROOT/.ci-tools/bin/act"; [ -x "$ACT" ] || ACT="$(command -v act)" || { echo "act is missing: tools/ci/bootstrap_tools.sh --act"; exit 2; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git clone -q "$ROOT" "$tmp/wt" && git -C "$tmp/wt" checkout -q "$(git -C "$ROOT" rev-parse HEAD)"  # a real checkout of HEAD, as a runner has: the scripts use git
[ ! -e "$tmp/wt/.env" ] || { echo "refusing: a .env is tracked"; exit 1; }
cd "$tmp/wt" || exit 2
# the clone is under TMPDIR, outside $HOME: check its own parents too
[ ! -e "$tmp/.env" ] && [ ! -e "$tmp/../.env" ] || { echo "refusing: a .env sits above the scratch checkout" >&2; exit 1; }
IMAGE="${ACT_IMAGE:-catthehacker/ubuntu:act-latest}"
exec "$ACT" push -j "$JOB" -W ".github/workflows/$WF" --env-file /dev/null --secret-file /dev/null --var-file /dev/null --input-file /dev/null -P "ubuntu-latest=$IMAGE" --pull=false ${ACT_ARGS:-}
