#!/usr/bin/env bash
# CD, the part that publishes nothing: build the image once, export it, list what is in it, and checksum it. Run by the release workflow on a tag `v*`, or by hand: tools/ci/release.sh v1.2.3
# Writes to ${DIST:-$CI_OUT/dist}: todoh-<tag>.tar.gz (docker save), todoh-<tag>.sbom.cdx.json (CycloneDX, by trivy), SHA256SUMS. Pushes nothing, signs nothing, deploys nothing.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TAG="${1:?usage: release.sh <tag, like v1.2.3>}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || { echo "a release tag looks like v1.2.3"; exit 2; }
TRIVY="aquasec/trivy@sha256:a22415a38938a56c379387a8163fcb0ce38b10ace73e593475d3658d578b2436"
DIST="${DIST:-$CI_OUT/dist}"; mkdir -p "$DIST"
IMAGE="todoh:$TAG"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -q --build-arg BUILD_JOBS="${BUILD_JOBS:-4}" -t "$IMAGE" . >/dev/null
docker save "$IMAGE" | gzip -n > "$DIST/todoh-$TAG.tar.gz"
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$DIST:/out" "$TRIVY" image --format cyclonedx --output "/out/todoh-$TAG.sbom.cdx.json" "$IMAGE" >/dev/null
( cd "$DIST" && sha256sum "todoh-$TAG.tar.gz" "todoh-$TAG.sbom.cdx.json" > SHA256SUMS && sha256sum -c SHA256SUMS )
echo "release artefacts for $TAG in $DIST (nothing was pushed or deployed)"
