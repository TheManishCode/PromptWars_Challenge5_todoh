# Changelog

## Unreleased (launch/l0)

- /plan page (React, `ui/`) with its committed build; the landing page now leads to it.
- Shared quotes: records, comparison, promotion into a plan, per-workspace backup.
- Lint, pyflakes and bandit clean; `make lint` and `make security` pass.
- Fixed a time-bomb in the approvals race test (its child processes used the real clock against a fixed 2026-11-01 proposal date).
- Container image: `.dockerignore` now excludes run-time data (`plans/`, `logs/`, `wall_data/`, `workspaces/`), `.env*` and unconfirmed media; `tools/check_image.sh` checks the built image for them.
- docs/CONFIGURATION.md: how to build the planning tools in a clean clone.
### L1 (IP clearance), folded into this branch
- All /plan drawings (characters, tangle, table sketch, thinker/mascot) are now inline SVG authored for this project (`ui/src/components/Art.jsx`); no image files; the borrowed `plan-story-*`/`plan-reference-*` webp and their source notes are out of the repository, the served list and the build. The mascot is a new character (round head, party hat), not the reference one.
- The landing hero poster and video (licence unconfirmed) are removed from the page and the served list; `landing.js` keeps only the motion-lite switch.
- `docs/ATTRIBUTIONS.md` lists `plan-instrument-serif-italic.ttf` (OFL, name table verified) and `favicon.svg` (authored).
- `tests/test_static_provenance.py`: every served media file is attributed, none unconfirmed, nothing git-ignored reaches the image, static/ holds only the served list, the built page names no image file.
- `tools/check_image.sh` looks in the installed package (not the removed /app/eventmate), fails on plans/ logs/ wall_data/ workspaces/ .env and on any static file not on the served list; shown to fail by planting `plans/leak.jsonl` in an image built FROM a clean one (R-007).
- docs/CONFIGURATION.md: how to build the planning tools in a clean clone.
- /plan accessibility (R-008, R-009): the brand link names itself "to;doh, back to the start" (label starts with the visible text), the planning rooms sit in a <main> landmark; `tests/test_plan_page_a11y.py` (axe on every room at 1440 and 390 px, keyboard-only path) from review/l5-plan-axe, no axe ignores.

### L2 (outbound safety)
- Per-plan cap: `SendPolicy.per_plan_per_day = 10` first requests per plan or brief per 24 h, counted under the same store lock as the daily and per-vendor caps; the rest wait with a reason in `poll_conversations().waiting` and go the next day. Brief flow (`brief_<id>`) already shares the store, suppression (checked at draft and at dispatch), pause and kill switch.
- Every first request ends with "(Written with to;doh on behalf of <sender>. Reply STOP and we will not write again.)" inside the hashed body, on email and WhatsApp, and in the mailto/wa.me fallback.
- A reply that is just STOP suppresses the address; quoting the footer does not. `tests/test_outbound_safety.py`, each check shown to fail when its code is removed.
- R-010: the feedback masking test checks the comment, not an envelope that holds a random id.

### L3 (shared quote privacy)
- The shared pool keeps the month of the event (first of the month), never the day; old rows read back coarsened. `shared_earlier` no longer returns the date and says "a different month than your event", worked out on the server.
- Docs made true: the pool is dormant in every shipped mode (PRIVACY.md, HANDOFF section 7, BLOCKED.md, D-009).
- Tests: month-only storage, no date in what is shown, and a pasted reply writes nothing at all under `shared/` (negative controls: day kept; paste allowed).
- R-015: a first-line "stop" with please/thanks or a signature under it is read as a stop request (`tests/test_stop_shapes.py` from review/l2-stop-shapes, 15 cases; control: without the rule 7 fail).
- `tools/pre-commit` and `make hooks`: refuse staged .env, run-time data, caches, solvers and secrets; installed in .git/hooks.

- Stop detection is word-based, not a pattern (a pattern version failed the quadratic-time guard). Test fix: the Google-listing-on-disk check matched "581" inside random hex ids (failed about one run in six); it now matches whole tokens (30/30 reruns pass).
