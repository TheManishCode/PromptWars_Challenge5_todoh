# Putting it live: the exact steps and the checklist (WP6)

**Nothing here was run, deployed or paid for, and no host is chosen.** This page turns `docs/HOSTING.md` and `docs/RUNBOOK.md` into an ordered list for the owner, after checking what free hosts say today (read 2026-11-09, table below). Choosing a host, creating an account, a domain, DNS and a certificate are the owner's steps.

## What the app needs from a host (measured, not guessed)

| Need | Why | Evidence |
|---|---|---|
| An **always-on process** (not one that sleeps) | the background loop sends approved messages and reads replies (`EVENTMATE_POLL_SECONDS`, default 60) and a build runs on a queue | `docs/HOSTING.md`; the soak ran 30 minutes continuously |
| A **persistent disk** that survives restarts and redeploys | plans, replies, quotes, payments and the workspace database are files and SQLite under `EVENTMATE_DATA_DIR`; one process per data directory | `docs/HOSTING.md`, `docs/RUNBOOK.md` |
| About **250 MB of RAM for the app** plus the proxy | server RSS was 214 to 234 MB over the 1800 s soak (20 visitors, 10 on `/plan`) | reviewer's soak, `docs/REVIEW_LOG.md` |
| An image of about **330 MB** (1.4 GB on disk) built for the host's CPU | the Dockerfile compiles Fast Downward and VAL from source (pinned commits); a build can use over 1 GB of RAM per compile job (`BUILD_JOBS`) | `Dockerfile`, `tools/check_image.sh`. **Only built and run on x86-64 here; an Arm host is untested** |
| A TLS-terminating proxy that forwards `Host $http_host` | the application's Origin check; `deploy/nginx.conf.example` | R-011, `docs/HOSTING.md` |
| Outbound HTTPS and, for mail, IMAP (993) and SMTP (465 or 587) | the mailbox, Google token endpoint, Places, weather, search | `docs/EMAIL_SETUP.md`. **Some hosts block outbound SMTP: check before choosing** |
| Linux kernel with Landlock (5.13+) or bubblewrap | the file reader's sandbox; shared mode refuses to start without it | `docs/HOSTING.md`, `tools/check_image.sh` |

## What free hosts say (read 2026-11-09; each can change)

| Host | What the page says | Fits? | Source (page date) |
|---|---|---|---|
| **Render, Free web service** | spins down after 15 minutes without inbound traffic (about a minute to wake); **a free web service cannot have a persistent disk: local files, including SQLite, are lost on every restart, redeploy or spin-down**; 750 free instance hours a month; free Postgres expires after 30 days | **No.** It sleeps (the poll loop stops), and it loses the data | https://render.com/docs/free |
| **Fly.io** | "Is there a free tier? **No.** New organizations don't have a free tier"; a $1 trial for 7 days; the cheapest always-on Machine is $2.19 per 30 days plus data | **Not free**, a small recurring cost; the owner's budget rule is zero | https://fly.io/docs/about/pricing/ |
| **Google Cloud, Compute Engine free tier** | 1 non-preemptible **e2-micro** VM a month in us-west1, us-central1 or us-east1, 30 GB-months standard persistent disk, **1 GB outbound a month**; usage is by hours, so one always-on instance fits; a billing account is needed (the free trial is $300 over 90 days) | **Possible**: always-on, persistent disk. The e2-micro has 1 GB of RAM (not checked here against build and run together: build the image elsewhere and pull it); 1 GB egress is small; US regions only; needs a billing account (a card) | https://cloud.google.com/free/docs/free-cloud-features (2026-10-27) |
| **Oracle Cloud, Always Free** | "Always Free services are available for an unlimited period"; the list includes AMD and Arm (Ampere A1) compute and Block Volume; a card is needed to sign up; "accounts left idle for 30 days or more may be deemed abandoned and become eligible for suspension or termination" | **Possible**; shapes, sizes and regions on the page were not rendered (so what fits is unknown); Arm needs an Arm build of the image (untested); the idle-account clause matters for a quiet site | https://www.oracle.com/cloud/free/ |
| **Railway** | a free trial page only; trial terms not read in detail | **Not assessed**; a trial is not a recurring free allowance, which the owner's rule excludes | https://docs.railway.com/reference/pricing/free-trial |
| **Koyeb** | the pricing FAQ mentions "1 free Instance" included with plans; its size, sleep behaviour and storage were **not read** | **Not assessed** | https://www.koyeb.com/docs/faqs/pricing |

