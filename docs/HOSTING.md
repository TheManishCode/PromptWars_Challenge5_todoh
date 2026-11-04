# Hosting: what the application expects

This page says what the application needs from whoever runs it, and what it does not do. It does not choose a provider, and it does not assume any cost: free and paid hosts differ, and the owner has to check the price, the limits and the terms of the one they pick.

**The application is built for one person on one machine (`EVENTMATE_MODE=local`, the default).** Shared mode (other people can reach it) starts only when every check below passes, and its behaviour behind a real reverse proxy has not been verified. Per-visitor workspaces exist (`hosting.WORKSPACES_IMPLEMENTED` is True). Do not put the server where other people can reach it without shared mode: every plan is visible to anyone who can reach the port.

## What the server is
- One Python process, `python -m eventmate.web`. State lives in files under the plan and log directories (JSONL, SQLite, one file per plan version) guarded by file locks, so run **one** process per data directory. Several processes on the same files are tolerated by the locks (they are tested), but a second host on a copy of the files is not supported.
- The built-in server has an idle timeout (30 s) and a connection cap (128), set with `EVENTMATE_IDLE_TIMEOUT_S` and `EVENTMATE_MAX_CONNECTIONS`. It does **not** stop a client that sends one byte just inside the timeout. For anything other people can reach, put a reverse proxy in front with its own header and body timeouts (and a request-rate limit), or use waitress if the build includes it (`--server waitress`).
- Planning needs Fast Downward and VAL (`docs/CONFIGURATION.md`). Without them the product refuses to plan, by design.

## What the application expects in shared mode (checked at start-up; it refuses to start otherwise)
| Setting | Why |
|---|---|
| `EVENTMATE_MODE=shared` | Turns the checks on. Any other value than `local` or `shared` is an error. |
| `--allow-remote` and `--allowed-host <name>` for each name you serve under | The host check refuses any other `Host` header. Loopback names alone do not count. |
| `EVENTMATE_WEB_TOKEN` | Required for any non-loopback bind (existing rule). |
| `EVENTMATE_SECRET`, at least 32 random characters | Derives each workspace's CSRF value. |
| `EVENTMATE_TRUSTED_PROXY` = the proxy's address or network (comma separated) | The only source of client addresses (`X-Forwarded-For`, read from the right) and of "this was HTTPS" (`X-Forwarded-Proto`). Without it forwarded headers are ignored, so every caller looks like the proxy. |
| `EVENTMATE_PUBLIC_URL` starting `https://` | Shared mode is not served over plain HTTP. |
| `EVENTMATE_OWNER_TOKEN`, at least 16 characters | The owner-only status route. |
| A Linux kernel with Landlock (5.13 or later), or bubblewrap installed | The file reader (PDF, Word, text, CSV and HTML quotes) runs with no network and can open only its own directory, the interpreter and this package. Where the host can do neither, shared mode refuses to start, and no file is read in shared mode without it: there is no weaker fallback. The shipped container (non-root, all capabilities dropped, no-new-privileges, read-only) gets this from Landlock; bubblewrap and `unshare` are not available inside it. Locally the reader still runs without either, unless `EVENTMATE_REQUIRE_NETLESS=1`. Secrets belong in the environment, never on the command line: at the weaker levels (not accepted in shared mode) a reader can read the server's command line. |
| No `.env` file | Secrets (model keys, mailbox, gateway) come from the real environment only. |
| Sending off | Real email and WhatsApp sending stays off for visitors; it needs its own explicit setting that is not built yet. |

When a request arrives over TLS (directly, or through the trusted proxy saying so) the response carries `Strict-Transport-Security`. TLS itself is terminated by the proxy; the application never handles certificates.

## What the proxy must do
Terminate TLS; set `X-Forwarded-For` and `X-Forwarded-Proto` itself (and drop any the client sent); forward only the allowed `Host`; apply header and body timeouts and a body-size limit of at least 6 MB (the audio upload is 5 MB plus a multipart wrapper, every other route is held to 64 KB by the application); limit the request rate; and not cache `/api/` responses.

