# Workspaces: one for each visitor, no sign-up

Status: **built and tested on branch `feature/workspaces`; not run on a real host, behind a real proxy or in a real browser.** The response shapes below are what the backend emits today; the frontend has not been changed to use them (it stores no identity today), so treat the "frontend must" lists as proposals the owner takes to the frontend.

## What it is
In `EVENTMATE_MODE=shared` every visitor gets a workspace of their own on their first page load. A workspace is a directory (`<EVENTMATE_DATA_DIR>/workspaces/ws-<16 hex>/plans` and `/logs`) with its own plans, ledger, guest lists, vendor messages, jobs, quotes, contacts, annotations, voice notes, traces, the assistant's memory and pending approvals, and its answer cache. Nothing is shared between two workspaces except the read-only seed data (vendors, rules, knowledge). That is the isolation: another workspace's plan id is simply not in this workspace's store, so every lookup of it ends in the same `404 {"error":"not found","code":"not_found"}`, byte for byte the answer for an id that never existed (the two-workspace suite checks this on every plan-scoped route, 58 of them, generated from the app's route table).

Local mode (the default) is unchanged: one implicit workspace named `local`, your existing directories, no cookie.

## The credential
- Made by `GET /api/session` when the request has no valid cookie. 192 bits from `secrets.token_urlsafe(24)` (32 characters). Only its SHA-256 is stored (`workspaces.db`); it is never stored, logged or returned in JSON.
- Delivered as cookie `todoh_ws`: `HttpOnly`, `SameSite=Strict`, `Secure`, `Path=/`, no `Domain`, `Max-Age` one year, pushed forward by every `GET /api/session`.
- Any other route with a missing, malformed, unknown, rotated or revoked credential gets the generic `404 not_found` and **creates nothing**. The page, its static files and `/healthz` need no workspace. Routes with their own authentication (`/api/owner/status`, `/api/wall/*`) are exempt by an exact prefix list (`WORKSPACE_EXEMPT`).
- The CSRF value is `HMAC-SHA256(EVENTMATE_SECRET, "csrf:" + workspace id)`, so each workspace has its own; `GET /api/session` returns it.
- Creating workspaces is bounded: 5 an hour per address and 60 an hour in all (`429 workspace_rate_limited`), at most `EVENTMATE_MAX_WORKSPACES` (default 1,000; `503 workspace_limit`), and `sweep_idle_empty` removes workspaces that never saved anything. Measured cost of an empty workspace: one database row (under 512 bytes) and three empty directories (a few KB of disk, about 12 MB at the cap). No file exists until the workspace first saves something.

## `GET /api/session` (existing route, one added field)
```
{"csrf": "<43 chars>", "today": "2026-11-01", "roles": ["organizer"], "guest_lists": [...],
 "workspace": {"id": "ws-0123456789abcdef", "created": "2026-11-01T07:40:00Z", "mode": "shared"}}
```
Errors on this route: `429 workspace_rate_limited`, `503 workspace_limit`.

### What the frontend must do (proposed, not built)
1. First load: call `GET /api/session` (the browser keeps the cookie by itself; `fetch` same-origin sends it). Keep `csrf` in memory and send it as `X-EventMate-CSRF` on every write (as today).
2. A `404` with `code: "not_found"` on a route that used to work means the credential is gone (cleared browser data, rotated, revoked or the workspace was deleted): show a clear state and offer to start a fresh workspace by calling `GET /api/session` again. Never resend a send, approve or spend action automatically.
3. A `403` on a write means the CSRF value is stale: call `GET /api/session` once and let the person retry.
4. Show the quota and limit codes as plain messages: `workspace_quota` (507), `plans_per_day`, `lookup_quota`, `rate_limited`, `workspace_share`, `busy` (429), `sending_disabled` (403).

## Delete, export, import
| Route | What it does | Notes |
|---|---|---|
| `DELETE /api/plans/<id>` with `{"confirm": "<id>"}` | existing; now also removes the plan's lines from the logs and writes a tombstone | refused with 409 while a message for it is being sent |
| `DELETE /api/workspace` with `{"confirm": "delete"}` | shared mode only: removes the credential, every file (each overwritten, then unlinked), the row; clears the cookie; `{"deleted": true}` | After it the old cookie gets the generic 404, so a second call cannot be told apart from a miss; local mode answers 404 |
| `GET /api/workspace/export` | one zip: manifest, plan versions exactly as stored, ledger, saved leads, guest lists, outreach history, jobs history | needs the CSRF header; no quota; plaintext and flagged as containing personal data |
| `POST /api/workspace/import` (multipart field `archive`) | validates, then restores plans, ledger, leads and guest lists; answers `{"imported": {...}, "remapped": {...}, "hashes_verified": true, "history_kept": {...}}` | 20 MB; hostile-input rules below |

