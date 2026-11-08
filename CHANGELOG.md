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
