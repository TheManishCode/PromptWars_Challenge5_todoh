#!/usr/bin/env bash
# The negative control of the image leak check (R-007: it once said "ok" because it looked in a directory that does not exist in the image). Build FROM the image with a planted run-time data file
# and a planted file in static/, in the installed package; tools/check_image.sh must FAIL and name both. If it passes, the check cannot see a leak.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
IMAGE="${TODOH_IMAGE:?TODOH_IMAGE is the image to plant a leak in}"
pkg="$(docker run --rm --entrypoint python "$IMAGE" -c 'import eventmate,os;print(os.path.dirname(eventmate.__file__))')"
work="$(mktemp -d)"; trap 'rm -rf "$work"; docker rmi -f "$IMAGE-leaky" >/dev/null 2>&1' EXIT
cat > "$work/Dockerfile" <<EOF
FROM $IMAGE
USER root
RUN mkdir -p $pkg/plans && echo x > $pkg/plans/leak.jsonl && echo x > $pkg/web/static/plan-story-knot.webp
USER 10001
EOF
docker build -q -t "$IMAGE-leaky" "$work" >/dev/null
if out="$(TODOH_IMAGE="$IMAGE-leaky" "$ROOT/tools/check_image.sh" 2>&1)"; then echo "check_image.sh PASSED an image with planted leaks: its leak check is blind"; exit 1; fi
echo "$out" | grep -q "the image contains: .*plans" && echo "$out" | grep -q "plan-story-knot.webp" || { echo "check_image.sh failed, but not by naming the planted files:"; echo "$out" | tail -n 8; exit 1; }
echo "the leak check failed on the planted image and named both planted files"
