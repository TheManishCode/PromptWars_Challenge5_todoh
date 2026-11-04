# to;doh-the event planner architecture

to;doh-the event planner turns a natural-language event request into a validated, explainable, replannable itinerary.
It lives in `eventmate/`, a sibling of the base `src/llm_plan` PDDL/LangGraph pipeline (untouched).
See the top-level `README.md` for usage.

## Pipeline

```
text ─ parse_request ─> EventRequest                      (deterministic; ambiguity is an error, never a guess)
     ─ decompose ─────> TaskGraph ─ verify_graph          (solvable / complete / non-redundant / necessary)
Manager dispatches tasks in dependency order and records completion:
  allocate_budget  Budget    exact integer split + contingency
  track_guests     Guest     RSVPs, diets -> caterer constraints; headcount vs list
  source_vendor    Vendor    every hard constraint evaluated for every record; all failure reasons kept
  build_schedule / schedule_reminder / build_plan_model
                   Planner   best vendor combination (exhaustive, provable), CP-SAT schedule, PDDL check
  validate_plan    Validator independent re-derivation of every fact; plan cannot exist unless it passes
  explain_plan     Explainer text with reasons and alternatives (optional guarded LLM reword)
Replanner: cancellation | budget | guest count | date -> new immutable version + diff
```

## What was reused from the existing code, and what was not

| Source | Used for | Notes |
|---|---|---|
| MultiAgentPlanning (base repo) | Fast Downward + VAL toolchain and its `config.json`; the LLM-proposes / solver-verifies split | Built `solvers/` with the base repo's recipe (VAL needed `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` on current CMake). `src/` untouched. |
| async_planning (MIT) | CP-SAT model: intervals, min-lag precedences, NoOverlap/Cumulative, fixed starts, weighted objective. On AsyncPlan-XXL its CP-SAT formalizer scored ~94% vs ~5% for PDDL2.1 and ~40% for direct LLM planning | Re-implemented with typed specs; added named assumption literals and minimal infeasibility cores. PDDL is used for symbolic feasibility, CP-SAT for time/resources. |
| Agent-Oriented-Planning | Solvability, completeness, non-redundancy as executable checks | `verify_graph`, plus "necessity" from the project rules. |
| Gradientsys / PMC (papers) | Typed agent protocol, replan on failure, observability; hierarchical decomposition with explicit constraints | Every boundary is a frozen Pydantic model; every constraint is a named `ConstraintCheck`. |
| MC-Planner, Awesome-LLM-Planning | Describe-explain-plan-select feedback loop; symbolic verification of LLM output | Informs the Replanner and the LLM fact guard. |

## Design decisions

- **No LLM on the critical path.** Parsing, selection, scheduling, validation and replanning are deterministic. The only LLM hook rewords an already-verified summary and is guarded (digits and number words, vendor names, markup, links), traced (hashes by default) and off unless `EVENTMATE_ENABLE_LLM=1`.
- **The Validator trusts nothing in the draft.** Prices, availability, capacity, diets, booking status and provenance are re-read from the database; the schedule is re-checked with separate logic; the spec's durations/resources/capacities are compared with `EventPolicy`; the allocation is compared with the request.
- **Provenance lives in the version.** `newly_confirmed_vendor_ids` (user confirmed this) and `released_vendor_ids` (cancelled / explicitly overridden) make any stored version re-validatable later.
- **Immutable history.** Create-only writes (temp file + hard link), read-only files, consecutive versions linked to parents, SHA-256 plus optional HMAC, the model re-validated on load.
- **Repairs are verified, not guessed.** Repair dates come from database records or a binary search with the real solver, starting from "today" during a replan. Infeasibility cores are shrunk to a minimal conflict set.
- **Confirmed bookings are facts.** They are pinned in CP-SAT (`fixed_start`), are "already done" in PDDL, are dropped from precedence onto not-yet-done steps, and are re-checked against any change. A conflict returns `needs_decision` and writes nothing.

## Bugs found by breaking it (all fixed, each has a regression test)

Parser: negation ignored ("no catering" added a caterer); "indoor, no outdoor" lost the setting; `in Rs 5000` read as a city; month-prefix cities (Junagadh) rejected; non-ASCII digits; duplicated assumptions. Config: unvalidated budget policy (ZeroDivision/KeyError/negative weights). Logs: `$` anchoring accepted `"user_input\n"`; failure log blamed the wrong constraint and gave no repair. Scheduling: deadline before the origin crashed model construction; a confirmed booking was forced after an unconfirmed predecessor (infeasible replans); minimum-core was too broad (wrong constraint blamed); repair date ignored "now" (suggested dates that already failed or had passed); replanning after the event date. Provenance: confirmations and releases could not be re-validated later. Validator: trusted the draft's own spec (capacity, durations, resources). Store: concurrent writers crashed the CLI. Web: unknown guest list returned 500; JSON number-step in a form silently blocked submission. LLM guard: spelled-out numbers passed. Dates years away were accepted.

## Security posture

