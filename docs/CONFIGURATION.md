# Configuration

## Environment variables

| Variable | Purpose |
|---|---|
| `EVENTMATE_SIGNING_KEY` | HMAC key (>=16 bytes) that makes stored plans tamper-evident even against someone who can write files |
| `EVENTMATE_LOG_RETENTION_DAYS` | How old a run log must be before `purge-logs` removes it (default 90) |
| `EVENTMATE_VOICE_ENGINE`, `EVENTMATE_VOICE_MODEL`, `EVENTMATE_VOICE_DEVICE`, `EVENTMATE_VOICE_COMPUTE` | Local voice-note transcriber (off unless `faster-whisper` is installed and a model folder is named). Set in the shell, not `.env` |
| `EVENTMATE_VOICE_KEEP_ORIGINAL`, `EVENTMATE_VOICE_NOTE_DAYS` | Opt-in keeping of original voice notes (default off) and how long they are kept (default 30 days). Set in the shell, not `.env` |
| `EVENTMATE_WEB_TOKEN` | Bearer token required for the API (mandatory for non-loopback binds) |
| `EVENTMATE_OWNER_EMAILS` | Comma-separated list of the only addresses the owner digest may be sent to (unset: any address the caller proposes). **Set it on any installation that someone other than you can reach**: the confirmation code is returned to whoever proposes the address, so it does not prove who owns it (F-009) |
| `EVENTMATE_FD`, `EVENTMATE_VAL` | Paths to Fast Downward / VAL if not in the default locations |
| `EVENTMATE_MAIL_AUTH` | `password` (default) or `oauth2`: how the mailbox signs in. Anything else means `password` |
| `EVENTMATE_GMAIL_CLIENT_ID`, `EVENTMATE_GMAIL_CLIENT_SECRET`, `EVENTMATE_GMAIL_REFRESH_TOKEN` | For `oauth2` only (Google SASL XOAUTH2 on `smtp.gmail.com` / `imap.gmail.com`). Secrets: environment only, never in code, logs or chat. Steps and the token-expiry rules: `docs/EMAIL_SETUP.md`, `docs/INTEGRATIONS.md` |
| `EVENTMATE_IMAP_SPAM_FOLDER` | Optional name of the mailbox's spam folder (Gmail: `[Gmail]/Spam`). Replies from addresses we wrote to that the provider filed there are shown to you read-only and never filed or acted on. Empty (default) = not looked at |
| `EVENTMATE_BROWSER=1` | (`pip install '.[browser]'`) A read-only browser for vendor pages that show their text only after scripts run (needs Playwright and Chromium; `EVENTMATE_BROWSER_NO_SANDBOX=1` inside a container). It clicks and types nothing, Chromium has no network of its own (every request is answered by the same checked, pinned fetch as a static page), and the text it returns goes to the price and contact readers, never to a model |
| `NVIDIA_API_KEY`, `OPENROUTER_API_KEY` (`EVENTMATE_ENABLE_LLM=0` turns models off) | Models: NVIDIA for heavy work, OpenRouter free models for light work and as backup; no SDK needed |
| `EVENTMATE_INTAKE_MODEL` (`auto` by default; `always`) | Intake: `auto` asks a model only when a fact a plan needs is still empty and nobody is being asked about it. Complete briefs are then read by the rules alone, in milliseconds instead of the model's 5-25 s per message. `always` asks a model on every message |
| `EVENTMATE_INTAKE_TIER` (unset; `light`) | Intake: `light` reads briefs with the light model tier. Off by default: an unreachable light provider costs its whole timeout (30 s) on every message that reaches a model |

Secrets are only read from the environment and never written to disk or logs.

## Keys and optional integrations

Secrets live in `.env` at the repository root (git-ignored; copy `.env.example`, then `chmod 600 .env`). Format: `NAME=value`. Only the names in
`eventmate/env.py` are read, and a value already set in your shell wins. Nothing prints or logs a value.

