# Changelog

## Unreleased (launch/l0)

- /plan page (React, `ui/`) with its committed build; the landing page now leads to it.
- Shared quotes: records, comparison, promotion into a plan, per-workspace backup.
- Lint, pyflakes and bandit clean; `make lint` and `make security` pass.
- Fixed a time-bomb in the approvals race test (its child processes used the real clock against a fixed 2026-11-01 proposal date).
- Container image: `.dockerignore` now excludes run-time data (`plans/`, `logs/`, `wall_data/`, `workspaces/`), `.env*` and unconfirmed media; `tools/check_image.sh` checks the built image for them.
- Borrowed `plan-story-*` art is git-ignored (quarantine) until L1 replaces it.
