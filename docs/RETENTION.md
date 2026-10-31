# Retention and deletion

What to;doh-the event planner keeps, for how long, and how to remove it. Everything is local files and one SQLite database under the plan directory (`plans/` by default) and the log directory (`logs/`); nothing here is sent anywhere.

## Delete a plan

Forgets a plan and everything it owns. It cannot be undone.

```sh
python -m eventmate delete-plan <plan_id> --confirm <plan_id>
```

```
DELETE /api/plans/<plan_id>        header X-EventMate-CSRF, body {"confirm": "<plan_id>"}
-> 200 {"deleted": "<plan_id>", "removed": {"plan_versions": 2, "message_and_ledger_files": 7, "jobs": 1}}
-> 400 confirm does not repeat the plan id      -> 404 no such plan
-> 409 a message for this plan is being sent right now; try again in a moment
```

Removed: every plan version; the budget ledger; vendor requests, replies and the standing permission (`mandates/`); quote-request jobs and their history (`jobs.db`, rewritten so deleted rows are not recoverable from the file); ideas and notes; saved price leads; digests; the shortlist; the community checklist. The assistant also forgets the plan's conversation memory, cached answers and approved annotations.

Kept on purpose, because they are not per plan:

| Kept | Why |
|---|---|
| do-not-contact list (`suppressions/`) and unmatched-mail records | Forgetting them could let a vendor who asked to stop be messaged again |
| Discovered businesses and their source-backed contacts | They belong to the vendor search, not to one plan |
| Vendor price observations | Market facts that carry no plan id |
| Run logs (below) | Expire by age |

The deletion itself is recorded in the decision log by plan id only, with counts and no plan contents. If a delete fails part-way, run it again: plan versions are removed last, so the plan still lists until the delete has finished.

## Logs

Decision, tool-call, model-trace, validation and assistant logs are kept for **90 days** by default. Set `EVENTMATE_LOG_RETENTION_DAYS` to change it (minimum 1). Nothing expires on its own; run

```sh
python -m eventmate purge-logs            # uses the setting
python -m eventmate purge-logs --days 30
```

from a scheduler (cron, a systemd timer) if you want it automatic. Only files named like a run log are touched; symlinks and other files are left alone. Logs hold the brief you typed (placeholders mask phone numbers, emails and card-like numbers; names and addresses are not masked), so delete a plan and purge logs if a brief should not outlive its use.

## Conversations

Vendor conversations live with their plan and go when the plan is deleted. They have no separate expiry.

## Not covered

Backups or copies you made of these directories; the mailbox itself (messages already sent or received stay in the mail account); anything a vendor or model provider received. Deleting here does not reach those.
