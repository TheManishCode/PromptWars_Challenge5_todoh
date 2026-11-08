# Launch plan: builder-todoh + reviewer-todoh

Written 2026-11-08 by reviewer-todoh (session 7f7b64) after reading `docs/HANDOFF.md`, `docs/FUTURE.md`, `docs/eventmate_architecture.md`, `docs/HOSTING.md`, `docs/RUNBOOK.md`, `SECURITY.md` and running a baseline.
Goal: take the current working tree to a state where a human can put it live, with every claim backed by a command that was run. "Live" itself (DNS, a real host, a public URL) is the owner's step, not ours.

## 1. Roles and the one rule that keeps them apart

- **builder-todoh (524791)** owns the working tree at `todoh/`, every branch named `launch/*`, and `main`. Builds forward and does not wait for review.
- **reviewer-todoh (7f7b64)** never edits the builder's working tree. It reviews **committed** snapshots only, in its own worktree (`git worktree add /tmp/reviewer-todoh/wt <sha>`), runs the gate, writes adversarial tests and negative controls on branches named `review/*`, and reports findings.
- Consequence: nothing is reviewed until it is committed. That is deliberate: the handoff starts with "nothing from this session is committed".

## 2. Message protocol (Imbo, session to session)

| From | Subject line | Body |
|---|---|---|
| builder | `READY L<n> <sha>` | branch, what changed, which tests prove it, anything knowingly left out |
| reviewer | `PASS L<n> <sha>` | the gate output summary; builder may merge to `main` and tag `m<n>-done` |
| reviewer | `FAIL L<n> <sha>` | findings `R-<nnn>` with severity (BLOCKER / MAJOR / MINOR), the command that shows it, and a `review/*` branch with failing tests where possible |
| builder | `FIXED R-<nnn> <sha>` | one line per finding |
| either | `BLOCKED <topic>` | goes to `docs/BLOCKED.md` too; work moves on |

BLOCKER stops the merge of that milestone only. The builder keeps going on L<n+1> while L<n> is in review. After 3 failed fixes of one finding: revert to the last good commit, log it, try another approach.

## 3. Gate (run by the reviewer on the exact sha, in a clean worktree)

1. `ruff check eventmate tests tools`, `pyflakes eventmate tests`, `node --check` on the shipped JS: zero findings.
2. `bandit -q -r eventmate -ll`: zero medium or high, or a `# nosec <id>` with a reason the reviewer accepts.
3. Full `pytest -q`: all pass; skips listed and justified (browser skips are not acceptable for L4 and L5).
4. `ui/`: `npx vitest run` and `npx vite build` reproduce the committed `plan.js`/`plan.css` byte for byte (or the difference is explained).
5. Secrets: `gitleaks git` over history and `gitleaks dir` over tracked files: zero. `.env` stays untracked.
6. Negative controls: for each new safety check, the reviewer breaks the code it guards in the worktree and confirms the named test fails, then discards the change. A check whose test still passes when broken is a finding.
7. Docs: `CHANGELOG.md` entry, `docs/DECISIONS.md` rows for every non-trivial choice (REVIEW-marked where the owner should look), `docs/HANDOFF.md` current, `docs/BLOCKED.md` current.

## 4. Baseline found on 2026-11-08 (before L0)

- HEAD `605a917` on `intake-core-reader-patch`; 25 modified, 2 staged, ~25 untracked files including the whole `/plan` page, `ui/`, `pricing/shared_quotes.py`, `sourcing/quote_records.py`.
- `docs/` is in `.gitignore`: `HANDOFF.md`, `FUTURE.md`, `research/` and this file are untracked.
- ruff: 13 findings; pyflakes: 5 (tests and `web/routes/staging.py`). The Makefile's `lint` target is red.
- bandit `-ll`: 1 medium, B108 at `eventmate/documents/isolation.py:141` (the architecture doc says 0).
- gitleaks: history clean; the only hits are in the git-ignored `.env` (6). Good.
- **IP:** `eventmate/web/static/plan-story-*.webp` are pixel crops of outside images (`ui/artwork-source-map.json`), and `ui/CHARACTER-SOURCE.md` says rights are unverified and "clear rights or replace the character before launch". They are not git-ignored and `plan.js` references them.
- Remote `origin` is GitHub: **no pushes** (public exposure boundary). Merges and tags are local.

## 5. Milestones to launch-ready

| ID | Name | Done when |
|---|---|---|
| L0 | Land and go green | The session's work is in reviewable commits on `launch/l0` (grouped by feature, not one blob); the docs that matter are tracked (narrow the `docs/` ignore or `git add -f`; record the choice); lint, pyflakes, bandit and the full suite are green; merged to `main`, tag `m0-done`. |
| L1 | IP clearance | No borrowed artwork ships: `plan-story-*` and `plan-reference-*` are removed or replaced with art we author (SVG/CSS); `/plan` still works and looks finished; a test fails if a static asset is not listed with its source and licence in `docs/ATTRIBUTIONS.md`; fonts' licences checked. |
| L2 | Outbound safety | Before any real vendor is contacted (HANDOFF section 4): send caps per brief and per day, the suppression list enforced in the brief flow, a clear sender line in every request, all with tests and negative controls. Sending stays off for visitors in shared mode. |
| L3 | Shared quote privacy | The shared store has a retention rule (exists), a way to remove records on request (or a documented decision not to), a privacy-notice paragraph, and the switch documented. Owner decision marked REVIEW. |
| L4 | Hosted dress rehearsal | `make deploy-check` passes (proxy tests, smuggling tests, image check) and a real-browser run of `/` -> `/plan` -> a finished plan **through nginx + TLS in shared mode** with fakes for vendors and mail. Soak test 30 min / 20 visitors passes. |
| L5 | Accessibility and browsers | axe-core 0 violations on `/`, `/plan` (every step), `/app`; keyboard-only path through `/plan`; Chromium and Firefox at 390 / 820 / 1440 px. |
| L6 | Launch checklist and report | `docs/LAUNCH_CHECKLIST.md` with each item done (evidence), not done, or needs a human; `docs/FINAL_REPORT.md`. |

L2-L5 may proceed in any order once L0 is merged; L1 must be merged before anything is called launch-ready.

## 6. Out of our hands (goes to BLOCKED.md, needs the owner)

- Real mailbox end-to-end test (needs the owner's own mailbox; never a vendor's).
- Counsel on `cards-legal` and on the shared quote pool (decision 5 partly reversed).
- The host, DNS, TLS certificate and the decision to go public.
- Any paid Google Places usage beyond a small pilot with the key already in `.env`.