## Routes for the operator
- `GET /healthz`: `{"ok": true}`, no data, no authentication, for a load balancer or an uptime check.
- `GET /api/owner/status` with header `X-EventMate-Owner: <EVENTMATE_OWNER_TOKEN>`: counts only (uptime, workspaces active in the last minute, pending jobs, slow searches in flight and their capacity, when the last background round finished and how many failed, model call counters). No names, no content. With no token set, or a wrong one, it answers exactly like an unknown route.

## Limits that are in force
Writes: 60 per minute per client address (`create_app(rate_limit_per_minute=...)`) and twice that per workspace. Model use per workspace: 200 calls and 300,000 estimated tokens an hour, 2 in flight (`EVENTMATE_WS_LLM_*`), under the owner's overall ceiling `EVENTMATE_LLM_MAX_CALLS`. Slow searches (places, deals, weather, flights): 4 at once; in shared mode one workspace may hold 2 of them (in local mode the one user may use all 4). Free tiers of the outside services (maps, search, weather) can run out; the application says so and never switches to a paid path by itself.

## Disk, backups, deletion
A persistent disk path for the plan and log directories is required; the plaintext files are the product's memory. Disk encryption of the host is not checked by the code. Backups are the operator's to make; deleting a plan removes this application's files only and cannot reach a backup until it expires, so write down how long backups are kept.

## Repeatable checks to run before a real host
- `deploy/nginx.conf.example` is a reference proxy configuration. `python -m pytest tests/test_proxy_deploy.py` runs **that file** in front of the real application in shared mode, with real TLS (a self-signed certificate), and checks: the probes; the security headers and HSTS (believed only because the proxy says https); that a client-supplied `X-Forwarded-For` and `X-Forwarded-Proto` do not get through; that another Host name is refused; the cookie flags (Secure, HttpOnly, SameSite=Strict); a write without the CSRF header; a body over the application's 64 KB limit; the owner route being invisible without its token; and that the progress stream reaches the browser as it is written. Changing the shipped proxy file to forward the client's own `X-Forwarded-For`, to stop saying "https", or to drop the Host check makes the matching test fail (each was tried).
- `tests/pentest/test_proxy_smuggling.py` sends four request-smuggling shapes through nginx.
- `tools/check_image.sh` builds the image and checks it the way the compose file runs it (non-root, all capabilities dropped, no-new-privileges, read-only): licences and Fast Downward's source are in the image; shared mode refuses to start without its settings; the file reader is shut in at a level shared mode accepts; a plan is validated by the pinned Fast Downward and VAL; and a shared-mode container starts, answers `/healthz` and `/readyz`, serves only its Host name, hides the owner route and stops cleanly. `make deploy-check` runs all of it.

## Not verified
These checks run on one machine with a self-signed certificate, one nginx version and no load. They do not replace a run on the real host with its real proxy, certificate, DNS, firewall and clock, and they do not cover another proxy (Caddy, a cloud load balancer), real traffic, backups, or the mailbox and WhatsApp accounts.

## Request framing: the proxy must reject ambiguous requests (red-team F-021)

waitress, like most WSGI servers, reads a request that carries both `Content-Length` and `Transfer-Encoding: chunked` as chunked and then treats the bytes after the
chunked body as a **second request** (it answers twice). Behind a proxy that frames the same bytes by `Content-Length`, that is request smuggling. Put a proxy in
front that refuses such requests. **Tested with nginx 1.30.6 only** (`tests/pentest/test_proxy_smuggling.py`, skipped when nginx is not installed): four smuggling shapes
(Content-Length then Transfer-Encoding, the reverse, an obfuscated value, a space before the colon) each got exactly one answer, 400 or 501. Caddy, HAProxy, a cloud load
balancer or another nginx version were not tested; check yours with that test's four requests before trusting it. Never expose waitress directly to the internet.
