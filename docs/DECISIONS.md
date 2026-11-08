# Decisions

Rows marked REVIEW are the owner's to look at.

| # | Decision | Why | Marked |
|---|---|---|---|
| D-001 | `docs/*.md` stay tracked (they already were); new docs are added with `git add -f` because `docs/` is in `.gitignore`. | The launch method needs HANDOFF, DECISIONS, BLOCKED and LAUNCH_PLAN reviewable on a sha. | REVIEW: the ignore line says docs are "not published for now"; tracked docs are public if the repo is ever pushed. |
| D-002 | `plan-story-*.webp`, `ui/art/` are git-ignored, not committed in L0. | Pixel crops of outside images, rights unverified (`ui/CHARACTER-SOURCE.md`). Keeping them out of history avoids having to rewrite it. | Consequence: the committed `plan.js` references files that are absent from a clean checkout, so `/plan` shows no story art until L1 and `make ui` cannot rebuild from a clean clone. |
| D-003 | `papers to refer/` (PDFs of papers) and `*.egg-info/` are git-ignored. | Third-party publications are not ours to republish; egg-info is a build product. | |
| D-004 | B108 at `documents/isolation.py` is suppressed with `# nosec B108` and a reason. | The path is a tmpfs inside the bubblewrap sandbox, not a host temp file. | REVIEW |
| D-005 | The approvals race test now builds its child queues with the same fixed clock as the proposal. | It used the real clock, so once the date passed the 7-day expiry the proposal read "expired". | |
