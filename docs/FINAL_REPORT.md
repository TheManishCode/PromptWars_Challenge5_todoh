# Final report: to;doh launch readiness

2026-11-08. builder-todoh (524791) and reviewer-todoh (7f7b64), method in `docs/LAUNCH_PLAN.md`. Baseline `605a917`; end state `main` at tag `m5-done` (`3882561`) plus this report. Nothing was pushed: `origin` is GitHub and counts as public exposure. "Live" (a host, DNS, a certificate, a real mailbox) is the owner's step.

## 1. What was built, per milestone

| Milestone | Commits | Tag | What |
|---|---|---|---|
| L0 land and go green | `fad36e1` `576cd54` `9fe5b44` `aa733ef` `c7b17eb` | `m0-done` | The session's work as feature-grouped commits (lint and test hygiene; shared quotes; the `/plan` page; build hygiene; docs). ruff, pyflakes and bandit clean; the approvals race test no longer expires with the calendar (R-004). `.dockerignore` keeps owner data out of the image (R-003). |
| L1 IP clearance | `5919bb2` `50bf2a4` `0e8bcaa` `6f1d7d5` | `m1-done` | Borrowed art replaced by inline SVG we drew (`ui/src/components/Art.jsx`), including a new mascot; hero photo and video removed; attributions complete; `tests/test_static_provenance.py`; image check made non-vacuous (R-007); axe fixes (R-008, R-009). |
| L2 outbound safety | `63b8ccb` `857281e` | `m2-done` | Per-brief cap (10 a day) beside 30 a day and 3 a vendor; sender and "Reply STOP" line inside the approved hash; tests with negative controls. |
| L3 shared-quote privacy | `9886515` `b9235b4` `fe6e294` `c83bcfa` `3290cba` | `m3-done` | Pool keeps the month, not the day; the other person's date never leaves the store; pasted replies never written; documented as dormant (R-013, R-014); first-line "stop" read (R-015); `tools/pre-commit` and `make hooks`. |
| L4 hosted rehearsal | `55da06e` `31bca2a` `3882561` | `m4-done` | Real-browser journey through nginx and TLS in shared mode; nginx keeps the port (R-011); Build needs a chosen vendor, one quote can be chosen (R-012); `/plan` visitor in the soak. |
| L5 accessibility and browsers | `dcda652` `7d080a4` | `m5-done` | Chromium and Firefox at 390 / 820 / 1440; screen-reader pass recorded as a human task; flake fixes (R-016). |
| L6 checklist and report | this commit | none | `docs/LAUNCH_CHECKLIST.md`, `docs/FINAL_REPORT.md`, reviewer's log and ledger. |

Gate evidence at `3882561` (reviewer, clean worktree): 5050 passed, 4 skipped, 0 failed; UI build reproduces byte for byte; fresh image check all ok; soak 1800 s, 20 visitors (10 on `/plan`): 31,508 requests, 0 5xx, 530 builds p95 6.6 s, request p95 164 ms, RSS 214 to 234 MB. Details in `docs/REVIEW_LOG.md`; every finding R-001 to R-016 and P-001 in `docs/REVIEW_FINDINGS.md`.

## 2. From a clean clone: commands to copy

Needs Python 3.12 or later (verified on 3.14), Node, `git`, `cmake g++ make flex bison`, `nginx` and `openssl` for the hosted journey, Docker for the image. Run from the repository root.

```sh
# 1. environment
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,serve]"        # add ,flights to also run tests/test_travel.py (pulls fli from GitHub at a pinned commit)

# 2. the planning tools (plans refuse to run without them; ~10 min compile)
mkdir -p solvers && cd solvers
git clone --depth 1 --branch release-24.06.1 https://github.com/aibasel/downward.git fast-downward-24.06.1
(cd fast-downward-24.06.1 && test "$(git rev-parse HEAD)" = 1eef26b2cbf599a1894606aa898d9d49e1034cb9 && python3 build.py -j4)
git init -q VAL && cd VAL && git remote add origin https://github.com/KCL-Planning/VAL.git \
  && git fetch -q --depth 1 origin 3c7a1f330bdab0ba28a4762bb45c3f06c27fb6d4 && git checkout -q FETCH_HEAD \
  && mkdir -p build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 && make -j4
cd ../../..

# 3. browsers for the browser tests
python -m playwright install chromium firefox     # Chromium may also be a system install

# 4. the commit guard (refuses .env, run-time data, caches, solvers, secrets)
make hooks

# 5. checks
make lint            # pyflakes, ruff, node --check
make security        # bandit -ll
make test            # about 12 minutes; 5054 collected / 5050 passed / 4 opt-in skips with ,flights (5008 passed / 7 skipped without)
make ui              # needs npm; rebuilds plan.js and plan.css, git should stay clean
make deploy-check    # proxy and smuggling tests against nginx, then builds and checks the image (needs Docker)

# 6. the hosted journey alone (real Chromium, nginx, TLS, shared mode; nothing is sent)
python -m pytest -q tests/test_hosted_journey.py

# 7. the soak (run from a checkout with no .env: shared mode refuses to start with one)
python tools/loadtest/soak.py --seconds 60 --visitors 8          # smoke
python tools/loadtest/soak.py --seconds 1800 --visitors 20       # before a release
```

## 3. REVIEW decisions and deviations (with how to reverse each)

`docs/DECISIONS.md` has the full rows.

