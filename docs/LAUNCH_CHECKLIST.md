# Launch checklist

Three categories only. **Done**: a command was run on a named commit and passed. **Not done**: the work is missing or knowingly left. **Needs a human**: it cannot be honestly done or judged by us. Shas are on branch `launch/*`, merged to `main`, tags `m0-done` to `m5-done`. The reviewer's independent record is `docs/REVIEW_LOG.md` and `docs/REVIEW_FINDINGS.md`.

## Done

| Item | Evidence |
|---|---|
| Lint, pyflakes, `node --check`, `bandit -ll` clean | `make lint`, `make security` on a clean worktree at `3882561` |
| Full suite green | `python -m pytest -q`: 5050 passed, 4 skipped (opt-in), 0 failed, clean worktree, `3882561` (reviewer and builder, separately) |
| `/plan` build reproduces | `make ui`: vitest 9 passed, `plan.js` / `plan.css` byte for byte, `3882561` |
| No secrets in history or tracked files | `gitleaks git` and `gitleaks dir` on a clean worktree; `.env` untracked; `tools/pre-commit` refuses staged secrets and local state |
| No borrowed art or unconfirmed media ships | `tests/test_static_provenance.py` (5 tests); inline SVG art (`0e8bcaa`); `docs/ATTRIBUTIONS.md` |
| No run-time data or unlisted files in the image | `tools/check_image.sh`; reviewer planted `plans/leak.jsonl` and a story webp in an image and the check failed (R-007) |
| Container image works | `tools/check_image.sh`, fresh image from `3882561`: all checks ok (solvers validate a plan, sandbox, shared mode refuses without a secret, probes, Host check, owner route hidden, drains on SIGTERM) |
| Outbound safety | per-day 30, per-vendor 3, per-brief 10 caps; suppression at draft and dispatch; pause and kill switch; sender and STOP line inside the approved hash; STOP and first-line "stop please" read: `tests/test_outbound_safety.py`, `tests/test_stop_shapes.py`; five reviewer mutants killed |
| Visitors cannot send | `SENDING_ROUTES` refused in shared mode (unchanged, tested) |
| Shared-quote privacy | month not day; other person's date never leaves the store; pasted replies never written; pool documented dormant: `tests/test_shared_quotes.py`, `tests/test_quote_promote.py`; three reviewer mutants killed |
| Hosted dress rehearsal | `tests/test_hosted_journey.py`: shared mode, waitress behind the shipped nginx with TLS, real Chromium, `/` to a built plan, shipped config unmodified; proxy and smuggling tests (10 passed) |
| Soak | `soak.py --seconds 1800 --visitors 20` (10 on /plan), reviewer, `3882561`: 31,508 requests, 0 5xx, 530 builds p95 6.6 s, request p95 164 ms, RSS 214 to 234 MB, threads 25 to 27, fds 9 to 8 |
| Accessibility | axe 0 violations on `/`, `/app` and every `/plan` room at 390 and 1440 px; keyboard-only path to Yes (`tests/test_plan_page_a11y.py`) |
| Browsers and widths | Chromium and Firefox at 390 / 820 / 1440 on `/`, `/app`, every `/plan` room: axe, overflow, console (`tests/test_browsers_sizes.py`, 6 passed) |
| Docs current | `CHANGELOG.md`, `docs/DECISIONS.md`, `docs/BLOCKED.md`, `docs/HANDOFF.md`, `docs/REVIEW_LOG.md`, `docs/REVIEW_FINDINGS.md` |

## Needs a human

1. **A real host**: choose a provider, DNS, a real certificate, secrets in the real environment (`docs/HOSTING.md`). Keep `Host $http_host` in the proxy file.
2. **A real mailbox test**, with the owner's own address, never a vendor's (`docs/EMAIL_SETUP.md`).
3. **The screen-reader pass** with NVDA or VoiceOver: the 8-point checklist is in `docs/BLOCKED.md`.
4. **Counsel** on the vendor-card legal text and on the shared quote pool (R-013, D-009).
5. **The owner's REVIEW decisions**, `docs/DECISIONS.md`: D-001, D-002 (the art and mascot), D-004, D-006, D-007, D-009, D-011, D-012.
6. **Live-model behavioural evals**: `prompts/assistant.lock.json` records them `not_run`; run them with a real model key if models are turned on.
7. **Safari / WebKit** pass: not tested.
8. **Hero media**: add a photo or video back only with a confirmed licence.

## Not done

- The shared earlier-quote pool has no source in any shipped mode; wire an operator mailbox (`docs/OWNER_DECISIONS.md` section B); the feature is kept.
- Opt-in tests not run: Docling (2), live Google Flights, voice engine.
- Manual "Send it yourself" drafts carry a STOP footer the app cannot honour (D-012).
- The per-brief cap restarts when a brief becomes a plan (D-011); the global 30 a day is the real bound.
- Stop shapes still not read: "pls stop", "stop it", "STOP - Ravi" on one line.
- Python versions other than 3.14 here, another proxy (Caddy, a cloud load balancer), traffic from more than one machine.
