# The signature wall (Phase 8)

Visitors leave a name on the left wall of the 3D room after solving a hidden puzzle. The name stays until the site closes, unless it is removed. A returning visitor can find
their own name among the others for a Hall of Fame entry. No login, no fingerprint, zero cost. The puzzle, the picking, the camera and the drawing are frontend work.

**Status (2026-11-01, branch `feature/wall-and-pentest`, not merged):** the backend is built and tested solver-free (see HANDOFF for counts). Nothing here has been run against the real
frontend or on a real phone, and the wording of the public-name notice is the owner's decision (WALL_PRIVACY.md).

## What it is

- Code: `eventmate/wall/` (`names.py`, `store.py`, `routes.py`, `limits.py`, `layout.py`), owner CLI `eventmate/wall_admin.py`, blocklist `eventmate/data/wall_blocklist.txt`.
- Its own SQLite file (WAL, `synchronous=FULL`): `$EVENTMATE_WALL_DB`, default `<plan dir>/../wall_data/wall.sqlite3` (so deleting a plan can never touch it). `EVENTMATE_WALL=off` removes the routes.
- It imports no plan, guest, draft or workspace store (a test parses every import). No wall response contains plan data. Deleting a plan or workspace leaves the wall unchanged. Private exports never include wall data.
- **A persistent disk is required.** A host with an ephemeral disk loses the wall. Use `backup` (below) on a schedule you choose; the hosting decision is the owner's.

## Authentication: the wall does not use the private session

| | Private routes (`/api/...`) | Wall routes (`/api/wall/...`) |
|---|---|---|
| Bearer token (`EVENTMATE_WEB_TOKEN`) | required when set | **not required** (default). `EVENTMATE_WALL_BEARER=1` keeps it: then a missing token is `401 bearer_required` |
| CSRF | `X-EventMate-CSRF` (process value from `GET /api/session`) | `X-EventMate-Wall-CSRF`, from `GET /api/wall/session` only |
| Credential | none (local) | cookie `todoh_wall` |
| Host, Origin, JSON-only, 64 KB body cap | yes | yes |

- The exemption is by **exact registered route rule**, 14 rules, no prefix match. Any other `/api/wall/...` path is `404 {"error":"not found","code":"not_found"}` and is **not** exempt from the bearer token (a test proves it).
- The wall CSRF value is `base64url(HMAC-SHA256(key = the credential, "todoh-wall-csrf-v1"))`: bound to the cookie, derivable only by someone holding it, nothing stored. The private CSRF does not authorise a wall write and the wall value does not authorise a private write (tested). No wall response contains the private value.
- The credential is 256 random bits (`secrets.token_urlsafe(32)`), stored only as a SHA-256 hash, never logged. Cookie: `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`, one year, `Secure` over HTTPS.
- **Honest limits.** The credential identifies a browser profile, not a person. Clearing cookies, a private window, another browser or device gives a fresh visitor. No fingerprinting is added. The real brakes against flooding are validation, rate limits, daily caps and moderation. The public `mark_id` proves nothing.

## The 14 routes

Errors: `{"error": "<text>", "code": "<code>"}`. Codes: `bad_request`, `invalid_name`, `already_marked`, `code_exists`, `not_found`, `unauthorized`, `bearer_required`, `rate_limited`, `wall_closed`, `not_eligible`, `not_ready`, `round_expired`, `round_used`, `round_stale`.
A wall write without a valid cookie and wall CSRF is `401 unauthorized`. The frontend then calls `GET /api/wall/session` again and shows a retry choice; **it never resends a write automatically.**

Public reads (no cookie dependence, `ETag`, `Cache-Control: public, max-age=60`; the Hall `max-age=30`; byte-identical with or without a cookie, never a `Set-Cookie`):