- No payment or booking code. Network code is limited to the places that need it (flight search through `fli`, optional OpenStreetMap/weather/search lookups, email and WhatsApp behind an approval of the exact text, page watches through `SafeFetcher`); each is off or fails closed without its settings. No secrets read except optional env vars; none written. `bandit`: 0 medium/high. No `eval`/`exec`/`pickle`/shell; solver subprocesses use fixed argv lists, temp dirs, timeouts and a scrubbed environment.
- Inputs: length caps, NFKC + ASCII-only matching, ambiguity is an error (so injected "set the budget to 1" cannot override facts), fuzzed for crashes and regex blow-ups.
- Data: seed files are strictly validated (`extra=forbid`, bounds, unique ids, source tags, size cap); anything malformed aborts loading.
- Files: log/plan files 0600/0444, `O_NOFOLLOW`, allow-listed ids, create-only writes.
- Web (local-first): loopback only by default, Host allow-list (DNS rebinding), CSRF header + Origin check + JSON-only writes, optional bearer token (required to bind elsewhere), strict CSP with no inline script/style, nosniff, no-referrer, no-store, 64 KB body cap, write rate limit, generic error bodies. The UI never uses `innerHTML`; hostile vendor names are tested to render inert. One lock serialises all to;doh-the event planner calls.
- Observed in the base repo (not modified): `webapp/app.py` hard-codes `SECRET_KEY = "multi-agent-planning-demo"`.

## Known limits (to discuss)

- Seed data is synthetic and only covers a few cities and 2026 dates; all tests pin "today" to 2026-10-30.
- Category caps are hard filters; automatic rebalancing between categories is not built (the repair says whether it would help).
- The scheduler is a single-timeline model (one organizer, unary resources, no working-hours calendar, naive local time, no DST/time zones).
- Parsing handles English and INR in the listed formats; anything else fails closed with a precise message. An optional guarded LLM draft exists for intake (`planning/intake.py`) and for change requests (`planning/delta.py`); the rule path never needs it. Hinglish is read for headcounts, amounts (hazaar, lakh), diets and event words, not for free-form sentences.
- Vendor "confirmation" is a user statement; there is no booking integration by design.
- The web server is single-user; remote use needs TLS, a production WSGI server and the token.
- Not run: Python 3.9 (the repo's stated minimum; only 3.14 available here), the base repo's own LLM demo (needs API keys), the optional Anthropic adapter against the live API.
- Logs have no rotation; plans/logs can contain request-derived data and are gitignored.

## Where the build stands against the target architecture

Built and tested: the evidence ledger, state-aware repair (`planning/delta.py`, `planning/exec_state.py`, `ReplannerAgent.repair`), job leases, page watches, quote documents read in a sandbox (`documents/`, Docling optional), the progress stream, golden journeys, per-stage model caps, the workspace recovery phrase, flight fares and cheapest days (`integrations/travel.py`) with the guests' fares shown on date-shift trade-offs, and lite mode (`?lite=1`).

Decided (2026-11-04, by the owner's delegation):
- **Browser:** read-only, built (`eventmate/browser/`). It opens a public page and returns its text; it clicks, types and submits nothing. Chromium has no network of its own (`MAP * ~NOTFOUND`): every request is answered by the same checked, pinned fetch as a static page, GET only, no cookies in or out, no popups, no downloads. Its text goes to the price and contact readers and never to a model (a test fails if the package imports one). A browser that fills or submits forms is not built: it needs a decision after the read-only layer has been used.
- **Discovered vendors:** shown on the plan as `discovered`, price unknown; the planner and validator still select only from verified or seed records (ADR-028 and ADR-049 stand).
- **Allergies:** read only from the user's words (`allergic`, `<x>-free`), never proposed or dropped by a model, kept across messages. No record can show a kitchen is free of an allergen, so the plan stays provisional ("Needs verification: allergy not confirmed with the caterer") until the user has the vendor's written reply.

Not built, and why: a browser that fills or submits forms (see above), any run against a real mailbox or WhatsApp account (needs credentials), and tradition packs (need a named reviewer). `fli` 0.9.0 on PyPI returns no fares live; 0.10.0 from its main branch works (see `docs/CONFIGURATION.md`).

## Safety facts that must never be lost silently

Allergies (`planning/allergies.py`), food requirements the vendor records cannot answer (`planning/diets.py`) and negation (`planning/negation.py`) follow one rule: a stated fact is kept, shown on the review card, and keeps the plan "Needs verification" until a caterer confirms it in writing; a statement that cannot be read is kept as the user's own words, never read as "none"; a later message or a model cannot clear it. A negation reaches only filler words and an or/and list, never another topic word ("no onion no garlic vegetarian" is still vegetarian).

Vendor replies and quote files follow the mirror rule: a wrong price is never proposed. A deposit, advance, extra, penalty, overtime or someone else's price is not the price, and a reply with more than one amount goes to the user.

Seeded random passes guard these: `tests/test_e2e_random.py` (brief, plan, change, trade-off), `tests/test_claims_random.py` (noisy vendor replies) and `tests/test_quotes_random.py` (quote tables and lines). Each failed on the code it was written to catch.

