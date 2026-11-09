# CI/CD: one gate, the same on a laptop and in CI

`make ci` runs exactly what the workflows run, step by step: every job calls a script in `tools/ci/`. The gate is the reviewer's gate (`gate.sh`, ported) plus the guards that would have caught each finding of the first two programs. **Nothing here has been run on GitHub**: it was not pushed. It was run locally, and under `act` where noted below. The first real run is the owner's.

## What each gap was, and what catches it now

| Gap | Caught by | Shown to fire (`tools/ci/guard_demos.sh`, `tests/test_ci_guards.py`) |
|---|---|---|
| G1 silent skips (R-018: 40 tests skipped for a missing extra) | `tools/ci/require_tools.sh` (nginx, openssl, Chromium, Firefox, fli must exist, or the job fails) and `tools/ci/skip_guard.py` (any skip not on `tools/ci/allowed_skips.txt` fails, with its reason; today exactly the four opt-in tests) | an unexpected skip is reported and fails |
| G2 `/plan` build drift (R-006, L1) | `tools/ci/ui.sh`: `npm ci`, vitest, `vite build`, then no change in `eventmate/web/static` | a hand-edited `plan.js` fails |
| G3 tests writing into the repository | `tools/ci/clean_tree.sh` (a `git status` snapshot before, verify after; in CI the snapshot is empty) | a file left behind fails; it fired on my own file added mid-run |
| G4 a weaker container check | `tools/ci/deploy_check.sh`: the proxy/TLS and smuggling tests (they may not skip), `tools/check_image.sh` (read-only, all capabilities dropped, no-new-privileges, Landlock, FD+VAL validated a plan inside the image, the leak check), and `tools/ci/leak_control.sh` (a planted file must make the leak check FAIL) | the control builds FROM the image with `plans/leak.jsonl` and a story image planted |
| G5 unpinned solvers | `tools/ci/solvers.sh`: Fast Downward `1eef26b2cbf5…` and VAL `3c7a1f330bda…` verified at their commits (strict in CI), built with the CMake flag; the cache key is the hash of the recipe | a different commit fails in CI |
| G6 workflow security | top-level `permissions: contents: read`; concurrency; a timeout on every job; every action by commit sha; trivy by image digest; `gitleaks git` and `gitleaks dir` on the tracked files; actionlint, zizmor, shellcheck; `tests/test_ci_guards.py` enforces the first four | an action at `@v4` fails the guard test |
| G7 no negative controls | `tools/ci/mutants.py`: 26 patches that re-introduce real bugs; each named test must PASS without it and FAIL with it; a patch that no longer applies fails ("update this mutant") | a neutered test lets a mutant survive and the gate fails |
| G8 no CD | `release.yml`, `tools/ci/release.sh`, `tools/ci/deploy.sh` (refuses), `tools/ci/post_deploy_smoke.sh` | the stub refuses and never prints a secret (tested) |

## Running it

```sh
python -m venv .venv && . .venv/bin/activate && pip install --require-hashes -r requirements.lock && pip install --no-deps -e .
make ci-tools        # pinned actionlint, shellcheck, gitleaks (checksummed) into .ci-tools/, and zizmor, uv from tools/ci/requirements-ci.txt
make ci              # static, ui, solvers (verify), tests (with the skip guard and clean-tree check), mutants. Goes on after a failing step and reports them all
make ci-full         # ci, then deploy-check (needs Docker) and a 60 s soak
tools/ci/gate.sh [SHA] [--quick]   # the review gate: ONE committed sha in a clean worktree (--quick skips the full suite)
make ci-static | ci-ui | ci-tests | ci-mutants | ci-deploy-check | ci-soak     # one gate at a time
tools/ci/guard_demos.sh            # watch each guard fire once, in a throwaway worktree
tools/ci/act.sh JOB                # run a workflow job under act, from a clean checkout (never the working tree: act prints an unparsable .env in full)
```
Logs, junit and coverage go to `$CI_OUT` (default `/tmp/todoh-ci-out`), never into the tree. Differences between a laptop and CI are named, not hidden: on a laptop the solver commits are reported (not failed) when they are an older local build (`CI_STRICT_SOLVERS=1` forces it), the clean-tree check blames only changes made while the gate ran, and the mutants run against `HEAD` (commit first).

## Jobs (`.github/workflows/ci.yml`; a `push`, a `pull_request`, or called by `nightly` and `release`)