1. `GET /api/wall/meta`
```json
{"schema_version":1,"accepting":true,"section_count":2,"visible_count":57,"hall_unlocked":true,
 "hall_period":{"id":"2026-11","kind":"month","starts_at":"2026-10-31T18:30:00Z","ends_at":"2026-11-30T18:30:00Z"},
 "limits":{"name_min":2,"name_max":24},"round":{"window_ms_default":5000,"untimed_available":true}}
```
2. `GET /api/wall/sections/<id>?after_slot=<n>&limit=<m>` (limit default 100, max 300)
```json
{"section_id":0,"layout_version":1,"capacity":48,"slots":[{"slot":0,"mark_id":1,"name":"Asha"}],"next_after_slot":null}
```
Unknown id (including a number too large for the database): `404 not_found`. Visible marks only.
3. `GET /api/wall/hall?order=newest|fastest&cursor=<c>&limit=<m>` — `newest` (default 50, max 100, `rank` null, `next_cursor` for the next page) or `fastest` (top 5, max 10, `rank` 1-based computed at read time, `period` object, `next_cursor` null):
```json
{"entries":[{"mark_id":4,"name":"Player1x","elapsed_ms":400,"rank":1}],"next_cursor":null,"period":{"id":"2026-11","kind":"month","starts_at":"…","ends_at":"…"}}
```
Only opted-in, visible marks; ordered by best time, then time of the win, then `mark_id`.

Private reads (`Cache-Control: no-store`, `Vary: Cookie`):

4. `GET /api/wall/session` → `{"csrf":"…","has_mark":false,"mark_status":null}`. Sets the cookie if the request has no valid one; never replaces a valid one. Rate limited (`429 rate_limited`).
5. `GET /api/wall/me` (needs the cookie, else `401`) →
```json
{"has_mark":true,"mark":{"mark_id":1,"name":"Asha","status":"visible","section_id":0,"slot":0},"hall":false,"hall_eligible":false,"best_elapsed_ms":null,"has_recovery_code":false}
```
Pending: `section_id` and `slot` are null. Removed: `{"mark_id":1,"status":"removed","name":null,"section_id":null,"slot":null}`. No mark: `"mark": null`.

Writes (cookie + `X-EventMate-Wall-CSRF`, JSON body, `Cache-Control: no-store`, `Vary: Cookie`):

6. `POST /api/wall/marks` `{"name":"Asha"}` with header `Idempotency-Key` (8 to 128 of `A-Za-z0-9_-`) → `201 {"mark_id":1,"status":"pending"}`; `409 already_marked {"mark_id","status"}`; `400 invalid_name`; `503 wall_closed`; `429 rate_limited`. A retry with the same key returns the original result and creates nothing (kept 24 hours). A refused (409) attempt is not stored.
7. `POST /api/wall/me/recovery-code` `{"replace_existing":false}` → `200 {"recovery_code":"ABCD-EFGH-IJKL-MNOP-QRST-UV"}` (shown once; only its hash is stored); `409 code_exists`; `404` without a mark. `replace_existing:true` invalidates the previous code. A lost response means issuing a new code explicitly.
8. `POST /api/wall/recover` `{"recovery_code":"…"}` → `200 {"mark":{…as /me},"has_recovery_code":false}` and a new cookie. One transaction: the code is consumed, the mark's row gets a new credential, the previous cookie stops working. Any failure is the same `404 not_found`; 10 failures per address and 100 per hour overall then `429 rate_limited`.
9. `POST /api/wall/me/remove` `{}` (cookie) or `{"recovery_code":"…"}` → `200 {"removed":true}`. Blanks the name, clears the Hall flag and the person's times, keeps the credential linked (it still counts as marked). Same reply when already removed; generic `404` for nothing to remove or a wrong code; a code-based removal consumes the code.
10. `POST /api/wall/me/hall` `{"opt_in":true|false}` → `200 {"hall":bool}`. `true` needs a found round (`409 not_eligible` otherwise). `false` always works and deletes this period's time.
11. `POST /api/wall/rounds` `{"mode":"timed"|"untimed"}` →
```json
{"available":true,"round_id":"…","section_id":0,"from_slot":0,"to_slot":17,"mode":"timed","window_ms":5000,
 "snapshot":{"layout_version":1,"capacity":48,"next_slot":3,"slots":[{"slot":0,"mark_id":1,"name":"Asha"}]},
 "issued_at":"…","expires_at":"…","server_now":"…"}
```
or `{"available":false,"reason":"not_visible"|"no_target"|"daily_limit"|"round_in_progress"}`.
12. `POST /api/wall/rounds/<id>/ready` `{}` → `{"ready_at","deadline_at","server_now"}` (`deadline_at` null for untimed). Idempotent: the same values, the timer never resets. `409 round_expired`, `409 round_stale` (the wall changed since issue; nothing consumed, not counted), `404` unknown or foreign (identical body).
13. `POST /api/wall/rounds/<id>/tap` — timed `{"slot":int}`, untimed `{"mark_id":int}`; the wrong key, both or neither is `400 bad_request` (round not consumed). Before `ready`: `409 not_ready` (never a win). The first valid tap consumes the round → `{"result":"found"|"missed"|"expired","hall_offer":bool,"elapsed_ms":int|null,"ranked":bool,"server_now":"…"}`. Second tap: `409 round_used`.
14. `POST /api/wall/rounds/<id>/cancel` `{}` → `{"cancelled":true}` (own unused round), `{"cancelled":false}` (already finished), `404` unknown or foreign.

