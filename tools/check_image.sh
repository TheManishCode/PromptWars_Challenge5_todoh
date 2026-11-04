#!/usr/bin/env bash
# Build the image and check what a deployment depends on, the way the compose file runs it (non-root, all capabilities dropped, no-new-privileges, read-only root).
# Repeatable, needs Docker and network for the build. Not a substitute for a real host: no TLS, no reverse proxy (tests/test_proxy_deploy.py covers that), no real credentials.
#   tools/check_image.sh            build and check
#   TODOH_IMAGE=name tools/check_image.sh   check an image that is already built (skips the build)
set -u
cd "$(dirname "$0")/.."
IMAGE="${TODOH_IMAGE:-todoh-check}"
FAIL=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAIL=1; }
run()  { docker run --rm --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true --read-only --tmpfs /tmp "$@"; }

if [ -z "${TODOH_IMAGE:-}" ]; then
  echo "building $IMAGE"; docker build -q --build-arg BUILD_JOBS="${BUILD_JOBS:-4}" -t "$IMAGE" . >/dev/null || { bad "the image did not build"; exit 1; }
fi

echo "licences travel with the tools"
run --entrypoint sh "$IMAGE" -c 'test -f /opt/solvers/NOTICE.txt && test -f /opt/solvers/downward/LICENSE.md && test -f /opt/solvers/VAL/LICENSE && test -d /opt/solvers/downward/src' && ok "NOTICE, both licence texts and Fast Downward's source are in the image" || bad "a licence file or the Fast Downward source is missing"

echo "shared mode refuses to start without its settings"
OUT=$(run -e EVENTMATE_MODE=shared "$IMAGE" python -m eventmate.web --host 127.0.0.1 --port 8000 --allow-remote 2>&1); CODE=$?
[ "$CODE" -ne 0 ] && echo "$OUT" | grep -qi "EVENTMATE_SECRET" && ok "it refused and named EVENTMATE_SECRET" || bad "it started, or did not say what was missing (exit $CODE)"

echo "the file reader is shut in as far as shared mode needs"
LEVEL=$(run --entrypoint python "$IMAGE" -c "from eventmate.documents import sandbox; print(sandbox.isolation_level())" 2>&1 | tail -1)
case "$LEVEL" in contained|confined) ok "isolation level: $LEVEL";; *) bad "isolation level is '$LEVEL': shared mode would refuse to start here";; esac

echo "the planner works with the pinned tools"
PLAN=$(run --entrypoint python "$IMAGE" -c "
from datetime import date; import tempfile, pathlib
from eventmate.service import EventMate
r = pathlib.Path(tempfile.mkdtemp()); em = EventMate(log_dir=r/'l', plan_dir=r/'p', run_id='img')
o = em.plan('Plan a 120-person engagement party in Pune on December 12 under INR 200,000. Vegetarian catering, indoor venue, photographer.', today=date(2026,10,30))
print(o.status, [c.detail for c in o.plan.validation.checks if c.name=='symbolic_feasibility'][0] if o.plan else '')" 2>&1 | tail -1)
echo "$PLAN" | grep -q "^planned.*VAL validated" && ok "$PLAN" || bad "no plan validated by VAL: $PLAN"

echo "a shared-mode server starts, answers its probes and stops cleanly"
SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40); TOKEN=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
NAME="todoh-check-$$"
docker run -d --name "$NAME" --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true --read-only --tmpfs /tmp -v "$NAME-data:/data" -p 127.0.0.1::8000 \
  -e EVENTMATE_MODE=shared -e EVENTMATE_SECRET="$SECRET" -e EVENTMATE_OWNER_TOKEN="$TOKEN" -e EVENTMATE_TRUSTED_PROXY=172.16.0.0/12 -e EVENTMATE_PUBLIC_URL=https://todoh.example \
  -e EVENTMATE_ENABLE_LLM=0 "$IMAGE" python -m eventmate.web --host 0.0.0.0 --port 8000 --allow-remote --allowed-host todoh.example >/dev/null
PORT=""; for _ in $(seq 1 40); do PORT=$(docker port "$NAME" 8000/tcp 2>/dev/null | head -1 | sed 's/.*://'); [ -n "$PORT" ] && curl -s -o /dev/null -H "Host: todoh.example" "http://127.0.0.1:$PORT/healthz" && break; sleep 0.5; done
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: todoh.example' "http://127.0.0.1:$PORT/healthz")" = "200" ] && ok "/healthz answers 200" || { bad "/healthz did not answer 200"; docker logs "$NAME" 2>&1 | tail -5; }
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: todoh.example' "http://127.0.0.1:$PORT/readyz")" = "200" ] && ok "/readyz answers 200" || bad "/readyz did not answer 200"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' "http://127.0.0.1:$PORT/")" != "200" ] && ok "another Host name is not served" || bad "another Host name was served"
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: todoh.example' "http://127.0.0.1:$PORT/api/owner/status")" = "404" ] && ok "the owner status route is invisible without the token" || bad "the owner status route answered without the token"
START=$(date +%s); docker stop -t 30 "$NAME" >/dev/null; STOPPED=$(( $(date +%s) - START ))
[ "$STOPPED" -lt 25 ] && ok "it stopped in ${STOPPED}s (it drains in-flight requests on SIGTERM)" || bad "it took ${STOPPED}s to stop"
docker rm -f "$NAME" >/dev/null 2>&1; docker volume rm "$NAME-data" >/dev/null 2>&1

[ "$FAIL" -eq 0 ] && echo "all checks passed" || echo "some checks failed"
exit "$FAIL"
