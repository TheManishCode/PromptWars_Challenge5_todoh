#!/usr/bin/env bash
# After a deploy (or against a local compose stack): is it up, is it ready, does it refuse another Host name, is the owner route hidden. Reads nothing secret; prints no token.
#   tools/ci/post_deploy_smoke.sh BASE_URL PUBLIC_HOST   e.g. http://127.0.0.1:8000 todoh.example.com
#   OWNER_TOKEN=...  (optional) also checks the owner status route answers WITH the token
set -uo pipefail
BASE="${1:?usage: post_deploy_smoke.sh BASE_URL PUBLIC_HOST}"; HOST="${2:?public host name}"
fail=0; ok() { printf '  ok    %s\n' "$1"; }; bad() { printf '  FAIL  %s\n' "$1"; fail=1; }
code() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@"; }
[ "$(code -H "Host: $HOST" "$BASE/healthz")" = 200 ] && ok "/healthz answers 200" || bad "/healthz did not answer 200"
[ "$(code -H "Host: $HOST" "$BASE/readyz")" = 200 ] && ok "/readyz answers 200 (storage writable, database open, disk free)" || bad "/readyz did not answer 200"
[ "$(code -H 'Host: evil.example' "$BASE/")" != 200 ] && ok "another Host name is not served" || bad "another Host name was served"
[ "$(code -H "Host: $HOST" "$BASE/api/owner/status")" = 404 ] && ok "the owner route is invisible without the token" || bad "the owner route answered without the token"
[ "$(code -H "Host: $HOST" "$BASE/")" = 200 ] && ok "the front page answers 200" || bad "the front page did not answer 200"
if [ -n "${OWNER_TOKEN:-}" ]; then
  [ "$(code -H "Host: $HOST" -H "X-EventMate-Owner: $OWNER_TOKEN" "$BASE/api/owner/status")" = 200 ] && ok "the owner route answers WITH the token" || bad "the owner route did not answer with the token"
fi
[ "$fail" -eq 0 ] && echo "smoke: all checks passed" || echo "smoke: FAILED"
exit "$fail"
