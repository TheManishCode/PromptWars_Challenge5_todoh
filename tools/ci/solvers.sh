#!/usr/bin/env bash
# Build Fast Downward and VAL at the exact commits the product was tested with (the same ones as the Dockerfile and docs/CONFIGURATION.md) and verify them. Needs cmake g++ make flex bison git.
#   tools/ci/solvers.sh build [DIR]     build into DIR (default solvers/) unless already built and verified
#   tools/ci/solvers.sh verify [DIR]    check the commits and that both binaries run
#   tools/ci/solvers.sh pack|unpack     tar the result (an artifact loses file modes) into/from $CI_OUT/solvers.tar
#   tools/ci/solvers.sh key             a cache key: the hash of this recipe (a change to the recipe is a new key)
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
FD_TAG=release-24.06.1
FD_SHA=1eef26b2cbf599a1894606aa898d9d49e1034cb9
VAL_SHA=3c7a1f330bdab0ba28a4762bb45c3f06c27fb6d4
CMD="${1:-build}"; DIR="${2:-${CI_SOLVERS:-$ROOT/solvers}}"
FD="$DIR/fast-downward-24.06.1"; VAL="$DIR/VAL"

# In CI (CI=true) the commits must be the pinned ones. On a developer's machine the solvers may be an older local build: that is reported, and does not fail, because rebuilding them takes ten minutes
# (CI_STRICT_SOLVERS=1 forces the check; `CI_SOLVERS=$PWD/.ci-solvers tools/ci/solvers.sh build` makes a pinned copy beside it).
STRICT="${CI_STRICT_SOLVERS:-${CI:-}}"
verify() {
  local fd_head val_head; fd_head="$(git -C "$FD" rev-parse HEAD 2>/dev/null)"; val_head="$(git -C "$VAL" rev-parse HEAD 2>/dev/null)"
  if [ "$fd_head" != "$FD_SHA" ]; then if [ -n "$STRICT" ]; then echo "Fast Downward is not at $FD_SHA (it is at ${fd_head:-nothing})"; return 1; else echo "WARNING: Fast Downward is at ${fd_head:0:12}, not the pinned ${FD_SHA:0:12} (CI builds the pinned one)"; fi; fi
  if [ "$val_head" != "$VAL_SHA" ]; then if [ -n "$STRICT" ]; then echo "VAL is not at $VAL_SHA (it is at ${val_head:-nothing})"; return 1; else echo "WARNING: VAL is at ${val_head:0:12}, not the pinned ${VAL_SHA:0:12}"; fi; fi
  [ -f "$FD/fast-downward.py" ] && [ -x "$VAL/build/bin/Validate" ] || { echo "a solver binary is missing"; return 1; }
  "$PY" "$FD/fast-downward.py" --help >/dev/null 2>&1 || { echo "Fast Downward does not run"; return 1; }
  "$VAL/build/bin/Validate" >/dev/null 2>&1; [ $? -le 1 ] || { echo "VAL does not run"; return 1; }
  echo "solvers verified (Fast Downward ${fd_head:0:12}, VAL ${val_head:0:12}; pinned ${FD_SHA:0:12} and ${VAL_SHA:0:12})"
}
case "$CMD" in
  key) sha256sum "${BASH_SOURCE[0]}" | cut -c1-16;;
  verify) verify;;
  build)
    if verify >/dev/null 2>&1; then verify; exit 0; fi
    mkdir -p "$DIR"; rm -rf "$FD" "$VAL"
    git clone --depth 1 --branch "$FD_TAG" https://github.com/aibasel/downward.git "$FD"
    [ "$(git -C "$FD" rev-parse HEAD)" = "$FD_SHA" ] || { echo "release-24.06.1 is not at the pinned commit: refusing"; exit 1; }
    (cd "$FD" && "$PY" build.py -j"$(nproc)")
    git init -q "$VAL"; git -C "$VAL" remote add origin https://github.com/KCL-Planning/VAL.git
    git -C "$VAL" fetch -q --depth 1 origin "$VAL_SHA"; git -C "$VAL" checkout -q FETCH_HEAD
    [ "$(git -C "$VAL" rev-parse HEAD)" = "$VAL_SHA" ] || { echo "VAL is not at the pinned commit: refusing"; exit 1; }
    mkdir -p "$VAL/build"; (cd "$VAL/build" && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 >/dev/null && make -j"$(nproc)" >/dev/null)
    verify;;
  pack) tar -cf "$CI_OUT/solvers.tar" -C "$(dirname "$DIR")" "$(basename "$DIR")"; echo "$CI_OUT/solvers.tar";;
  unpack) tar -xf "$CI_OUT/solvers.tar" -C "$(dirname "$DIR")"; verify;;
  *) echo "usage: solvers.sh build|verify|pack|unpack|key [DIR]"; exit 2;;
esac
