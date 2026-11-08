# What is stored, where, for how long, and how to delete it

This is the plain statement for a person using the planner. It describes this application's own files; it says nothing about your mailbox, your phone or any outside service you use with it.

**The application writes plaintext.** Plans, ledgers, guest names and vendor messages are ordinary JSON files. Disk encryption on the machine is not checked by the code; if it matters, turn on your operating system's disk encryption.

| What | Where (local mode) | How long | How to delete |
|---|---|---|---|
| Plan versions (the event, vendors, costs, schedule) | `plans/plan-<id>-vNNNN.json`, read-only | until you delete the plan | `DELETE /api/plans/<id>` or `python -m eventmate delete-plan <id> --confirm <id>` |
| Briefs waiting for your approval (the words you typed and what the reader understood) | `plans/staging/staged-<id>.jsonl` | 24 hours if you never press Done; 7 days after a build finishes; removed when the next brief is started | removed by that sweep, or with `DELETE /api/workspace` |
| Ledger (quotes, payments and refunds you recorded; nothing is ever paid by this application) | `plans/ledger/` | with the plan | with the plan |
| Guest list (names, party size, RSVP, diets; no contact details) | `plans/guests/`, owner-only | with the plan; or `DELETE /api/plans/<id>/guests` | overwritten, then removed |
| Vendor messages you approved, replies, jobs, saved leads, ideas, digests | `plans/outreach/`, `inbound/`, `jobs.db`, `leads/`, `ideas/`, `digests/` | with the plan | with the plan |
| Discovered businesses | `plans/externals/` | kept on purpose. **From Google listings only the place id is kept** (Google's policy); the name, address, rating, link, website and phone are fetched again whenever a card is shown and are not stored. A name is kept only if you typed or confirmed it for your own record | not deleted; an earlier version's stored listing content is removed at start-up |
| Do-not-contact lists, vendor price observations | `plans/` | kept on purpose: forgetting a refused vendor would let it be messaged again; observations carry no plan id | not deleted with a plan |
| Logs and traces (decisions, tool calls, assistant questions and answers with contact details masked) | `logs/` | 90 days by default (`python -m eventmate purge-logs`); lines about a plan are removed when you delete it | with the plan, or by age |
| Voice notes | not kept unless you turn that on; audio is deleted right after it is transcribed | the period you set | with the plan |
| Tombstones | `plans/tombstones.jsonl` | for ever: one line `{kind, id, deleted_at}` per deleted plan and nothing else | not deleted: it is the proof of deletion |

**In shared mode** each visitor's data is in their own workspace directory and removed by `DELETE /api/workspace` (credential, every file, the database row; a tombstone with the workspace id and the time remains). Export (`GET /api/workspace/export`) gives you a zip of your data first; it is plaintext and contains vendor contact details and guest names, so keep it as private as the data it holds.

**Shared earlier quotes (dormant).** One store, shared by every workspace, can hold what a vendor quoted the last person (`pricing/shared_quotes.py`). It is fed only by a reply that arrives through a configured mailbox. No shipped mode has one (hosted visitors cannot send or receive mail; local mode has no shared store), and a reply a user pastes is never written to it, so in practice it stays empty. If it is ever fed it holds: the vendor's address as a hash or a Google place id, the kind, the amount, per plate or total, the guest count to the nearest ten, the **month** of the event (never the day), the day the quote was given, and when it stopped holding. It holds no name, no contact details, no words, no owner. Another person is told "a different month than your event", never which. Records are dropped 90 days after the quote stopped holding. There is no per-person removal: nothing in a record says whose it was, so none can be found; `EVENTMATE_SHARED_QUOTES=0` stops it, and the operator can delete `<data>/shared/shared_quotes`. Owner decision, see `docs/DECISIONS.md` D-009.

**What deletion cannot reach.** Backups and exported archives you or the host's operator made, a copy in your browser, anything an outside service already received (search, maps, weather and model providers see the text sent to them: a masked question, a city, a date), and any mailbox. Deletion removes this application's files only. If you keep backups, write down how long, because a restore brings deleted data back unless the tombstones are applied to it.

**What models see.** Optional language models receive only the following: contact details are replaced by placeholders first, guest names and contact details are never sent, vendor reply text is never sent.
