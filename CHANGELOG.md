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

### L4 (hosted dress rehearsal) and L5 (browsers)
- `tests/test_hosted_journey.py` (from review/l4-hosted-journey): shared mode, waitress behind the shipped nginx with TLS, real Chromium, `/` to a built plan, nothing sent.
- R-011: the reference nginx sends `Host $http_host` (with the port), so writes are not refused as cross-origin on a port other than 443; documented in HOSTING.md; the journey test no longer patches the file.
- R-012: "Build the plan" is on only when each needed kind has a chosen vendor, and says which to choose or ask; the Compare step offers "Plan with this vendor" for a single confirmed quote ("Only one quote so far"); chosen vendors come from the server so a reload still shows them; the server's refusal reads "confirmed and chosen quote". Browser test asserts the disabled state and the wording (control: with Build always enabled it fails).
- `tests/test_browsers_sizes.py` (from review/l5-browsers): `/`, `/app` and every `/plan` room in Chromium and Firefox at 390, 820 and 1440 px, axe clean, no overflow, no console error. Needs `python -m playwright install firefox`.
- BLOCKED.md: the screen-reader pass is a human task, with a checklist.
- R-016: `tests/test_retention.py` and `tests/test_approvals_durable.py` no longer search random ids for a digit string.
- `tools/loadtest/soak.py`: a /plan visitor type (`--plan-share`, half by default) walks brief, vendors, requests, pasted replies, confirm, promote, commit with from_quotes and polls the queue to done through the API. Fails on any 5xx, a commit that never ends, a refused step, or builds over `--build-p95-s` (60). Smoke on this commit: 120 s, 12 visitors (6 on /plan), 1391 requests, all 2xx, 24 builds, build p50 2.3 s / p95 2.6 s, RSS 213 to 218 MB, threads 25 to 27, fds 8 to 8. It must be run from a checkout without a `.env` (shared mode refuses one).
- R-017: the VAL build recipe (docs/CONFIGURATION.md, FINAL_REPORT, CI) passes `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`, which CMake 4 requires. The Dockerfile builds on Debian bookworm (CMake 3.25) and is unchanged.
- R-018: FINAL_REPORT says the flights extra is needed for the full 5054-test count.

## Live program (branch live/integration, not merged to main)

### LM1: a quote prices only the event it fits (audit A1)
- `VendorRepository.price(provider, guests, event_date)` and `quote_expired(...)` use the newest quote that fits the event (`agents/vendor.py: quote_fits`): same `quoted_for_date`, and for a per-plate price the guest count quoted or inside the vendor's own min/max. Anything else is history and the seed or catalogue price applies. All callers pass the event date.
- `promote_quote` takes the reply's own quote (`ObservationStore.quote_for_reply`), not the vendor's latest, and refuses a quote given for another date.
- A quote confirmed through a follow-up job also records the plan's event date. A quote typed in through the API (`POST /api/observations/<vendor>/quotes`) prices only if it names `quoted_for_date`.
- A quote confirmed from a document line is recorded for a plan's event date when the plan is named (`confirm_quote_line(..., plan_id)`, the page already sends it); without one the date is unknown and it never prices (D-013).
- Tests: `tests/test_quote_event_fit.py` (reviewer probes `cc49526` plus fit and expiry cases), `tests/test_quote_promote.py`. Controls: date check removed (2 fail), guest check removed (1 fails), promote on latest (1 fails).

### LM2: the wrong event is never guessed; a brief survives into its plan (audit A2, A4)
- A reply with none of our thread headers is matched by the sender's address only when one event was ever written to at that address (`match_method` = `sender`, shown as "Matched by the sender's address, not by the thread"). When two or more events were, it is held in a review queue (`messaging/reply_review.py`, `GET /api/reply-reviews`, `POST .../assign` and `.../dismiss`, a "Which event?" note on the Replies step) and filed nowhere until the user picks. WhatsApp has no thread headers, so it takes this road. Every stored reply carries its method (`header`, `sender`, `assigned`, `pasted`); `PollResult.held` counts the held ones.
- `planning/brief_links.py`: when a brief is built into a plan the link is recorded and the outreach, inbound, shortlist and quote-confirmation stores resolve either id to the same files, so a reply after the build is the plan's reply and can be read and confirmed from the plan. `_scope(brief)` reads as the plan once there is one. A pending brief with a request still unanswered is neither expired nor swept, and a reply keeps the brief (`quotes-carry` is done).
- Tests: `tests/test_reply_matching.py` (8; the reviewer's A2 probe is the first). Controls: guess the newest (3 fail), no store alias (1), no sweep protection (1), method not recorded (4).

### LM3: replies are not missed silently; status words say what is known (audit A3, A5)
- `EmailChannel.poll` asks the IMAP server for the threads we know (`FROM` each address written to, `HEADER In-Reply-To` / `References` each of our message ids, delivery failure notices), up to 200 messages a poll newest first, plus the newest 20 other messages of the window for stop requests and notices. `last_poll` reports examined, fetched, overflow and mode; the poll result carries `overflow`, `spam` and `warnings`, and the Replies step shows them ("N more replies are in the mailbox and were not read this time"). A server that cannot search by thread falls back to the window and says so.
- Optional spam folder (`EVENTMATE_IMAP_SPAM_FOLDER`, empty by default): read-only, only addresses we wrote to, the results go to the review list as `spam` messages (a note on the Replies step) and are never filed, read as quotes or obeyed.
- A5: each request carries `delivery` (`draft`, `queued`, `accepted`, `bounced`, `replied`, `self`, ...) with a label that does not claim delivery; the Message step, the overview and the dashboard use it.
- `tests/mailfake.py` FakeIMAP now answers SEARCH (SINCE, FROM, HEADER) and keeps folders, and fails if a mailbox is opened for writing. `tests/test_mail_poll.py` (12 tests). Controls: newest-fifty again (4 fail), overflow not counted (1), spam filed like inbox (1), spam read without being named (1).
- New variable, placeholder only: `EVENTMATE_IMAP_SPAM_FOLDER` in `.env.example`, `deploy/env.example` (empty) and appended, empty, to the local `.env`.
- R-019: filing a held reply is compare-and-set under the review store's cross-process lock (`ReplyReviewStore.claim`): open, then assigning (a lease of 5 minutes, naming the event), then assigned. Two callers at once: one wins. A claim that never finishes lapses and can only be finished for the same event. Tests: a two-process race, a second tab, a lapsed claim; controls: no claim in assign (4 fail), claim not exclusive (2 fail).

