# Launch checklist

Status per item: **done** (with the evidence), **not done**, or **needs a human**. Evidence is a command that was run on a named commit. Draft at `3882561`; the reviewer's verdicts are in `docs/FINAL_REPORT.md`.

## Done

| Item | Evidence |
|---|---|
| Lint, pyflakes, `node --check`, bandit `-ll` clean | `make lint`, `make security` on a clean worktree |
| Full test suite green | `python -m pytest -q`: 5050 passed, 4 skipped (opt-in), 0 failed at `3882561` |
| `/plan` build reproduces | `make ui`: vitest 9 passed, `plan.js`/`plan.css` byte for byte |
| No secrets in history or tracked files | `gitleaks git`, `gitleaks dir` on a clean worktree; `.env` untracked; `tools/pre-commit` refuses staged secrets and local state (`make hooks`) |
| No borrowed art or unconfirmed media ships | `tests/test_static_provenance.py`; art is inline SVG (`ui/src/components/Art.jsx`); `docs/ATTRIBUTIONS.md` |
| No local data in the image | `tools/check_image.sh` (fails on a planted file, shown by the reviewer) and `.dockerignore` |
| Outbound safety | per-brief 10/day, per-day 30, per-vendor 3, suppression at draft and dispatch, pause, kill switch, sender and STOP line inside the approved hash, bare and first-line STOP read (`tests/test_outbound_safety.py`, `tests/test_stop_shapes.py`) |
| Visitors cannot send | `SENDING_ROUTES` refused in shared mode (unchanged) |
| Shared-quote privacy | pool dormant, month not day, pasted replies never written (`tests/test_shared_quotes.py`, `tests/test_quote_promote.py`) |
| Hosted dress rehearsal | shared mode, waitress behind nginx with TLS, real Chromium, `/` to a built plan (`tests/test_hosted_journey.py`); proxy and smuggling tests; `tools/check_image.sh` on an image from the clean tree |
| Soak | `tools/loadtest/soak.py`: 120 s smoke clean; full 1800 s / 20 visitors run by the reviewer (result in the final report) |
| Accessibility and browsers | axe 0 on `/`, `/app`, every `/plan` room; keyboard-only path; Chromium and Firefox at 390/820/1440 (`tests/test_plan_page_a11y.py`, `tests/test_browsers_sizes.py`) |

## Needs a human

- **Screen-reader pass** (NVDA or VoiceOver): checklist in `docs/BLOCKED.md`.
- **Owner decisions** marked REVIEW in `docs/DECISIONS.md`: D-001, D-002, D-004, D-006, D-007, D-009, D-011, D-012.
- **Real mailbox end to end**, with the owner's own mailbox, never a vendor's (`docs/LAUNCH_PLAN.md` section 6).
- **A real host**: DNS, TLS certificate, secrets in the real environment (`docs/HOSTING.md`), keep `$http_host` in the proxy file.
- **Confirm the licences** of any hero photo or video before adding one back.

## Not done

- The shared earlier-quote pool has no source (BLOCKED.md): wire an operator mailbox or remove it.
- Opt-in tests not run: Docling (2), live flights, voice engine.
- Manual "Send it yourself" drafts carry a STOP footer the app cannot honour (D-012).
- The per-plan cap restarts when a brief becomes a plan (D-011).
