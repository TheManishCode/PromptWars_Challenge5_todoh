#!/usr/bin/env bash
# What can be checked before a real host: the reference proxy in front of the real application with TLS, the smuggling attacks, the container exactly as compose runs it (read-only, all capabilities
# dropped, no-new-privileges: tools/check_image.sh), the image's leak check shown to FAIL when something is planted, and a vulnerability scan of the image (trivy, pinned by digest).
#   TODOH_IMAGE=name  reuse an image that is already built
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
command -v docker >/dev/null || { echo "docker is needed"; exit 2; }
TRIVY="aquasec/trivy@sha256:a22415a38938a56c379387a8163fcb0ce38b10ace73e593475d3658d578b2436"  # 0.65.0, by digest: a tag can be moved
IMAGE="${TODOH_IMAGE:-todoh-ci:$(git rev-parse --short HEAD)}"
export TODOH_IMAGE="$IMAGE"

run proxy-tls-and-smuggling-tests "$PY" -m pytest -q -p no:cacheprovider -rs "--junitxml=$CI_OUT/proxy-junit.xml" tests/test_proxy_deploy.py tests/pentest/test_proxy_smuggling.py
: > "$CI_OUT/no_skips.txt"  # these tests need nginx and openssl: not one of them may skip
run proxy-tests-did-not-skip "$PY" tools/ci/skip_guard.py "$CI_OUT/proxy-junit.xml" "$CI_OUT/no_skips.txt"
docker image inspect "$IMAGE" >/dev/null 2>&1 || run image-build docker build -q --build-arg BUILD_JOBS="${BUILD_JOBS:-4}" -t "$IMAGE" .
run check-image tools/check_image.sh
run image-leak-check-fails-when-it-should tools/ci/leak_control.sh
run trivy-high-critical-with-a-fix docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$TRIVY" image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed "$IMAGE"
finish deploy-check