## Slots, sections, layout

- A mark gets `(section_id, slot)` only at first approval, inside one `BEGIN IMMEDIATE` transaction. **A slot is never reused or moved.** A hidden mark keeps its slot (restore puts it back); a removed mark's slot is an empty tombstone forever; a pending mark has no position.
- Sections are 0-based and contiguous, created lazily when the open one is full, never deleted or renumbered. Capacity is stored per section at creation (default 48, hard maximum 300 enforced by a `CHECK`); changing the default affects new sections only. **48 is a default to confirm on a real phone before release.**
- Where a slot is drawn is a fixed table in code keyed by `(layout_version, slot)` (`layout.py`); adding a layout version never moves an existing name.

## Return-visit challenge

The owner's own view is rearranged: the round names a `to_slot`; the owner's name moves there (a swap with a visible name, or a move into a removed tombstone or a not-yet-assigned slot). Everyone else always sees the canonical position. Nothing overlaps or disappears and no fake name is ever created, so a lone first visitor can play.
- **Frozen snapshot:** the round returns the section's visible marks as they were at issue (at most 300). The client draws only from `snapshot`, never the live list. `ready` re-checks that nothing was hidden, restored, removed or re-laid-out since (`mod_seq`); if it was, `409 round_stale`. Plain approvals do not cause that.
- Clock: the server's only. Defaults: window 5000 ms plus 1500 ms allowance, round expires 120 s after issue if not started, 5 rounds per credential per day, one open round at a time. An untimed round has no deadline in its response but is closed 10 minutes after `ready` as a safety limit.
- Recent destinations (last 3) are avoided, but with finite slots repeats must eventually happen: the wall does not promise "never repeats".
- **Honest limits.** The client must know `to_slot` to draw the view, so a script can read it, delay `ready`, or tap instantly (a test pins that a script can reach 251 ms). The server timer only bounds how slow a human may be; latency and slow phones make a short window unfair; the time includes network and render time. A lone name is trivially easy to find, so early times are not comparable with times on a busy wall. This is a game: nothing that matters may depend on it, and no prize or privilege may. The puzzle itself is frontend UX; the backend keeps no puzzle state and issues no unlock ticket, so validation, rate limits, moderation and removal are the real controls.
- After `ready` the client holds a frozen view that may include a name hidden or removed since. That exposure is bounded to the round's lifetime, appears only to the round's owner, and contains only names that were public at issue. A removed name leaves every public read at once.
- The untimed path (for keyboard and screen-reader users) earns `found_at`, `hall_unlocked` and the Hall opt-in like the timed path but has no comparable time, so it is **never speed ranked**. Speed ranking therefore excludes the accessible path by design (reversible).

## Hall of Fame

