# Decisions

Rows marked REVIEW are the owner's to look at.

| # | Decision | Why | Marked |
|---|---|---|---|
| D-001 | `docs/*.md` stay tracked (they already were); new docs are added with `git add -f` because `docs/` is in `.gitignore`. | The launch method needs HANDOFF, DECISIONS, BLOCKED and LAUNCH_PLAN reviewable on a sha. | REVIEW: the ignore line says docs are "not published for now"; tracked docs are public if the repo is ever pushed. |
| D-002 | The borrowed artwork was never committed; it was moved out of the tree to `../_quarantine-borrowed-art/` (with its source notes) and replaced by SVG drawn for the project. | Pixel crops of outside images and a third-party character, rights unverified. Never in history, so no rewrite is needed. | REVIEW: the new look (a hand-drawn ink style, a party-hat mascot) is a design change the owner should see. |
| D-003 | `papers to refer/` (PDFs of papers) and `*.egg-info/` are git-ignored. | Third-party publications are not ours to republish; egg-info is a build product. | |
| D-004 | B108 at `documents/isolation.py` is suppressed with `# nosec B108` and a reason. | The path is a tmpfs inside the bubblewrap sandbox, not a host temp file. | REVIEW |
| D-005 | The approvals race test now builds its child queues with the same fixed clock as the proposal. | It used the real clock, so once the date passed the 7-day expiry the proposal read "expired". | |
| D-006 | The landing hero poster/video were removed, not kept as optional. | Licence unconfirmed; an optional, absent file added code paths (tag stripping, 404 handling) for nothing. The hero is a plain gradient. | REVIEW: restore a hero only with a confirmed licence. |
