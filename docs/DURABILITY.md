# Durability: what survives, what is checked, what to do when a file is damaged

Built on branch `feature/durability-b` (v6 prompt Phase 1.2 to 1.5). Phase 1.1 (restart-safe assistant approvals) is the other thread's uncommitted work in `event-agent/` and is **not** in this branch. Nothing here was run against a real crash or a real disk failure: damage is simulated by truncating and editing files.

## Schema versions (`eventmate/migrations.py`)
Every JSONL line is written with `schema_version` as its first key; a plan file and a job row carry it too. A record without one is version 1, which is how every file written before this reads. On read a record is upgraded **in memory**, one step at a time; the bytes on disk are never rewritten. A plan's stored hash covers the stored schema, so it is checked before any upgrade. A record from a *newer* application is refused with a message naming the file, the line and both versions. No upgrader is registered yet (every format is still version 1); the tests register a fake version 2 to prove the path. `tests/fixtures/v1/` holds a plan, ledger, outreach and jobs in the pre-version format and must keep loading after any later change.

## `python -m eventmate verify`
Reads every store and the logs and reports, in words and without printing any record's content: unreadable or torn lines (file and line), a future schema, a record that does not match its model, a damaged job database, a plan whose history has a version missing from the middle, a version that no longer links to its parent, a plan **rolled back** (its newest file deleted: each save records the newest version and hash in `plan-heads.jsonl`), and record files that belong to no plan. It changes nothing and exits 1 on damage. Plan versions written before parent hashes existed are checked for consecutive numbering only.

## Two kinds of store
- **Authoritative** (money and send state): ledger, vendor messages and replies, standing permissions, contacts, do-not-contact lists, the job database. While any of them is damaged, **nothing is approved and nothing is sent** in that workspace (`StoreDamaged`, a 503 `store_damaged` over HTTP with a request id and no path). A send cap or a "this vendor was asked" that cannot be read must not be guessed.
- **Derived** (traces, digests, ideas, shortlists, observations, notification logs): reported, but they do not stop sending, and `repair --reset-derived FILE` starts one afresh (the old file is kept read-only).

## Repair never swaps silently
`repair --quarantine [FILE...]` copies each damaged file to `<name>.damaged-<time>` (read-only), writes the readable lines to `<name>.repaired-<time>` (a candidate) with a small JSON of what was kept and which lines were dropped and why, prints exactly which lines were set aside, and leaves the live file as it is. `repair --apply CANDIDATE --confirm N` swaps it in only if N equals the number of dropped lines, the live file has not changed since it was quarantined, and the candidate has not changed; at a terminal it asks you to type N. Until then the block on sending stays and the caps still count what the quarantined copy holds. A send whose outcome cannot be established stays `outcome_unknown` and counts against the caps (existing outbox behaviour, exercised by the test).

## Logs (1.5)
Operational logs (decisions, tool calls, LLM traces, validation failures, assistant traces) rotate at `EVENTMATE_LOG_MAX_BYTES` (20 MB) or on a new UTC day to `<name>.jsonl.<UTC time>-<n>`, keeping the newest `EVENTMATE_LOG_KEEP` (30) of each. Money and send state never rotate. Plans are never touched by log cleanup. Age-based `purge-logs` and plan-deletion scrubbing treat rotated files as logs.

## What is not done
Restart-safe approvals (1.1, other thread); consistency check that a job's plan exists is reported as an orphan but nothing repairs it; the voice and guest-list files have no per-file `verify`; rotation under many processes appending at once was not stress-tested beyond the existing append tests; rotated logs are not compressed.