Public, opt-in, default off, offered after a `found` result. The opt-in is a standing choice across periods until withdrawn. Times reset each period: the calendar month in `Asia/Calcutta` (a setting; `hall_kind` may be `week`); the period comes from the server clock at the tap and at each read, and `hall_period` carries absolute UTC bounds. Ended periods are deleted lazily on the first write in a new period (no scheduled job); names, slots, `found_at`, `hall_unlocked` and the opt-in persist. A win under the 250 ms floor is `found`, unranked and still unlocks. `hall_unlocked` is set in the same transaction as the first found tap by any visitor and never clears (not by opt-outs, removals, hides, an empty Hall or a period reset). The table is a display rule, not an access control: the Hall routes are public by contract and every entry is opt-in. Copies already held by a browser or CDN cannot be recalled before the cache lifetime ends.
A found tap records the time for every visitor (so a visitor can opt in right after winning and have the time count); times are shown only for opted-in, visible marks; `opt_in:false` deletes the current period's time and removing a mark deletes all of its times.

## Names

Server-side only: NFKC, control, zero-width and bidirectional characters removed, 2 to 24 characters, letters and combining marks of any script, digits, space, hyphen, apostrophe, full stop; at least two letters or digits and one letter; nothing URL-shaped or email-shaped; no run of six identical characters; each word in one alphabet (Latin plus Cyrillic in one word is refused); IPA and modifier letters refused; a small blocklist (`data/wall_blocklist.txt`: whole words plus a few fragments, matched after case, accent and look-alike-digit folding and tolerant of repeated letters). **The blocklist and the look-alike check reduce abuse; they are not complete.** The same name may be used by different people; a name never grants ownership, a Hall entry or removal. The stored value is the normalized one.

## Moderation and operation (CLI, no web route, no admin login)

`python -m eventmate.wall_admin [--db FILE] <command>`: `list-pending`, `list --status …`, `approve|reject|hide|restore|remove <id>`, `export-public --out FILE`, `backup --out FILE`, `restore-backup FILE [--force]`, `stats`, `sweep`, `accepting on|off`, `new-layout N`.
- Default **pre-moderation**: a new mark is `pending` and invisible until approved. A delay is not moderation; someone must review. `WallConfig(moderation="post")` makes marks visible at once (then `hide` must be fast).
- Caps: 200 submissions per rolling day, 10,000 in total, 500 pending, 100,000 unclaimed sessions; at any cap `accepting` is false and a submit is `503 wall_closed`. **Accepted risk:** an attacker with many addresses can fill the daily cap with junk; pre-moderation means none of it is ever shown and the cap bounds the work.
- `backup` uses SQLite's online backup API and integrity-checks the result (never copy a live file). `restore-backup` checks integrity, refuses to replace a live file without `--force`, and removes any stale `-wal`/`-shm` first. **A restore has been tested on a temporary directory only; test one on the real host before relying on it.** After a restore of an older backup, removals made since the backup are undone: re-apply them (the owner keeps their own removal record).
- `export-public` writes only visible marks as `{section_id, slot, name}`. Anything published cannot be recalled.
- Schema version in `meta` and an upgrader list (empty: 1 is the first release). A database newer than the build is refused with a clear message.
- Housekeeping (`sweep`, also run at most hourly on a session bootstrap): idempotency keys after 24 h, sessions that never claimed a mark after 30 days, rounds after 30 days, ended periods. It never touches a mark.
- Limits are in memory and forgotten on restart (an address is personal data even when hashed, so none is stored): bootstrap 30 per address per hour and 2000 overall, 5 new names per address per hour, 10 failed recovery codes per address per hour and 100 overall, 60 wall writes per address per minute.
- When deployed behind a proxy the address used for these limits is `request.remote_addr`; trusting a forwarded header needs the Phase 5 trusted-proxy setting, which is not built yet. Until then, behind a proxy every visitor shares one address and the per-address limits collapse into one shared budget: set the numbers deliberately.

## Not verified

Behaviour on a real phone, with the real frontend, behind a real proxy, on a real persistent disk; the backup schedule; the notice wording; the capacity of 48; the fairness of the 5 s window.
