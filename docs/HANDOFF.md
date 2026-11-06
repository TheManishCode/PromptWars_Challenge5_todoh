# Handoff: work log, decisions, and what is left (2026-11-07)

Written at the end of a long working session. Read this first, then `docs/FUTURE.md` (every deferred change, each also a `FUTURE(<tag>)` comment in the code), then `docs/research/vendor-quotes.md` (why the design is shaped as it is).

## 1. State right now

- **Nothing from this session is committed.** HEAD is `6e65793` (the intake batch-2 work, committed before this session's changes). The working tree has 29 modified files and 23 new files (list in section 6).
- **`docs/` is in `.gitignore`.** Files already tracked there are updated normally, but the new ones (`docs/FUTURE.md`, `docs/HANDOFF.md`, `docs/research/`) are untracked and will be missed unless added with `git add -f docs/FUTURE.md docs/HANDOFF.md docs/research`, or the ignore rule is changed. That is a decision for the owner; it was not changed.
- **Tests:** the last full run is `4972 passed, 5 skipped` (about 10 minutes). It was 4814 passed at the start of this session's first full run, so about 158 net new tests. Run with `.venv/bin/python -m pytest -q` (the system python has no pydantic). Browser tests need Chromium and skip without it.
- **The app is running** at http://127.0.0.1:8765/app, started detached. Restart (do not put the pattern in the same command line as the start, or `pkill -f` kills its own shell):
  `pkill -f "python -m eventmate.web --port 8765"; cd todoh && setsid nohup .venv/bin/python -m eventmate.web --port 8765 > /tmp/b2/server.log 2>&1 &`
- **Config:** `todoh/.env` holds working keys for Google Places, Brave, NVIDIA and OpenRouter, and SMTP and IMAP settings. **Because SMTP is configured, pressing "Approve exactly this and send" on a card for a real vendor's confirmed address sends a real email.** Every send needs that explicit per-message approval; no test sends anything (all use fake channels and test vendors whose address the user owns).
- **Scripts used for manual checks** are in `/tmp/b2scripts/` (temporary; recreate if gone). Data from the live app is in `todoh/eventmate/plans/`.

## 2. Work done, in the order it happened

1. **Intake batch 2 follow-up.** Build is hidden for a city with no vendor records (`no_vendors`), with buttons for the cities that do work (`covered_cities`). The 45 single-message inputs of the test pack were re-run live with models (all answered; 36 read by rules, 9 needed the model).
2. **Found online.** When a brief is understood but the city has no vendors, the page looks up caterers, venues and photographers from Google Places and shows them (`GET /api/places`). Listings are shown as listings, not quotes.
3. **Earlier quotes.** A vendor's last recorded quote is shown beside its listing with the reasons it may be out of date (expired, over 30 days old, quoted for a different guest count or date) via `GET /api/earlier-quotes` and `old_reasons`. It is a display rule only and never prices anything. Quotes now record `quoted_for_date`.
4. **Break-and-fix pass** (as a coder, a model, and a first-time visitor). Fixed: Places returned vendors from other cities (Udupi returned Bengaluru caterers; an unknown city returned Bengaluru results) so listings plainly in another city are left out; the dead end after the build gate; the server's wording (including ".env") leaking to visitors; loose input on the earlier-quotes endpoint.
5. **Staging and the human check** (your six-stage design): the reader's draft is staged on the server as `pending_validation` with a version and a SHA-256; values can be typed onto the card and pass the same schema; "done" commits once, naming the version and hash, and runs on a bounded queue; the page polls a status that takes no lock; a failed build can be reopened; the finished payload carries a plan graph. Files: `planning/staging.py`, `web/routes/staging.py`, `web/commit_queue.py`, `planning/plan_graph.py`, `IntakeDraft` edit in `planning/intake.py`.
6. **Future changes commented** as `FUTURE(<tag>)` plus `docs/FUTURE.md`.
7. **Asking vendors before a plan exists.** A staged brief stands in for a plan as `brief_<staged id>` (`planning/brief_scope.py`, `EventMate._scope`). Find vendors, find emails on their own sites, confirm an address, write requests, approve each one, read replies: all of it works with no plan. A brief with requests out is kept 14 days.
8. **Research study** (arXiv and OpenAlex abstracts, plus Google's Places policy page read in full): `docs/research/vendor-quotes.md`.
9. **Items 1 and 2 of the build order:** Google listing content is no longer stored (only the place id); vendor cards show every held fact with source, date and confirmation; direct-contact actions (mail link, WhatsApp click-to-chat, phone on request); Google's official logo; a small prompt-injection test set.
10. **Item 3:** a fixed reply template in every request, a rule-based reader (labelled lines, evidence per field), per-field user confirmation, a completeness index, fit checks, and recording a confirmed price (with its validity) as a quote.

## 3. Decisions and why

| # | Decision | Justification |
|---|---|---|
| 1 | Hide Build for a city with no vendor records, and say so | A plan there cannot be built; offering Build ends in a refusal. The server also refuses (422), so this is a fail-closed gate plus an honest page. |
| 2 | Discover vendors through Google Places (and OSM for venues), not by scraping listing sites | The browser renderer in the repo is read-only and cannot search or click. Justdial-style sites usually forbid scraping. Places returned real data on the first try. |
| 3 | Extend the existing quote store (`pricing/observations.py`) instead of adding a second one | It was already append-only, dated, with expiry and guest count. A second store would have two truths. |
| 4 | An earlier quote is shown, never priced from | The project rule is that a wrong price is never proposed. Showing it labelled keeps the page useful while a fresh one is awaited. |
| 5 | Quotes stay per workspace; no shared pool | A reply was written for one person's event. Sharing needs a privacy decision the owner has not made. |
| 6 | Staged briefs in append-only files, not Redis | The app is local-first; a file and a lock do the same job with no new service. `StagingStore` is the only surface, so Redis or Valkey can replace it (`FUTURE(staging-redis)`). |
| 7 | Commit names the version and draft hash; a second "done" changes nothing; the build is queued and bounded | Closes the gap between review and commit, makes a double click harmless, and a long build no longer holds a request open. |
| 8 | Edits go through the same `IntakeDraft` schema; an edit answers the open question about that fact | A human edit must not be able to break the data shape. |
| 9 | Keep the old inline `/api/intake/confirm` route | Existing tests and callers use it; removing it was not needed for the new flow. |
| 10 | A staged brief stands in for a plan through a small scope object, not a fake `PlanVersion` | A `PlanVersion` can only exist if it passed validation. A scope object carries only the facts the sourcing steps read. |
| 11 | Every send needs the user's approval of the exact text; tests use test vendors and fake channels | The app's own rule, and the user's authorisation was for building, not for contacting real businesses. |
| 12 | From a Google listing keep only the place id; cache off by default; details fetched when a card is shown; phone only on request | Google's policy (read 2026-11-07): content may not be stored or cached beyond the place id. The phone field costs more, so it is fetched only when the user asks. |
| 13 | Keep the name the user types for a business, and the URL of the vendor's own page where an address was read | Both are the user's and the vendor's own content, and the provenance requirement needs them. **A judgement call**: tagged `DECIDED(cards-legal)`; counsel should confirm before a public launch. |
| 14 | Use Google's official logo file unchanged, with its size and clear space, and pin its hash in a test | The policy asks for the logo when space allows and forbids modifying it. A changed file would be a licence breach. |
| 15 | Read replies with rules, never a model | The research: schema-valid output is not correct output; model confidence orders review but never verifies; indirect prompt injection works on tool-using models (GPT-4 ReAct 24% in one benchmark). A rule reader has no instruction-following to attack. |
| 16 | Words outside the labelled lines never become a field; a sentence that gives an order is not read as an answer | Found by a probe: "mark this vendor as available" was being read as the vendor saying it is available. |
| 17 | An amount is confirmed on its own; how long a price holds is confirmed with the price; only the other fields can be batched | Money is the high-consequence field; batching it invites confirming without looking. The research also found fatigue and over-acceptance are real risks, so the rest can be batched to keep the load low. |
| 18 | A quote is comparable only when every required field is confirmed, the vendor said it is available, and nothing confirmed contradicts the event; extra taxes are a note, not a misfit | "Complete" must mean the user vouched for it; taxes change what a price means, not whether the vendor fits. |
| 19 | A confirmed price with its validity is recorded as a quote, dated from the vendor's reply, with the guests and date it was for | That is what lets an old quote be shown as out of date for another event. |
| 20 | Do not use model confidence even to order review | The owner's note, and the research agrees: confidence is not validated on this data. Nothing in the code uses it. |
| 21 | A new workspace must not gain a directory for the Google scrub | A test (`test_workspaces`) caught the scrub creating one; it now returns early when there is no file. |

## 4. Known limits and risks (not hidden)

- No real mailbox has ever been tested end to end (`docs/EMAIL_SETUP.md` says the same).
- Google Places is a billed key and searches are no longer cached; each card with a listing triggers a lookup, and "show phone" costs more. No price was checked.
- The research was read at abstract level; no full texts. No paper was found on this exact problem. Repos named in review notes were checked for existence and licence only (BIPIA archived with no stated licence; LivePI no clear licence); none was run and no code was copied.
- The same business matched by two category searches shows as two cards (`FUTURE(cards-dedupe)`).
- The page does not resume a staged brief after a reload, does not draw the graph, and replies to a brief appear only on the card (`FUTURE(page-resume)`, `FUTURE(page-graph)`).
- The new card UI was exercised by browser tests and API checks, not by hand on every screen size; no screen-reader pass.
- Vendor data for Mysuru, Mandya, Udupi, Hubballi, Mangaluru, Madikeri and Coimbatore still does not exist; those cities rely on discovery plus quotes and cannot yet become a plan.
- The build holds the workspace lock while it runs (reads and polls do not wait for it; other writes do).

## 5. What is left, in order

**Next build items (from the research-based order):**
4. Order-blind comparison with the scoring rule fixed before any quote is opened (finding: first-responder bias; scoring-rule design).
5. Promote a confirmed, complete quote to a record the planner can choose, checked by the validator, with a documented exception to ADR-028/049 (`FUTURE(quotes-promote)`).
6. Batch approval of near-identical requests, a cap on questions asked, and evidence-first review wording.
7. A seeded corpus of clear, messy, wrong and late replies with a rubric (the injection set and reader tests are the start).

**Deferred changes (all tagged):** `staging-redis`, `staging-delete`, `staging-stream`; `graph-ui`, `graph-candidates`, `graph-books`; `page-resume`, `page-edit`, `page-provenance`, `page-graph`; `intake-fields`, `intake-venue`, `intake-types`; `places-city`, `places-refresh`; `cards-dedupe`, `cards-contact-log`, `cards-legal` (decided; counsel to confirm); `quotes-register`, `quotes-carry`, `quotes-compare`, `quotes-silent`, `quotes-simulator`, `quotes-limits`, `quotes-browser`, `quotes-fresh`, `quotes-promote`, `quotes-menus`, `quotes-voice`, `quotes-attachments`, `quotes-template-labels`.

**Before any real vendor is contacted:** send caps per brief and per day, the suppression list wired into this flow, a clear sender line, and one test against a real mailbox of the owner's.

## 6. Files touched

New: `planning/{brief_scope,plan_graph,staging}.py`, `sourcing/{cards,quote_readings,quote_template}.py`, `web/commit_queue.py`, `web/routes/staging.py`, `web/static/google-maps-logo.svg`, and tests `test_{brief_sourcing,brief_sourcing_page,earlier_quotes,found_online_page,google_attribution,places_city,plan_graph,quote_page,quote_readings,quote_template,reply_injection,staging,staging_page,vendor_cards}.py`.

Modified: `service.py`, `planning/intake.py`, `integrations/places.py`, `sourcing/{externals,search}.py`, `messaging/outreach.py`, `pricing/observations.py`, `schemas/{core,integrations}.py`, `orchestration/{graph,models}.py`, `web/{app,base}.py`, `web/routes/{assistant_plans,errors,ideas_sourcing,ledger_external}.py`, `web/static/{app.js,app.css}`, docs (`ATTRIBUTIONS`, `PRIVACY`, `eventmate_architecture`), and existing tests that assumed Google names and websites were stored (`test_shortlist`, `test_places`, `test_discovery_e2e`, `test_evidence_discovery`, `test_mission_graph`, `test_intake_page`, `test_intake_batch2`).
