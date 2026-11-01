# Frontend integration report (branch `integration/frontend`, local only)

Patch: `FINAL_back_alley_arcade.patch` (base 355ee57; 9 files: `arcade.js` +2,765 lines, `arcade3d.js`, `arcade.css`, `arcade.html`, `app.js`, `index.html`, `app.py` (13 asset names), 2 docs).
Not pushed, not merged, not deployed. The GLB models are **not in the patch**; the full download with the restored models was still being prepared.

## How it was integrated

1. The patch was applied unmodified to its own base 355ee57 (branch `frontend/patch-on-355ee57`; it applies cleanly) and then **merged** (not applied) onto the newest integration, `integration/c-plus-b-plus-ws` (threads B and C, workspaces, production hardening) plus thread B's last commit 0b911ad.
2. Conflicts: 5 files, 23 hunks. 21 were the product rename (`todoh` against `to;doh-the event planner`): the current brand was kept. 2 were the credits/footer in `arcade.html` and `ARCADE_UI.md`: the patch's text was kept. `app.py` merged without conflict.
3. Left as the patch has them (brand): `arcade.js` still has 6 `todoh` and 5 `EventMate` strings, `arcade.html` 1 `todoh`, plus the cabinet wordmark TODOH. These are the frontend's to change.

## Changes made to the frontend or backend because a real run showed a mismatch

