# What is stored, where, for how long, and how to delete it

This is the plain statement for a person using the planner. It describes this application's own files; it says nothing about your mailbox, your phone or any outside service you use with it.

**The application writes plaintext.** Plans, ledgers, guest names and vendor messages are ordinary JSON files. Disk encryption on the machine is not checked by the code; if it matters, turn on your operating system's disk encryption.

| What | Where (local mode) | How long | How to delete |
|---|---|---|---|
| Plan versions (the event, vendors, costs, schedule) | `plans/plan-<id>-vNNNN.json`, read-only | until you delete the plan | `DELETE /api/plans/<id>` or `python -m eventmate delete-plan <id> --confirm <id>` |
| Ledger (quotes, payments and refunds you recorded; nothing is ever paid by this application) | `plans/ledger/` | with the plan | with the plan |
| Guest list (names, party size, RSVP, diets; no contact details) | `plans/guests/`, owner-only | with the plan; or `DELETE /api/plans/<id>/guests` | overwritten, then removed |
| Vendor messages you approved, replies, jobs, saved leads, ideas, digests | `plans/outreach/`, `inbound/`, `jobs.db`, `leads/`, `ideas/`, `digests/` | with the plan | with the plan |
| Do-not-contact lists, discovered businesses, vendor price observations | `plans/` | kept on purpose: forgetting a refused vendor would let it be messaged again; observations carry no plan id | not deleted with a plan |
| Logs and traces (decisions, tool calls, assistant questions and answers with contact details masked) | `logs/` | 90 days by default (`python -m eventmate purge-logs`); lines about a plan are removed when you delete it | with the plan, or by age |
| Voice notes | not kept unless you turn that on; audio is deleted right after it is transcribed | the period you set | with the plan |
| Tombstones | `plans/tombstones.jsonl` | for ever: one line `{kind, id, deleted_at}` per deleted plan and nothing else | not deleted: it is the proof of deletion |

**In shared mode** each visitor's data is in their own workspace directory and removed by `DELETE /api/workspace` (credential, every file, the database row; a tombstone with the workspace id and the time remains). Export (`GET /api/workspace/export`) gives you a zip of your data first; it is plaintext and contains vendor contact details and guest names, so keep it as private as the data it holds.

**What deletion cannot reach.** Backups and exported archives you or the host's operator made, a copy in your browser, anything an outside service already received (search, maps, weather and model providers see the text sent to them: a masked question, a city, a date), and any mailbox. Deletion removes this application's files only. If you keep backups, write down how long, because a restore brings deleted data back unless the tombstones are applied to it.

**What models see.** Optional language models receive only the following: contact details are replaced by placeholders first, guest names and contact details are never sent, vendor reply text is never sent.
