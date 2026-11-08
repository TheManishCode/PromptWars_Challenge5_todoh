# Changes we have decided on and not built

Each one is also a comment in the code it touches, tagged `FUTURE(<topic>)`. Find them all with `grep -rn "FUTURE(" eventmate docs`. When one is built, delete its comment and its line here.

| Tag | What | Where |
|---|---|---|
| `staging-redis` | Redis or Valkey store for staged briefs when hosted for many users | `planning/staging.py` |
| `staging-delete` | Deleting a plan drops its staged brief | `planning/staging.py` |
| `staging-stream` | Push build state over the existing event stream instead of polling | `planning/staging.py` |
| `graph-ui` | Draw the plan graph ("why this plan") | `planning/plan_graph.py` |
| `graph-candidates` | Every vendor considered, with the constraint that removed it | `planning/plan_graph.py` |
| `graph-books` | One source for the task-to-vendor-category link | `planning/plan_graph.py` |
| `page-resume` | Find the staged brief again after a reload | `static/app.js` |
| `page-edit` | Typed edits for event type, diets, allergies, services | `static/app.js` |
| `page-provenance` | Per-field source on the review card | `static/app.js` |
| `page-graph` | Show the graph in the page | `static/app.js` |
| `intake-fields` | Access needs, allergy counts, diet group sizes, time of day, per-day catering | `planning/intake.py` |
| `intake-venue` | A venue the user already has | `planning/intake.py` |
| `intake-types` | Event types the engine does not have (farewell, reunion, pooja...) | `planning/intake.py` |
| `places-city` | Restrict Places to the city's coordinates | `integrations/places.py` |
| `quotes-register` | Save discovered places as businesses when shown | `sourcing/search.py` |
| `quotes-compare` | A recommended caterer + venue + photographer combination for the budget (per-category ranking exists) | `sourcing/search.py` |
| `quotes-silent` | Vendors that do not reply; WhatsApp or phone fallback | `sourcing/search.py` |
| `quotes-simulator` | Simulated vendor replies to the user's own addresses | `sourcing/search.py` |
| `quotes-limits` | Send caps, suppression list, sender identity | `sourcing/search.py` |
| `quotes-browser` | Read-only browser for sites that need scripts | `sourcing/search.py` |
| `quotes-fresh` | "Getting a fresh quote" wording and the old-to-new change | `pricing/observations.py`, `static/app.js` |
| `places-refresh` | Refresh place ids older than 12 months | `sourcing/cards.py` |
| `cards-legal` | DECIDED to keep the user-typed name and the vendor-page URL as the user's and the vendor's own content; counsel to confirm before a public launch | `sourcing/cards.py` |
| `cards-dedupe` | One business matched by two searches is two cards | `sourcing/cards.py` |
| `cards-contact-log` | Record a contact made outside the app, and paste the reply in | `sourcing/cards.py` |
| `quotes-menus` | Menu tiers and service styles as separate quotes | `sourcing/search.py` |
| `quotes-voice` | Voice-note replies, and calls typed in through the same parser | `sourcing/search.py` |
| `quotes-attachments` | Link a PDF or image quote's lines to the same fields | `sourcing/search.py` |
| `quotes-template-labels` | Labels are only ever added, never reworded; version the template if it must change | `sourcing/search.py` |