A **tombstone** is one line `{"kind", "id", "deleted_at"}` and nothing else (`<plans dir>/tombstones.jsonl` for a plan, `<data dir>/tombstones.jsonl` for a workspace). It is the only place the id of a deleted plan or workspace remains.

### What an export holds and does not
Holds: every plan version as stored, with its hash; the ledger; saved leads; guest lists (names); the outreach and inbound history and the jobs, as history. Does not hold: any credential, key or recovery material (a workspace stores none), logs, traces, the assistant's memory, caches, send caps and the do-not-contact lists (they are not per plan).

### How an import is treated
The archive is hostile input. Member names must match a fixed grammar and are never used as paths; absolute paths, `..`, backslashes, drive letters, NUL tricks, symlinks, devices, encrypted members and duplicate names are refused. Limits are enforced on what is read, not on what the header claims: 20 MB archive, 2,000 members, 5 MB a member, 60 MB in all, a ratio of 100 to 1 for members over 64 KB, 200 plans, 5,000 records a file. The manifest must list exactly the members present with their SHA-256; every plan version must pass its own hash and the plan model; every record must pass the model the application uses; **nothing is written until all of it has passed.** Imported outreach and jobs are kept as history files under `imported/<id>/` that nothing in the sending machinery reads, so an archive can never cause a message to be sent: the person drafts and approves afresh (this is stricter than "restored but inert"). A plan id that already exists is replaced by a new one everywhere it appears and the new versions are hashed again; without a collision the hashes are identical to the originals.

Known limits: the zip reader is Python's standard `zipfile`; ratio and size checks are applied to declared sizes and the real byte count, and a lying header can only make a member fail its CRC. Ideas, shortlist, community checklist and digests are not exported (they can be regenerated).

## Quotas and caps in shared mode
`EVENTMATE_WS_MAX_BYTES` (50 MB; `507 workspace_quota` on a write, reading and deleting are never refused), `EVENTMATE_WS_MAX_PLANS_PER_DAY` (20; `429 plans_per_day`), `EVENTMATE_WS_LOOKUPS_PER_HOUR` (60 outside lookups such as places, search and weather; `429 lookup_quota`; never a silent fallback to another source). The model limits are in `docs/MODEL_INPUTS.md`.

## No sending in shared mode
Every guest workspace is built with unconfigured email and WhatsApp channels, so nothing a visitor does can reach the owner's mailbox or gateway, and the routes that send or arrange sending (`outreach/.../approve`, `.../followup`, `autoreply`, `inbox/poll`, `jobs/tick`, `jobs/.../approve-requests`, `.../approve-nudge`, `notify/propose`, `notify/confirm`) answer `403 {"code": "sending_disabled"}`. Two independent layers. Enabling sending for guests would need a global owner-level send gate so the caps (30 a day, 3 per vendor per 24 hours) are counted across all workspaces; that gate is **not built**, so there is no setting to turn guest sending on.

## Open owner decisions (nothing below is built or on)
**Recovery.** The credential lives in an HttpOnly cookie that page scripts cannot read, so clearing the browser loses access unless the owner picks a recovery route. The options, from the v6 prompt: a recovery code shown once; export file plus import (this build already has export and import, so it works today as a manual backup, not a sync); device pairing with a short one-time code; a passkey; an email or messaging link (needs sending and a stored address; not assumed). Rules for whichever is chosen: shown once and stored only as a hash, expiring and single use, the same answer for wrong, expired and unknown codes, rate limited, rotate and revoke supported (the registry already supports both), logged without secrets. **Device sessions** (one revocable credential per device) and a view-only helper credential are likewise not built.
**Idle expiry** of workspaces that do hold data (it needs a notice channel and a recovery route first). Only empty workspaces are swept.
**Legacy data.** In shared mode, `python -m eventmate.workspaces_admin import-legacy ID --plan-dir DIR` copies an existing install's plans into the workspace the owner names. Nothing is ever assigned to a visitor automatically.

## Honest limits
- Per-workspace objects are kept in memory for at most 128 workspaces (least recently used are dropped and rebuilt from disk). What lives only in memory (a pending assistant proposal, voice session, the answer cache) is lost with them, and the durable approvals work (Phase 1.1) is not in this branch.
- The built-in server does not stop a client that sends one byte just inside its idle timeout; a reverse proxy is required (docs/HOSTING.md).
- The workspace directory and credential hashing were tested with fake clocks and a local server, not on a real host. SQLite file permissions are 0600; disk encryption of the host is not verified.
- Plan versions still have no parent-hash chain: deleting the newest version file silently rolls a plan back. That is the durability phase (v6 1.4), not this one.
