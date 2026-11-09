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
| CI-1 tests that vanish without a skip (a `collect_ignore` line, a deleted file) | `tools/ci/test_inventory.py`: `tools/ci/test_inventory.txt` lists the tests collected per file; the tests job fails if a file disappears or shrinks ("update tools/ci/test_inventory.txt in the same commit if this removal is intended"), growth is reported, and the static job requires the inventory to equal the tree | `collect_ignore` of one file, and a deleted file, both fail |
| G6 workflow security | top-level `permissions: contents: read`; concurrency; a timeout on every job; every action by commit sha; trivy by image digest; `gitleaks git` and `gitleaks dir` on the tracked files; actionlint, zizmor, shellcheck; `tests/test_ci_guards.py` enforces the first four | an action at `@v4` fails the guard test |
| G7 no negative controls | `tools/ci/mutants.py`: 32 patches that re-introduce real bugs; each named test must PASS without it and FAIL with it; a patch that no longer applies fails ("update this mutant") | a neutered test lets a mutant survive and the gate fails |
| G8 no CD | `release.yml`, `tools/ci/release.sh`, `tools/ci/deploy.sh` (refuses), `tools/ci/post_deploy_smoke.sh` | the stub refuses and never prints a secret (tested) |

## Running it

```sh
python -m venv .venv && . .venv/bin/activate && pip install --require-hashes -r requirements.lock && pip install --no-deps -e .
make ci-tools        # (repeats the two installs above, so it is safe from any starting point; then the fli commit that pyproject documents, checked by check_fli.py) pinned actionlint, shellcheck, gitleaks (checksummed) into .ci-tools/, and zizmor, uv from tools/ci/requirements-ci.txt
make ci              # static, ui, solvers (verify), tests (with the skip guard and clean-tree check), mutants. Goes on after a failing step and reports them all
make ci-full         # ci, then deploy-check (needs Docker) and a 60 s soak
tools/ci/gate.sh [SHA] [--quick]   # the review gate: ONE committed sha in a clean worktree (--quick skips the full suite)
make ci-static | ci-ui | ci-tests | ci-matrix | ci-mutants | ci-deploy-check | ci-soak     # one gate at a time
tools/ci/guard_demos.sh            # watch each guard fire once, in a throwaway worktree
tools/ci/act.sh JOB                # run a workflow job under act, from a clean checkout (never the working tree: act prints an unparsable .env in full)
```
Logs, junit and coverage go to `$CI_OUT` (default `/tmp/todoh-ci-out-<hash of the checkout path>`: one per checkout, so a review gate in a clean clone and a gate in your working tree never overwrite each other's files, which did happen once on a shared default), never into the tree. Two test runs in one checkout are refused (a lock). Differences between a laptop and CI are named, not hidden: on a laptop the solver commits are reported (not failed) when they are an older local build (`CI_STRICT_SOLVERS=1` forces it), the clean-tree check blames only changes made while the gate ran, and the mutants run against `HEAD` (commit first).

## Jobs (`.github/workflows/ci.yml`; a `push`, a `pull_request`, or called by `nightly` and `release`)

| Job (check name) | Does | Calls |
|---|---|---|
| `static` | actionlint, zizmor, shellcheck, `make lint`, bandit (no medium or high), pip-audit, lock drift, gitleaks history and tracked files, `.env` and run-time data not tracked, launch docs tracked, borrowed art not tracked, local actions tracked, provenance tests | `make ci-static` |
| `ui` | `npm ci`, vitest, `vite build`, `git diff` of `eventmate/web/static` | `make ci-ui` |
| `solvers` | build FD and VAL at the pinned commits, verify, cache (key = hash of the recipe), upload as an artefact | `tools/ci/solvers.sh` |
| `tests (py3.12)`, `tests (py3.13)`, `tests (py3.14)` | (a red run names its failed and errored node ids in the log, the step summary and `failed.txt`, an artefact) locked install (with the serve and flights extras), nginx, openssl, Chromium and Firefox; the suite with `-rs` and a junit file; the skip guard; the clean-tree check; coverage gate (94) on 3.14 only | `make ci-tests` |
| `mutants` | the 32 mutants (needs the browsers: two kill via the hosted journey) | `make ci-mutants` |
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

## Every supported Python
pyproject says Python >=3.12 and CI tests 3.12, 3.13 and 3.14, but a laptop gate that only ran the interpreter in its venv missed a privacy test that fails on 3.12 (an older SQLite keeps a deleted row's bytes unless `secure_delete` is on). So `make ci` also runs `make ci-matrix` (`tools/ci/matrix.sh`): the same tests gate (tools check, test inventory, the suite, skip guard, clean tree) on every version in `MATRIX_VERSIONS` (default `3.12 3.13 3.14`) except the one the main interpreter already ran in full. Each version gets its own uv venv built from `requirements.lock`, the package and the documented fli commit, and its own output directory (`$CI_OUT/py3.12`, ...). A version uv cannot provide is a **failure**; on an offline machine `MATRIX_SKIP_MISSING=1` turns it into a loud `NOT RUN` line and the summary says which versions the gate did not cover. Coverage is measured on the newest version only, as in CI. `tests/test_ci_guards.py` checks that the local and the CI matrix are the same versions.

## Flaky upstreams
The pinned tool downloads (`bootstrap_tools.sh`) retry for up to five minutes (`--retry 6 --retry-delay 10 --retry-all-errors --retry-max-time 300`: a GitHub release CDN returned HTTP 504 on every default retry once), and the workflows cache `.ci-tools` keyed on the hash of that script, so a blip costs one run at most. When the tools are missing `require_tools.sh` still refuses to run the suite ("missing: ...") rather than letting it skip.

## CD (nothing public, no deploy)
On a tag `v*`, `release.yml` runs the whole gate, then builds the image once and exports `todoh-<tag>.tar.gz` (docker save), `todoh-<tag>.sbom.cdx.json` (CycloneDX, by trivy pinned by digest) and `SHA256SUMS`, attaches build provenance (`actions/attest-build-provenance`; the job has `id-token: write` and `attestations: write` and nothing else beyond `contents: read`; **only when the repository is public**: on a private repository attestations need GitHub Enterprise Cloud and the step would fail every release, so it is skipped with a notice), and uploads the artefacts. The `deploy` job runs only when the repository variable `DEPLOY_ENABLED` is `true`, then waits for approval of the `production` environment, then calls `tools/ci/deploy.sh`, which **refuses** (exit 1) until a host is chosen (`docs/HOSTING_PLAN.md`). Placeholder secrets, set by the owner and never printed: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`. After a deploy, `tools/ci/post_deploy_smoke.sh BASE_URL HOST` checks `/healthz`, `/readyz`, that another Host name is refused, that the owner route is hidden (and, with `OWNER_TOKEN`, that it answers); it runs the same against a local compose stack: `make post-deploy-smoke URL=http://127.0.0.1:8000 HOST=todoh.example`.

## Known limits and differences
- Not run on GitHub: runner differences (Chromium's path, Landlock, nginx from apt, `apt` package names, the Arm/x86 split) are untested. The first run may need small fixes.
- `requirements.lock` pins PyPI `flights==0.9.0`; the pyproject `flights` extra names a git commit of fli (0.10.0). The gate leaves that one line out of the lock comparison and the tests run against what the lock installs. Which one is meant is the owner's to settle.
- The solver commits are strict only where `CI` is set. A laptop's local solver build here is Fast Downward `ed3d389`, not the pinned `1eef26b2`.
- `actions/attest-build-provenance`, the cache and artifact actions are pinned by sha but were not executed here. `act` does not run docker-in-docker jobs, the browsers, or the long suite.
- Equivalent mutants: if a refactor makes two rules cover each other, a mutant that switches off one will survive; the fix is a combined patch (as above).

## Owner's first-run checklist (nothing has run on GitHub yet; pushing is the owner's call)
1. In branch protection for `main`, require the check `ci-gate` (it aggregates every job; add the individual job names if you want them listed).
2. Expect the skip guard to fail the first run if a runner cannot set up Landlock, bubblewrap or seccomp (the "this machine cannot set up the ... level" skips in `tests/test_documents.py`). Decide on purpose: fix the runner (a self-hosted one, or a kernel that has them), or add those exact tests to `tools/ci/allowed_skips.txt` with a reason. Do not loosen the guard.
3. Create the `production` environment with required reviewers, and add the `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY` secrets and the `DEPLOY_ENABLED` variable only once a host is chosen. Until then `tools/ci/deploy.sh` refuses.
4. Rotate the keys `act` printed into an AI session's transcript (NVIDIA, OpenRouter, Google Places, Brave, Langfuse, the SMTP app password): `docs/BLOCKED.md`.

## Proof, run on this machine (commands and results)
All on this machine, at the commit named in the READY message (`git rev-parse --short HEAD`), 2026-11-09. Nothing was run on GitHub.

**Static tools.** `actionlint` (1.7.12): clean. `zizmor --offline --min-severity medium .github` (1.32.0): no findings (at lower severity it only suggests GitHub's self-repository syntax for the local `uses: ./...`). `shellcheck --severity=warning` (0.11.0) on every tracked shell script: clean.

**`COVERAGE=1 make ci`, end to end, exit 0:** static (16 checks) PASS; ui PASS (`npm ci`, vitest, `vite build`, no change in `eventmate/web/static`); solvers verified (a WARNING that the local Fast Downward is `ed3d389`, not the pinned `1eef26b2`: strict only in CI); tests PASS: require-tools ok, **5249 passed, 4 skipped**, skip guard "4 skip(s), all on the allow-list", clean-tree ok, **coverage 95.27% (gate 94)**; mutants: **all 26 killed**.

**`tools/ci/deploy_check.sh` (Docker):** the proxy/TLS and smuggling tests passed and did not skip; the image built; `tools/check_image.sh` all ok (read-only, all capabilities dropped, no-new-privileges, Landlock, FD+VAL validated a plan inside, the leak check); the leak control FAILED the planted image and named both planted files, as it must; trivy (pinned by digest) found no HIGH or CRITICAL with a fix.

**`tools/ci/soak.sh` (60 s, 20 visitors, 10 on `/plan`):** 1181 requests, all 2xx, no failures, RSS 218 to 222 MB. **`tools/ci/release.sh v0.0.0-ci`:** `todoh-v0.0.0-ci.tar.gz` (docker save), a CycloneDX 1.6 SBOM (157 components) and `SHA256SUMS` (both verified OK); nothing pushed. **`tools/ci/post_deploy_smoke.sh`** against a local container in shared mode: five checks passed; with `OWNER_TOKEN` the owner route also passed; against a wrong Host name it FAILED.

**Under `act` (0.2.97, image `catthehacker/ubuntu:act-latest`, from a clean clone):** the `ui` job: **succeeded**, `ui-reproduces ok`. The `static` job, including the composite `prepare` action (hash-pinned install, the checksummed tool downloads): **succeeded**, all 16 checks ok. The `solvers` job: built Fast Downward and VAL at the pinned commits, verified both and packed them (about 3 minutes with the cache action), and **failed only at `actions/upload-artifact`** ("Unable to get the ACTIONS_RUNTIME_TOKEN": `act` has no artifact service). **Not run under `act`**: `tests`, `mutants`, `deploy-check` and `soak` (browsers, docker-in-docker, a 12-minute suite); they were run directly, above.

**Each guard fires once (`tools/ci/guard_demos.sh`, in a throwaway worktree of HEAD):**
```
skip-guard       caught: tests.test_zz_demo_skip::test_needs_a_tool_that_is_missing: nginx is not installed here
ui-diff          caught: ui-reproduces FAIL: the build changed eventmate/web/static (plan.js edited by hand)
clean-tree       caught: ?? eventmate/left_behind_by_a_test.json
mutant-survives  caught: SURVIVED per-plan-cap-off (the cap test was neutered)
env-tracked      caught
unpinned-action  caught: tests/test_ci_guards.py (actions/checkout@v4)
borrowed-art     caught
act-bare-with-actrc     caught: a bare `act` with an unparsable .env printed no value
act-bare-without-actrc  control: without .actrc the same run DOES print the value
act-wrapper-refuses-near-a-env  caught
```
Two more were seen by accident: the clean-tree guard failed my own first `make ci` because I added a file while the suite ran, and the lock-drift step failed against today's upstream releases before it was changed to compare against the existing pins.

**An incident the gate now prevents.** `act` loads `./.env` by default and, when it cannot parse it, prints it in full; that put real keys into an AI session's transcript (rotation is an owner step: `docs/BLOCKED.md`). `act` also ignores `--env-file /dev/null` from a project `.actrc` ("the path must stay inside the project"), so `.actrc` points at four empty tracked files in `.github/` (`act.env`, `act.secrets`, `act.vars`, `act.input`); the static gate checks them; `tools/ci/act.sh` refuses to start with a `.env` in its directory or any parent up to `$HOME`, and runs `act` only from a clean clone.
