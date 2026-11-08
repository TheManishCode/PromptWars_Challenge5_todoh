# Review log (reviewer-todoh, session 7f7b64), 2026-11-08

Every verdict below was produced by running the command on the exact sha in a clean `git worktree` (no `.env`, no untracked files; only the locally built `solvers/` symlinked in, as `docs/CONFIGURATION.md` tells a clean clone to build). Gate script: lint (ruff, pyflakes, `node --check`), `bandit -ll`, `gitleaks` over history and tracked files, `.env` untracked, launch docs tracked, no borrowed art, `ui/` rebuilt byte-for-byte, full `pytest`.

## Verdicts

| Milestone | sha | Verdict | Evidence |
|---|---|---|---|
| L0 | c7b17eb | FAIL | pytest 3 failed / 5009 passed on a clean clone: tests needed untracked borrowed art (R-006); R-005, R-007 |
| L0+L1 | 6f1d7d5 | PASS (tag m0-done, m1-done) | pytest 5018 passed / 4 skipped / 1 failed: R-010, root-caused to a uuid4 id containing "98765", test defect, fixed in 63b8ccb; fresh image `tools/check_image.sh` all ok |
| L2 | 857281e | PASS (m2-done) | pytest 5025 / 4 skipped / 0 failed |
| L3 + R-015 | 3290cba | PASS (m3-done) | pytest 5042 / 4 skipped / 0 failed (c83bcfa failed only the repo's quadratic-regex guard, fixed in 3290cba) |
| L4 + L5 + R-016 | 3882561 | PASS | pytest 5050 / 4 skipped / 0 failed; fresh image `check_image.sh` all ok; soak below |

The 4 skips in every run are opt-in engines that need outside software or live services: Docling (2), live Google Flights, the voice engine. **NOT RUN.**

## Negative controls (each a deliberate break; the named test had to fail)

| Guard | Break | Result |
|---|---|---|
| Image leak check (R-007) | built FROM the image with `eventmate/plans/leak.jsonl`, then separately `static/plan-story-knot.webp`, planted in site-packages | FAIL with the exact path (before the fix it said ok: the check was vacuous) |
| Per-plan send cap | cap check disabled; `>=` changed to `>`; cap counting every plan | each killed |
| Sender + STOP footer | footer removed | 3 tests fail |
| Bare STOP reading | `^stop$` branch removed | killed |
| Shared pool month-only | validator keeps the day | killed |
| Pasted replies stay out of the pool | `pasted_by is None` check removed | killed |
| Other person's date never leaves the store | `quoted_for_date` put back in the API | killed |
| Build needs a chosen vendor (R-012) | old "comparable > 0" gating restored, ui rebuilt | hosted browser journey fails at "Build is disabled" |
| Pre-commit guard | scratch repo: `.env`, `.pyc`, plans jsonl, `wall.sqlite3`, `solvers/`, `owner_token`, a GitHub token | all refused; README, `.env.example`, docs accepted |
| Overflow detector (browser test) | 900px element on a 390px screen | reports 518px in Chromium and Firefox |

## End-to-end runs

- **Hosted journey** (`tests/test_hosted_journey.py`): shared mode, waitress behind the shipped `deploy/nginx.conf.example` with TLS, real Chromium. A stranger goes from `/` to a built, validated plan using only what a visitor can do (add vendors they know, send it themselves, paste replies, confirm line by line, choose, build). Nothing is sent. Passes on 3882561 with the shipped config unmodified.
- **Browsers and widths** (`tests/test_browsers_sizes.py`): `/`, `/app` and every `/plan` room in Chromium and Firefox at 390, 820 and 1440 px. No axe violation, no horizontal overflow, no console error. 6/6.
- **Proxy and smuggling** (`tests/test_proxy_deploy.py`, `tests/pentest/test_proxy_smuggling.py`) against nginx with TLS: pass.
- **Soak**, 3882561, `soak.py --seconds 1800 --visitors 20` (10 on the `/plan` path): exit 0, failures none. 31,508 requests, 0 5xx, 616 x 429 on `POST /api/plans` (1.96%: the per-workspace cap of 20 plans a day, by design). 530 `/plan` builds, commit-to-done p50 2.5 s / p95 6.6 s. Request p50 8 ms / p95 164 ms / p99 652 ms. Slowest route p95: `GET export` 1.09 s, `POST intake` 0.88 s. Server RSS 214 to 234 MB (peak 234), threads 25 to 27, fds 9 to 8.

## Independent checks of the builder's disclosures

- Accidental local commit of `.env` (L0): the hash of the current `.env` is absent from the object database, no reachable `.env` exists in any ref, and the one unreachable object was a tree with no `.env`. Clean.
- Accidental local commit of `__pycache__`, logs, plans and `wall.sqlite3` (L2, shas 48e3638 and 9db4b48): both shas are absent, zero unreachable objects, and 857281e's tree holds none of it. Clean. Neither was ever pushed. A pre-commit guard now refuses both classes (fe6e294).

## Findings

R-001 to R-016 and P-001: all closed except the owner decisions listed in `docs/BLOCKED.md`. The full ledger is in `docs/REVIEW_FINDINGS.md`.

## Not verified by the reviewer (and why)

- A screen-reader pass (needs a person with NVDA or VoiceOver): `docs/BLOCKED.md`.
- Any real mailbox, WhatsApp account or real vendor: never contacted, by design and by the charter.
- A real host, DNS, a real certificate, another proxy (Caddy, a cloud load balancer), or traffic from more than one machine.
- Live model behaviour: the suite runs with model calls off or faked. `prompts/assistant.lock.json` says the behavioural evals are `not_run` against a real model.
- Python versions other than the 3.14 venv here; browsers other than Chromium and Firefox (no Safari/WebKit pass).