| Feature | Needs | Command / endpoint |
|---|---|---|
| Weather (OpenWeather) | `OPENWEATHER_API_KEY` | `python -m eventmate weather CITY YYYY-MM-DD`, Overview card |
| Flight fares and schedules (fli, Google Flights) | `pip install '.[flights]'` (no key; a pinned fli commit, see below) | `flights`, `multicity`, `/api/travel/*` |
| Free-text search (Explore) | `NVIDIA_API_KEY` and/or `OPENROUTER_API_KEY` | `/api/explore`, Explore view (forms work without a key) |
| Ratings and reviews (Google Places) | `GOOGLE_PLACES_API_KEY` | `/api/places`, Explore view |
| Deals agent: searches the web, reads pages, ranks advertised prices | none (DuckDuckGo); optional `BRAVE_SEARCH_API_KEY` | `deals`, `/api/deals` |
| Venue discovery (OpenStreetMap / Overpass) | none; optional `EVENTMATE_OVERPASS_URL` | `discover`, `/api/discover/venues` |

**Install fli from the extra, not from PyPI.** `pip install '.[flights]'` installs fli 0.10.0 at a pinned upstream commit (`881aee5`, 2026-09-20), because the `flights` 0.9.0 on PyPI got an error answer (`[13]`, no data) from Google for every search on 3 Nov 2026, so fares came back empty. The pinned commit was checked live on 4 Nov 2026 (DEL-GOI, 31 days priced). A search that comes back empty on 0.9.0 says so and says what to install. The hash-pinned lock files still list `flights==0.9.0`: that is for the offline tests, which use fakes, not for real searches. Google can change what it answers at any time; if searches come back empty for routes that obviously exist, check fli first. The cheapest-day search was checked live against 0.10.0 (DEL-GOI, 61 days: 32 different prices, and the cheapest, dearest and a middle day each matched a one-way search for that day).

Flights never book or pay. Multi-city is built from separate one-way searches because Google does not serve multi-city results to fli. Discovery
results are candidates, not vendor-database facts. Forecasts exist for about five days ahead only.

Deals are leads: advertised prices from public pages, flagged when unusually low or very wide, never quotes. The agent obeys robots.txt and cannot reach private addresses.

Check everything at once: `python -m eventmate.smoke` (free sources only) or `python -m eventmate.smoke --llm` (also pings the model providers; a few credits). Tests never call a keyed service.

Vendor messaging settings are in [EMAIL_SETUP.md](EMAIL_SETUP.md). Community profiles and idea templates are data files under `eventmate/data/`; set `EVENTMATE_COMMUNITY_PROFILES` to use another profiles file.

## Finding vendors
| Source | Covers | Needs |
|---|---|---|
| OpenStreetMap (Overpass) | venues only | nothing; optional `EVENTMATE_OVERPASS_URL` |
| Google Places | venues, caterers, photographers | `GOOGLE_PLACES_API_KEY`. A shortlist search also requests `places.websiteUri` and `places.id`, which Google bills at a higher tier than the base fields; this has not been run against a live key |

Contact addresses are read only from a business's own website (the listing's, or one you type) and at most one same-domain contact page, honouring robots.txt. Found addresses are never used until you confirm them. Business addresses and the do-not-contact list are stored in the plan directory (`sourcing` data: `externals/`, `shortlist/`, `suppression/`, `unmatched/`) with no retention or deletion rules yet.

## Quote files

A vendor's quote can be added from the vendor's event in the dashboard ("Add a quote file"). The file is converted in a separate, resource-limited process (private temporary directory, no secrets in its environment, time, write and memory limits) and is not kept; the lines read from it are shown as unconfirmed until you confirm one, and nothing is a price until then. This runs on your own machine only, not on shared hosting.