| Job (check name) | Does | Calls |
|---|---|---|
| `static` | actionlint, zizmor, shellcheck, `make lint`, bandit (no medium or high), pip-audit, lock drift, gitleaks history and tracked files, `.env` and run-time data not tracked, launch docs tracked, borrowed art not tracked, local actions tracked, provenance tests | `make ci-static` |
| `ui` | `npm ci`, vitest, `vite build`, `git diff` of `eventmate/web/static` | `make ci-ui` |
| `solvers` | build FD and VAL at the pinned commits, verify, cache (key = hash of the recipe), upload as an artefact | `tools/ci/solvers.sh` |
| `tests (py3.12)`, `tests (py3.13)`, `tests (py3.14)` | locked install (with the serve and flights extras), nginx, openssl, Chromium and Firefox; the suite with `-rs` and a junit file; the skip guard; the clean-tree check; coverage gate (94) on 3.14 only | `make ci-tests` |
| `mutants` | the 26 mutants (needs the browsers: two kill via the hosted journey) | `make ci-mutants` |
| `deploy-check` | `deploy_check.sh` (proxy, smuggling, check_image, leak control, trivy HIGH/CRITICAL with a fix) | `make ci-deploy-check` |
| `soak` | 60 s, 20 visitors, half on `/plan` | `make ci-soak` |
| `ci-gate` | succeeds only if every job above succeeded (a skipped or cancelled one is not a pass) | n/a |

`nightly.yml` (02:17 UTC, and by hand): the whole gate again and the 1800 s soak. `live-evals.yml`: by hand only, never on push, with the call cap.

### Branch protection (the owner sets it on GitHub)
Require status checks to pass before merging, with these exact check names, and require branches to be up to date: **`ci-gate`** (it depends on all the others, so it alone is sufficient), and, to see which one failed in the merge box: `static`, `ui`, `solvers`, `tests (py3.12)`, `tests (py3.13)`, `tests (py3.14)`, `mutants`, `deploy-check`, `soak`. Also: require a pull request, dismiss stale approvals, no force pushes, no deletions. Create an environment named **`production`** with required reviewers (a person approves a deploy), and leave the repository variable `DEPLOY_ENABLED` unset.

## The skip guard
`tools/ci/allowed_skips.txt` is the exact set of opt-in tests that cannot run in CI (Docling twice, live flights, the voice engine). A skip for a missing tool is never added: install the tool. **A risk to decide on the first real run:** `tests/test_documents.py` has tests that skip with "this machine cannot set up the ... level" (Landlock, bubblewrap, seccomp, x32). They did not skip here; a runner whose kernel lacks Landlock may skip them, and the guard will then fail with the reason. Either the runner is changed or a line is added on purpose, with the reason it cannot run there.

## The mutants
`tools/ci/mutants/manifest.txt` lists `name | patch | tests that must fail | the real bug`. To add one: in a throwaway worktree make the break, `git diff -- <file> > tools/ci/mutants/<name>.patch`, add a line. The runner needs the named tests to pass on `HEAD` first (otherwise a failure means nothing), then to fail with the patch (pytest exit 1; an import error is "broken", not "killed"). An equivalent mutant is a finding about the code, not about the gate: the bare-STOP mutant survived once because the first-line rule (R-015) now covers the bare word, so it was changed to switch off both.

## CD (nothing public, no deploy)
On a tag `v*`, `release.yml` runs the whole gate, then builds the image once and exports `todoh-<tag>.tar.gz` (docker save), `todoh-<tag>.sbom.cdx.json` (CycloneDX, by trivy pinned by digest) and `SHA256SUMS`, attaches build provenance (`actions/attest-build-provenance`; the job has `id-token: write` and `attestations: write` and nothing else beyond `contents: read`; attestations on a private repository need a plan that supports them), and uploads the artefacts. The `deploy` job runs only when the repository variable `DEPLOY_ENABLED` is `true`, then waits for approval of the `production` environment, then calls `tools/ci/deploy.sh`, which **refuses** (exit 1) until a host is chosen (`docs/HOSTING_PLAN.md`). Placeholder secrets, set by the owner and never printed: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`. After a deploy, `tools/ci/post_deploy_smoke.sh BASE_URL HOST` checks `/healthz`, `/readyz`, that another Host name is refused, that the owner route is hidden (and, with `OWNER_TOKEN`, that it answers); it runs the same against a local compose stack: `make post-deploy-smoke URL=http://127.0.0.1:8000 HOST=todoh.example`.

## Known limits and differences
- Not run on GitHub: runner differences (Chromium's path, Landlock, nginx from apt, `apt` package names, the Arm/x86 split) are untested. The first run may need small fixes.
- `requirements.lock` pins PyPI `flights==0.9.0`; the pyproject `flights` extra names a git commit of fli (0.10.0). The gate leaves that one line out of the lock comparison and the tests run against what the lock installs. Which one is meant is the owner's to settle.
- The solver commits are strict only where `CI` is set. A laptop's local solver build here is Fast Downward `ed3d389`, not the pinned `1eef26b2`.
- `actions/attest-build-provenance`, the cache and artifact actions are pinned by sha but were not executed here. `act` does not run docker-in-docker jobs, the browsers, or the long suite.
- Equivalent mutants: if a refactor makes two rules cover each other, a mutant that switches off one will survive; the fix is a combined patch (as above).

## Proof, run on this machine (commands and results)
PROOF_PLACEHOLDER