| ID | Decision | To reverse |
|---|---|---|
| D-001 REVIEW | `docs/*.md` stay tracked despite the "not published" ignore line; new docs are added with `git add -f <file>`. | `git rm --cached` the docs you want private. They stay in history, so rewrite it before any push. |
| D-002 REVIEW | Borrowed art was never committed; moved to `../_quarantine-borrowed-art/` and replaced by SVG we drew. New look: ink line art, a party-hat mascot. | Redesign `ui/src/components/Art.jsx` and `make ui`. Restoring the old art needs written rights (`CHARACTER-SOURCE.md` was removed with it; copy in the quarantine folder). |
| D-004 REVIEW | `# nosec B108` on the sandbox's in-sandbox tmpfs path. | Delete the comment and the bandit gate goes red. |
| D-006 REVIEW | Landing hero photo and video removed, not made optional. | Put the files back, restore the tags in `landing.html`, list them in `STATIC_FILES` and `ATTRIBUTIONS.md`, only with a confirmed licence. |
| D-007 REVIEW | Per-brief cap of 10 first requests a day (follow-ups not counted). | `SendPolicy.per_plan_per_day` in `messaging/conversations.py`. |
| D-009 REVIEW | Shared quote pool dormant; month not day; no per-person deletion (records have no owner). | Wire a source (BLOCKED.md, `docs/OWNER_DECISIONS.md` section B); the feature is kept. `EVENTMATE_SHARED_QUOTES=0` turns it off now. |
| D-011 REVIEW | The per-brief cap restarts when a brief becomes a plan; the global 30 a day is the bound. | Count by brief id across the brief-to-plan link. |
| D-012 REVIEW | The same STOP footer on "Send it yourself" drafts, where the app never sees the reply. | Use a different footer for manual drafts in `outreach._message` (the hash covers whichever text is used). |
| D-003, D-005, D-008, D-010 | Egg-info and papers ignored; race-test clock; fixed sender and STOP line; first-line "stop" by word list. | See the rows. |

DEVIATIONS from `docs/LAUNCH_PLAN.md`:
- L1 was folded into the L0 branch and gated together (`launch/l0`, `6f1d7d5`), on the reviewer's recommendation after the clean clone failed. To reverse: none needed; tags `m0-done` and `m1-done` are the same commit.
- L3 became "make it dormant and honest" rather than a removal path, because the pool has no records to remove (R-013). To reverse: see D-009.
- Two process slips, P-001: `git add -f` staged `.env` and later `__pycache__`, logs, plans and a sqlite file as local commits. Both were caught before any push, rewritten and pruned; the reviewer verified the objects are gone. Prevented now by `tools/pre-commit`. Do not use `git add -f` on directories.

## 4. BLOCKED items and the exact human action

| Item | Action |
|---|---|
| Host, DNS, certificate | Pick a host. Follow `docs/HOSTING.md`: shared-mode settings, `deploy/nginx.conf.example` with `Host $http_host`, real secrets in the environment, no `.env` file. |
| Real mailbox test | With the owner's own address only: `docs/EMAIL_SETUP.md`, send one approved request to yourself, reply STOP and confirm the address is suppressed. |
| Screen-reader pass | NVDA or VoiceOver through `/` to `/plan` to a built plan: the 8-point list in `docs/BLOCKED.md`; record reader, browser and failures. |
| Counsel | Review the vendor-card legal text and the shared pool (R-013, D-009). |
| Shared earlier quotes | Decide how to feed the pool: an operator-run mailbox is the one clean source (`docs/OWNER_DECISIONS.md` section B). The feature is kept. |
| Live-model evals | `prompts/assistant.lock.json` says `not_run`; run them with a real key if models are switched on. |
| Safari / WebKit | Open `/`, `/plan` and a built plan in Safari at 390 and 1440 px. |

## 5. Known issues, by severity

No BLOCKER or MAJOR is open in the reviewer's ledger (all R-001 to R-016 closed). Open, by how much they matter:
1. **Medium, needs a human before launch:** no screen-reader pass; no real mailbox, host or certificate has been exercised; live-model behaviour untested.
2. **Medium:** the shared quote pool can never fill (dormant, documented); decide its fate.
3. **Low:** manual drafts promise a STOP the app cannot hear (D-012); the per-brief cap restarts at the brief-to-plan step (D-011); "pls stop", "stop it" and "STOP - Name" are not read as stops (the footer's exact word and the common shapes are).
4. **Low:** 616 of 31,508 soak requests were 429 on `POST /api/plans`, the 20-plans-a-day workspace cap working as designed (1.96%).
5. **Low:** four opt-in tests never ran (Docling x2, live flights, voice); Python 3.14 only here; no Safari; one proxy (nginx) only.
6. **Low:** the soak must be run from a checkout without `.env`.

## 6. Session log (2026-11-08)

- Reviewer measured the baseline at `605a917`: 25 modified and about 25 untracked files, ruff 13 and pyflakes 5 findings, bandit B108, borrowed art unignored. Builder acknowledged and started L0.
- L0: lint fixed, race test time-bomb fixed, work landed in five feature-grouped commits. FAIL `c7b17eb`: a clean clone failed three tests that needed untracked borrowed art (R-005, R-006, R-007).
- L1 done in place: SVG art, hero media out, attributions, provenance tests, image check made real. PASS `6f1d7d5`; tags `m0-done`, `m1-done`.
- L2: per-brief cap, sender and STOP line, tests and controls. Builder's first L2 commit swept in caches and run-time data (P-001, second time), rewritten as `857281e`. PASS; `m2-done`.
- L3 and R-015: month-only pool, docs made true, stop-by-first-line (a regex version tripped the repo's slow-pattern guard; replaced by a word list), pre-commit hook. PASS `3290cba`; `m3-done`.
- L4 and L5: hosted journey, Host/port fix, Build gating, browser matrix, soak `/plan` visitor, flake fixes. PASS `3882561`; full soak run by the reviewer; `m4-done`, `m5-done`.
- L6: this report, the checklist, and the reviewer's log and ledger. Reviewer's final pass (clean-clone dry run of section 2 and a last gate) comes after READY L6.