| # | Found by | Mismatch | Fix |
|---|---|---|---|
| 1 | real browser, wall round | the backend sends `server_now`, `deadline_at`, `ready_at` as ISO 8601 strings (the same as `hall_period.starts_at`); `arcade.js` read them as epoch seconds, so the countdown showed `NaNs`, the clock skew was `NaN`, and the page's own timeout (`left <= 0`) never fired. The server's deadline still decided the round, so nothing was ever mis-scored, but the player saw a broken clock and a round that never ended on the client | **frontend** (`arcade.js`): parse with `Date.parse` (a number is still read as epoch seconds). Send this upstream |
| 2 | real browser, guest import | the page removes a saved list with `DELETE /api/plans/<id>/guests` and **no body**; the route demanded a JSON object and answered 400 "JSON object body required". The backend's own tests always sent `{}` | **backend** (`web/app.py`): a bodyless DELETE is valid (also the voice-note DELETE); a body, if present, must still be an empty object (422 otherwise). Test in `tests/test_guestlist.py` |
| 3 | merge | the production-hardening ADR was numbered 063 twice | renumbered 066 |
| 4 | the full suite, twice | `net::ERR_NETWORK_CHANGED` failed two different browser tests (one on `integration/c-plus-b`, one here) | environmental, see below; the test helper now treats that message (and the page's "Cannot reach ... server", the same event seen from the app) as noise |

### The `ERR_NETWORK_CHANGED` investigation

It did not recur in 8 solo runs of the original failing test, 4 runs of the whole browser file, or the wall tests, but it did fail a second, different browser test in a long run (11:26:48 IST). The machine's journal shows a Docker container being started about once a minute (a new `veth` device appears in NetworkManager at 11:24:31, 11:25:39, 11:26:46, 11:27:53, 11:29:00, ...) and a Wi-Fi DHCP renewal at 11:31:54. Chromium reports `ERR_NETWORK_CHANGED` for in-flight requests whenever the interface list changes, even to loopback. That matches the failures' timing; it is not an application error. Nothing was changed in the app for it.

## What the new real-browser tests prove (`tests/test_wall_frontend_e2e.py`, real Chromium, real server, nothing faked)

- Wall, in **local and shared mode**: page bootstraps (`GET /api/wall/session`, `/meta`), the cookie is `HttpOnly`, `SameSite=Lax`, unreadable by page script; wall CSRF header equals the value the session route returned; tapping the wall (found by projecting the wall mesh through the page's own camera) opens the panel; dragging solves the puzzle; SIGN sends exactly `{"name": ...}` with an `Idempotency-Key` and the CSRF header and gets 201; the owner approves; the visitor returns; a timed round is drawn from the frozen snapshot; READY; the countdown is a real number (after fix 1); tapping the true cell sends `{"slot": n}` and gets `found` with `elapsed_ms`; the Hall offer appears; JOIN THE HALL puts the name in the real Hall.
- A lost wall cookie: the write gets 401, the page re-bootstraps, shows "session refreshed - tap again", **does not resend**, nothing is written; one more tap succeeds (both modes).
- `/arcade`: chat intake, the coin, the plan dossier, the guest-list attach through preview, commit (page shows "2 guest entries saved"), removal (after fix 2), and a hostile CSV (a formula behind a zero-width space) leaves IMPORT disabled. Every API call the page made that was refused (>= 400) is listed; none except the empty wall's `GET /api/wall/sections/0` 404 (see below).
- Shared mode: two browsers get two different `todoh_ws` cookies (`HttpOnly`, `SameSite=Strict`), different per-workspace CSRF values, a separate `todoh_wall` cookie, and neither sees the other's plan.

## Failures and known gaps (honest list)

Failing in the full suite of this branch, all expected until the models arrive:
- `tests/test_arcade.py::test_arcade_page_and_assets_are_served_with_credit`: it expects the credit "Snji", but the patch's footer credits a different cabinet ("PacMan Arcade Machine" by Matthew). The repo's `cabinet.glb` is still Snji's.
- `tests/test_arcade.py::test_every_whitelisted_static_file_exists...`: the patch whitelists 13 GLBs (`alley_van.glb`, `alley_dumpster.glb`, `alley_candy.glb`, `vend_*.glb`, `wall_*.glb`, `holo_table.glb`, `secret_dossier.glb`, `road.glb`, `car.glb`, `barrier.glb`) that are not present. Each is a 404 in the browser now (console "model load" errors; the room renders without them).
Do not edit those two tests to pass; they should pass with the complete download (and the credit test needs updating if the cabinet really changed).

Gaps found, not fixed (frontend work or decisions):
- **Lost workspace cookie in shared mode:** the page's next write gets the generic `404 not found` and prints "not found" in the chat. The contract wants one `GET /api/session` and a visible retry choice. Recorded as an expected failure in `test_shared_mode_a_lost_workspace_cookie...` (it does not resend, which is right).
- **The wall board shows section 0 only.** With more than one section (capacity 48 by default) names beyond the first section are not drawn on the board (rounds still use the owner's own section's snapshot).
- **The board lays names out by its own rule** (4 columns up to 12 slots, 6 above), not by `eventmate/wall/layout.py` (layout v1 is 8 columns). Harmless while the board is the only consumer; the table in code is not used.
- An empty wall has no section 0, so every poll of `GET /api/wall/sections/0` is a 404 and the browser logs a console error each time. Either the page should skip it when `meta.section_count` is 0 or the backend could return an empty section; the contract currently says 404.
- `crypto.randomUUID()` needs a secure context (HTTPS or localhost): on plain HTTP to another host name the SIGN button would throw.
- **Licences:** `vend_retro.glb` is CC BY-NC, `vend_cat.glb` CC BY-NC-ND (shipped unmodified, 55 MB, opt-in), `wall_mural.glb` CC BY-NC-SA (adapted: share-alike applies). "NC" forbids commercial use. That conflicts with any deployment that is not strictly non-commercial; the owner must decide before any public hosting.
- Not tested: the voice recorder and live mode (no microphone in the test browser), the real 3D look and frame rate (software GL), phones, other browsers, and the models themselves.

## Thread A

Thread A has **no commits** since 95aa06c; its work is uncommitted in `event-agent/` (durable approvals, doc-count test, websearch error text, a file `tests/test_approvals_durable.py`). What was run: that uncommitted diff applied (3-way) on a scratch copy of this branch (`scratch/frontend-plus-A-uncommitted`, worktree `event-agent-fe-a`, **not a branch anyone should merge**). Full suite there: 3325 passed, 7 failed, 1 skipped. The 7: the 2 model tests above; 3 in A's own `tests/test_doc_counts.py` (the count file and the count in the handoff have not been generated for this combination); and 2 real conflicts between A's SQLite approval queue and thread B's work:
- `tests/test_llm_boundary.py::test_the_stored_arguments_are_validated_again_when_applied` reads `ApprovalQueue._items`, which A's rewrite no longer has.
- `tests/test_workspaces.py::test_storage_growth_per_new_workspace_is_small_and_measured`: every workspace now gets an extra `assistant_approvals.db` (the test expects only `workspaces.db` in the registry listing); the documented growth per workspace changes.
A and B must reconcile these two when A commits.