**Reading of the above, not a decision:** among the free options only a VM (Google's e2-micro or Oracle's Always Free) is always-on with a disk, and both need a card on file. Render's free tier cannot hold this app's data at all. Whether either VM really costs nothing for this app, and in the owner's country, is the owner's to confirm on the provider's own billing page before creating anything.

## The steps (owner), in order

1. **Decide the host** (open decision, `docs/BLOCKED.md`). Check the provider's current free terms in front of you; nothing above is a promise.
2. **Domain and certificate.** A name you control pointing at the host (a free subdomain service is possible but is a third party; its terms were not read), and a certificate for it (the proxy example expects `ssl_certificate` paths; a free ACME certificate needs the name to resolve to the host first). Cost of a domain is separate.
3. **The host:** a Linux VM with Docker, the data volume on the persistent disk, ports 80 and 443 open (and nothing else to the internet; Prometheus and Grafana bind to 127.0.0.1 in `deploy/docker-compose.yml`).
4. **Build the image where there is RAM** (a laptop, not a 1 GB VM): `docker build -t todoh .`, then move it to the host (a registry login is an account: the owner's call) or build on the host with `BUILD_JOBS=1`.
5. **Secrets on the host only.** `cp deploy/env.example deploy/.env`, fill: `EVENTMATE_SECRET` (40+ random characters), `EVENTMATE_PUBLIC_URL=https://<name>`, `EVENTMATE_ALLOWED_HOSTS=<name>,todoh`, `EVENTMATE_TRUSTED_PROXY`, `EVENTMATE_OWNER_TOKEN`; then, only when wanted, the mailbox (`docs/EMAIL_SETUP.md`), `GOOGLE_PLACES_API_KEY`, `BRAVE_SEARCH_API_KEY`, `OPENWEATHER_API_KEY`, `EVENTMATE_ENABLE_LLM=1` with a model key. Never commit `deploy/.env`; `echo -n "$OWNER_TOKEN" > deploy/prometheus/owner_token`. **Shared mode refuses to start if a `.env` file exists in the application's directory** (secrets come from the environment only: the compose file passes `deploy/.env` as the container's environment).
6. **The proxy:** `deploy/nginx.conf.example` with your name and certificate paths; keep `Host $http_host`.
7. **Start:** `docker compose -f deploy/docker-compose.yml up -d --build`. Check `GET /healthz` and `GET /readyz` through the proxy, then `/`, `/plan`.
8. **Before inviting anyone:** `tools/check_image.sh`; `python tools/loadtest/soak.py --seconds 60 --visitors 8` against a copy; the real mailbox test with the owner's own address only (`docs/BLOCKED.md`); a backup and a **restore test** on a spare directory (`docs/RUNBOOK.md`); copy `tombstones.jsonl` off the machine on a schedule.
9. **Sending stays off for visitors** (the application refuses it in shared mode); the operator's own mailbox is the only sender, and nothing is sent without an approval.

## Checklist

| Item | State |
|---|---|
| Host chosen | **needs the owner** |
| Domain, DNS, certificate | **needs the owner** |
| Persistent data volume confirmed to survive a restart (the data directory is on it) | **not run** |
| Outbound SMTP and IMAP confirmed reachable from the host | **not run** |
| Image built for the host's CPU and `tools/check_image.sh` passes there | **not run** (x86-64 only, here) |
| `/healthz`, `/readyz`, `/plan` reachable through the proxy over TLS | **not run** (the same path was exercised with a self-signed certificate: `tests/test_hosted_journey.py`) |
| Secrets in the real environment, no `.env` in the container | **needs the owner** |
| Backup made and restored on a spare directory | **not run on a real host** |
| Monitoring and an alert wired to a person | **needs the owner** (`docs/RUNBOOK.md`: nothing is wired to a person) |
| Counsel: vendor cards, the shared quote pool, vendor consent wording | **needs a human** |
| Licence file for the repository (Fast Downward is GPL-3.0 and ships in the image) | **needs the owner** (`docs/DECISIONS.md` WP8) |