- **Text, CSV and HTML** quotes work with nothing installed.
- **PDF, Word, Excel, PowerPoint and images** need [Docling](https://github.com/docling-project/docling): `pip install '.[documents]'` (or `pip install docling`), then `python -m eventmate.documents.warm` once to download its models. Reading a quote afterwards runs offline, and the reader is shut in as far as the machine allows (`documents/isolation.py`; strongest first): **contained** (bubblewrap: no network, and only the interpreter, this package and its own directory exist for it), **confined** (Landlock plus a socket filter: no network, and every other file is refused), **network** (`unshare -rn`: no network, files still readable by the server's user) and **no-sockets** (a socket filter only). Shared mode needs contained or confined; locally `EVENTMATE_REQUIRE_NETLESS=1` asks for any level that cuts the network. Hugging Face is also set offline (`HF_HUB_OFFLINE=1`; `HF_HOME` says where the models are). Docling is heavy (torch); to keep it out of this project's environment, install it in its own and point `EVENTMATE_DOCLING_PYTHON` at that interpreter to run its tests. A one-page PDF used about 1.5 GB of memory; the limit is 3 GB, and a file that needs more is refused with a message.
- **Attachments on a vendor's reply** are counted and never opened unless you set `EVENTMATE_READ_QUOTE_ATTACHMENTS=1`. Then up to two attachments of a reply that matches a thread we sent, from the address we wrote to, are read the same way. A message longer than 256 KB is cut off by the mail fetch and offers no files: upload those by hand.

## Keeping secrets out of files

The SMTP password, the WhatsApp gateway key and the API keys can live in `.env` (mode 600, git-ignored), but a file on disk is the weakest option. The loader reads only the 30 allow-listed names and **a value already set in the real environment wins over the file**, so any of these work with no code change: put the secret in the environment when you start the app, and leave it out of `.env`.

| Where the secret lives | How to start the app | Notes |
|---|---|---|
| A password manager (1Password CLI) | `op run --env-file=.env.op -- python -m eventmate.web`, where `.env.op` holds a reference, not the secret: `EVENTMATE_SMTP_PASSWORD="op://Private/todoh SMTP/password"` | The secret is fetched when the process starts and exists only in its environment. `.env.op` is safe to keep, but still do not commit it |
| The desktop keychain on Linux (libsecret) | `EVENTMATE_SMTP_PASSWORD="$(secret-tool lookup service todoh user smtp)" python -m eventmate.web` | Store it once with `secret-tool store --label='todoh SMTP' service todoh user smtp` (it prompts; the value is not on the command line) |
| The macOS keychain | `EVENTMATE_SMTP_PASSWORD="$(security find-generic-password -a "$USER" -s todoh-smtp -w)" python -m eventmate.web` | Store it once with `security add-generic-password -a "$USER" -s todoh-smtp -w` (it prompts) |
| A service manager (systemd) | `LoadCredential=` or an `EnvironmentFile=` with mode 600 owned by the service user | The file is still on disk, but outside the project and not world-readable |

I have **not run** the 1Password, libsecret or macOS commands for this page; they follow those tools' documented usage, so check each against its own help before relying on it. What this application guarantees, and tests cover, is its own side: it never prints, logs or returns a secret's value (only names and warnings), ignores an `.env` that is a symlink, larger than 64,000 bytes or not a regular file, warns when `.env` is readable by other users, and lets the real environment override the file.

Do not put a secret on a command line (`--password ...`) or in a shell history line: other users can see process arguments. Rotate any secret that has ever been pasted into a chat, a ticket or a log.


## Building the planning tools (a clean clone needs this before `make test`)

Plans refuse to run without Fast Downward and VAL (by design: nothing unchecked is shown), so a fresh checkout fails most of the suite until they are built. The pinned commits are the ones the `Dockerfile` uses. Needs `cmake g++ make flex bison git`.

```
mkdir -p solvers && cd solvers
git clone --depth 1 --branch release-24.06.1 https://github.com/aibasel/downward.git fast-downward-24.06.1
(cd fast-downward-24.06.1 && test "$(git rev-parse HEAD)" = 1eef26b2cbf599a1894606aa898d9d49e1034cb9 && python3 build.py -j4)
git init -q VAL && cd VAL && git remote add origin https://github.com/KCL-Planning/VAL.git \
  && git fetch -q --depth 1 origin 3c7a1f330bdab0ba28a4762bb45c3f06c27fb6d4 && git checkout -q FETCH_HEAD \
  && mkdir -p build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 && make -j4
```

The `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` flag is needed on CMake 4 (VAL declares an older minimum); older CMake ignores it. The default lookup finds `solvers/fast-downward-24.06.1/fast-downward.py` and `solvers/VAL/build/bin/Validate`; otherwise set `EVENTMATE_FD` / `EVENTMATE_VAL`. The `solvers/` directory is git-ignored.
