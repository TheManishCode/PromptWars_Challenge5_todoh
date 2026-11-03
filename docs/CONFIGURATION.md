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
| `NVIDIA_API_KEY`, `OPENROUTER_API_KEY` (`EVENTMATE_ENABLE_LLM=0` turns models off) | Models: NVIDIA for heavy work, OpenRouter free models for light work and as backup; no SDK needed |

Secrets are only read from the environment and never written to disk or logs.

## Keys and optional integrations

Secrets live in `.env` at the repository root (git-ignored; copy `.env.example`, then `chmod 600 .env`). Format: `NAME=value`. Only the names in
`eventmate/env.py` are read, and a value already set in your shell wins. Nothing prints or logs a value.

| Feature | Needs | Command / endpoint |
|---|---|---|
| Weather (OpenWeather) | `OPENWEATHER_API_KEY` | `python -m eventmate weather CITY YYYY-MM-DD`, Overview card |
| Flight fares and schedules (fli, Google Flights) | `pip install flights` (no key) | `flights`, `multicity`, `/api/travel/*` |
| Free-text search (Explore) | `NVIDIA_API_KEY` and/or `OPENROUTER_API_KEY` | `/api/explore`, Explore view (forms work without a key) |
| Ratings and reviews (Google Places) | `GOOGLE_PLACES_API_KEY` | `/api/places`, Explore view |
| Deals agent: searches the web, reads pages, ranks advertised prices | none (DuckDuckGo); optional `BRAVE_SEARCH_API_KEY` | `deals`, `/api/deals` |
| Venue discovery (OpenStreetMap / Overpass) | none; optional `EVENTMATE_OVERPASS_URL` | `discover`, `/api/discover/venues` |

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
- **PDF, Word, Excel, PowerPoint and images** need [Docling](https://github.com/docling-project/docling): `pip install docling`, then `python -m eventmate.documents.warm` once to download its models. Reading a quote afterwards runs offline (`HF_HUB_OFFLINE=1` inside the sandbox; `HF_HOME` says where the models are). Docling is heavy (torch); to keep it out of this project's environment, install it in its own and point `EVENTMATE_DOCLING_PYTHON` at that interpreter to run its tests. A one-page PDF used about 1.5 GB of memory; the limit is 3 GB, and a file that needs more is refused with a message.
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

