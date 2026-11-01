# Runbook: running the planner for other people

For the person who operates the server. Local single-user use needs none of this. Everything here was exercised with fakes and local servers, not on a real host; read `docs/HOSTING.md` for what the application expects and `docs/WORKSPACES.md` for how visitors are separated.

## Start and stop
- Start: `EVENTMATE_MODE=shared EVENTMATE_DATA_DIR=/srv/todoh EVENTMATE_SECRET=... EVENTMATE_TRUSTED_PROXY=... EVENTMATE_PUBLIC_URL=https://... EVENTMATE_OWNER_TOKEN=... python -m eventmate.web --host 127.0.0.1 --port 8765 --allow-remote --allowed-host your.host`. It refuses to start and names every missing setting (`docs/HOSTING.md`). Run it under a supervisor (systemd or similar) that restarts it and sends `SIGTERM` to stop it.
- Stop: `SIGTERM` (or `SIGINT`). The built-in server stops accepting at once, lets running requests finish for up to 20 seconds, and prints whether any were abandoned. What a request has begun writing is finished or not begun: plan versions are written create-only and logs are append-only. Use the same signal for a restart.
- Is it up? `GET /healthz` says the process is alive. `GET /readyz` says it can take traffic: the data directory is writable, the workspace database opens, at least 50 MB of disk is free; a 503 names the class of problem (never a path). Point a load balancer's readiness check at `/readyz` and an uptime monitor at `/healthz`.
- What is it doing? `GET /api/owner/status` with `X-EventMate-Owner: <EVENTMATE_OWNER_TOKEN>`: counts only (uptime, workspaces in total, requests by status class and latency band, slow searches in flight, model calls, last background round). With no token configured it answers like an unknown route.

## Logs
- The access log is one JSON line per request on standard error (`EVENTMATE_ACCESS_LOG=stderr`, the default in shared mode; `off` silences it): request id, method, route *template*, status, milliseconds, opaque workspace id. It never holds a query string, header, cookie, body, address or name, so it is safe to keep. Every answer carries the same id in `X-Request-Id`, and an error body names it (`{"error", "code", "request_id"}`), so a person's report can be matched to a line and to the traceback the server wrote for that id.
- Tracebacks, startup refusals and background failures also go to standard error (the application's own logger `eventmate.web`). Your supervisor decides where that goes and how long it is kept; the application does not rotate it.
- Per-workspace decision and trace logs live inside each workspace and follow the retention in `docs/PRIVACY.md` (90 days by default, `python -m eventmate purge-logs`).

## Backups
- Make one: `python -m eventmate.workspaces_admin backup --to /mnt/backups` (needs `EVENTMATE_DATA_DIR`). It writes `todoh-backup-<UTC time>/` outside the data directory with SQLite's online backup of the databases, a copy of every workspace, the deletion log and a manifest of SHA-256 hashes, while the server keeps running. Check it: `... verify-backup PATH`. Do it daily; copy it off the machine; it is plaintext, so protect it like the data.
- **Ship the deletion log separately and often.** `<data dir>/tombstones.jsonl` records every plan and workspace deleted. Copy it off the machine as it grows (for example every few minutes). A backup is only as new as the day it was made; the log is what keeps deleted data deleted after a restore.
- Restore: `python -m eventmate.workspaces_admin restore PATH --into /srv/todoh-new --deletion-log /path/to/the-latest-tombstones.jsonl`. It refuses a directory that is not empty, verifies the manifest first, copies, then removes again every plan and workspace the log says was deleted after the backup. Start the server on the restored directory. Visitors keep their cookies and find their workspaces; anything saved after the backup is gone, and a person told that a plan was deleted gets that guarantee back.
- Test a restore on a spare directory now and then. A backup nobody has restored is a hope, not a backup.

## Routine
- Empty throwaway workspaces are removed automatically after 6 hours idle (`EVENTMATE_WS_EMPTY_IDLE_HOURS`). A workspace with anything saved is never removed automatically. `python -m eventmate.workspaces_admin list` shows each workspace's age and whether it holds data; `delete ID --confirm ID` removes one; `sweep --idle-days N` removes empty ones by hand.
- Capacity: at most `EVENTMATE_MAX_WORKSPACES` (default 1,000) workspaces, 50 MB each (`EVENTMATE_WS_MAX_BYTES`), 20 new plans a day each; 5 new workspaces an hour per address and 60 in all. Watch `workspaces_total` on the status route. If it climbs steadily with no matching traffic, someone is filling it (docs/REDTEAM.md, R-10): look at the access log for a burst on `GET /api/session`, tighten the proxy's rate limit for that route, and raise or lower the cap as you judge.
- Model use is capped per workspace (200 calls, 300,000 estimated tokens an hour, 2 at once) under your overall ceiling `EVENTMATE_LLM_MAX_CALLS`. Outside lookups (maps, search, weather) are capped at 60 an hour per workspace; their free tiers can still run out.

## When something is wrong
| You see | Likely cause | Do |
|---|---|---|
| `/readyz` 503 "storage is not writable" | disk full, read-only mount, permissions | free space or fix the mount; the server needs no restart |
| `/readyz` 503 "the workspace database cannot be opened" | `workspaces.db` locked or damaged | stop the server, `verify-backup` the latest backup, restore if needed |
| Visitors get `507 storage_full` | the data disk is full | free space; nothing was lost |
| Visitors get `workspace_limit` (503) | the cap is reached | see Capacity above |
| Many `429` | a burst from one address or a proxy not trusted | check `EVENTMATE_TRUSTED_PROXY` is the proxy's address, or all visitors share one budget |
| A person's workspace "disappeared" | they cleared their browser, or an empty one was swept | nothing can bring it back without their export; there is no recovery route yet (`docs/WORKSPACES.md`) |
| 500 with a `request_id` | a bug | find that id in the log; the traceback is next to it; report it with the id |
| A model or outside service failing | provider outage or limit | the application degrades to its no-model answers and says so; nothing to do but wait |

## Monitoring and alerts
`/metrics` (Prometheus format) needs the owner token as `Authorization: Bearer` or `X-EventMate-Owner`; `deploy/docker-compose.yml` starts Prometheus (scraping it with the token from `deploy/prometheus/owner_token`) and Grafana (dashboard `to;doh`). Alerts (`deploy/prometheus/alerts.yml`): TodohDown, TodohHigh5xxRate, TodohSlowRequests, TodohDiskLow, TodohBackgroundPollFailing, TodohSearchesSaturated, TodohRateLimitedSpike. Wire Prometheus to an Alertmanager or your cloud's paging; none is bundled. Thresholds are first guesses: tune them after a week of real traffic.
Before a release: `python tools/loadtest/soak.py --seconds 1800 --visitors 20` (needs the solvers); it fails on any 5xx, a slow p95 or growth in memory, files or threads.

## Not covered
Zero-downtime upgrades, more than one server process on the same data, high availability, and a bundled pager: alert rules exist (above) but nothing is wired to a person. The app is one process on one disk; plan for a short restart and for the backup as the recovery path.
