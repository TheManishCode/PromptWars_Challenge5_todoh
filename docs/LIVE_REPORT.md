# Live program report: mail, quotes, money, live data, hosting

2026-11-09. builder-todoh and reviewer-todoh. Base: `main` at `49619a5` (m6-done). Everything is on `live/integration` (not merged to `main`: the owner has not approved). Tags `live-m1-done` to `live-wp8-done` (as each is passed). **Nothing was sent, connected, deployed, paid for or pushed by this program; no account was created.** The only thing sent anywhere is the earlier push of `main` to GitHub, at the owner's request, before this program began.

Final gate (reviewer, clean worktree, `bebfb5d`): pytest **5221 passed, 4 skipped, 0 failed**; ruff, pyflakes, node, bandit 0; gitleaks on history and tracked files 0; `.env` untracked; `make ui` reproduces. The 4 skips are the opt-in Docling (2), live flights and voice-engine tests.

## 1. What is actually live

| Capability | Built | Tested on | Round-tripped for real? | Live? |
|---|---|---|---|---|
| Sending a vendor enquiry by email | yes (earlier work) | fake SMTP and IMAP (Python-level) and local socket servers | **no** | **not live** |
| Reading replies by email (thread search, overflow count, optional spam folder) | yes (LM3) | a fake IMAP that answers `SEARCH` | **no** (real Gmail `SEARCH` quoting and `HEADER` support unverified) | **not live** |
| Mailbox sign-in by OAuth (XOAUTH2) | yes (LM4) | the documented dialogue against local servers, a fake token service | **no** (no Google call made) | **not live** |
| Mailbox sign-in by app password | yes (earlier) | fakes | **no** | **not live** |
| A quote prices only the event it fits; promote uses its own reply | yes (LM1) | the full suite | n/a (no outside service) | **works in the app** |
| Replies filed to the right event or held for the user; brief survives into its plan | yes (LM2) | the full suite, two-process race | n/a | **works in the app** |
| Deposits, balances, hash-bound payment approvals, the user's "I paid", refunds | yes (WP4) | the full suite, two-process race, a hosted-browser journey | n/a: the app records and instructs | **works in the app** (moves no money) |
| PayPal | sandbox adapter, **off** | a fake PayPal | **no** (no sandbox credentials used; request body fields unverified) | **not live** |
| UPI links, gateway payment links | **not built** (NPCI spec unreadable; gateways collect, not pay) | none | no | **not live** |
| Google Places, Brave, OpenWeather, OpenStreetMap lookup, flights, AI model | read-only (earlier), with visible states (WP5) | fakes | **no** call made by this program | **not live** from this work |
| Behavioural evals against a real model | harness capped by calls | the cap, with a fake | **no** (`prompts/assistant.lock.json` says `not_run`) | **not run** |
| WhatsApp click-to-chat links, contact log, menu tiers, quote-file lines | yes (WP3) | the full suite | no API send exists | **works in the app** |
| A host, DNS, a certificate, a deployed site | plan only (WP6) | the same proxy path with a self-signed certificate (earlier) | **no** | **not live** |

"Works in the app" means it runs and is tested on fakes; it is not a claim that email, a payment or an outside service worked.

## 2. Per work package: status, commits, files

| Package | Status | Commits | Main files |
|---|---|---|---|
| LM1 (A1) a quote prices only its own event | **done and verified** (gate: 5055 passed, 4 skipped, 0 failed; PASS) | `d3707ad` `b4264b1` | `agents/vendor.py` (`quote_fits`), `pricing/observations.py`, `service.py`, `tests/test_quote_event_fit.py` |
| LM2 (A2, A4) wrong event never guessed; brief survives | **done and verified** (PASS, plus R-019 atomic assign) | `738cedd` `660cd3e` `c8d4fc4` | `messaging/conversations.py`, `messaging/reply_review.py`, `planning/brief_links.py`, `tests/test_reply_matching.py` |
| LM3 (A3, A5) replies not missed; honest status words | **done and verified** (PASS) | `860a34f` | `messaging/channels.py`, `tests/mailfake.py`, `tests/test_mail_poll.py` |
| LM4 (A6) OAuth sign-in | **built, not live-verified** (PASS on fakes) | `e533182` | `messaging/mail_oauth.py`, `docs/INTEGRATIONS.md`, `docs/EMAIL_SETUP.md`, `tests/test_mail_oauth.py` |
| WP1 remainder: un-promote, per-field evidence on Compare | **done and verified** (PASS) | `d9011f7` | `sourcing/quote_records.py`, `ui/src/panels/ComparePanel.jsx` |
| WP3 quiet vendors, contact log, menus, quote-file lines | **done and verified** (PASS; voice notes **not built**) | `0da98e5` `e95ebf1` | `sourcing/contact_log.py`, `sourcing/quote_template.py`, `tests/test_wp3_quotes.py` |
| WP4 deposits and balances | **built, not live-verified** (PASS after R-020 double-payment and R-021 fixes) | `5481d28` `e96f087` | `eventmate/payments/`, `web/routes/payments.py`, `tests/test_payments.py` |
| WP5 live data and models | **built, not live-verified** (PASS) | `ffbf46d` `06763b9` | `integrations/health.py`, `assistant/evals.py`, `tests/test_service_health.py` |
| WP6 hosting | **docs only** | `f90a508` | `docs/HOSTING_PLAN.md` |
| WP7 tests and evidence | **done and verified** | `82a3a45` | `tests/fixtures/replies/corpus.json`, `tests/test_reply_corpus.py`, `tests/test_e2e_money.py` |
| WP8 decisions | **presented, none taken** | `68ae62e` | `docs/OWNER_DECISIONS.md` |

Reviewer findings in this program: A1 to A6 (the audit, fixed in LM1 to LM4), R-019 (non-atomic assign, fixed), R-020 (a double payment after a lost answer, fixed: unknown outcomes are never retried or called failed), R-021 (a missing test on the freshness re-check, fixed). The ledger for the first program is `docs/REVIEW_FINDINGS.md`.

## 3. Official pages relied on, with the date read (2026-11-09)

Full table, with what each page does not say, in `docs/INTEGRATIONS.md`. Gmail: https://developers.google.com/workspace/gmail/imap/xoauth2-protocol (2026-09-15), https://developers.google.com/workspace/gmail/imap/imap-smtp (2026-11-03), https://support.google.com/accounts/answer/185833, https://support.google.com/mail/answer/22839, https://developers.google.com/identity/protocols/oauth2 (2026-10-29), https://developers.google.com/workspace/guides/configure-oauth-consent (2026-09-03). PayPal: https://developer.paypal.com/docs/payouts/standard/, https://developer.paypal.com/payouts/supported-features (2026-09-10), https://developer.paypal.com/api/rest/authentication/, https://developer.paypal.com/tools/sandbox/, https://developer.paypal.com/docs/api/payments.payouts-batch/v1/. Not readable (HTTP 403): the PayPal India help page and NPCI's UPI pages. Razorpay: https://razorpay.com/docs/payments/payment-links/ (2026-10-08), https://razorpay.com/pricing/. Data: https://developers.google.com/maps/billing-and-pricing/pricing (2026-11-03), https://developers.google.com/maps/documentation/places/web-service/policies (2026-11-06), https://wiki.openstreetmap.org/wiki/Overpass_API, https://openweathermap.org/price, https://api-dashboard.search.brave.com/documentation/pricing, https://openrouter.ai/docs/api-reference/limits (numbers not rendered), NVIDIA (nothing rendered). Hosts: Render, Fly.io, Google Cloud free tier (2026-10-27), Oracle Always Free (`docs/HOSTING_PLAN.md`).

## 4. What needs the owner or a provider (exact action in `docs/BLOCKED.md`)
- Approve the sender account, the recipient and the exact words for one real mailbox round trip and a bounce test, with the owner's own addresses only; then create the OAuth client and refresh token (`docs/EMAIL_SETUP.md`).
- Answer the payment-route questions (`docs/OWNER_DECISIONS.md` section C); supply PayPal sandbox credentials only if the sandbox run is wanted.
- Supply a free model key if the behavioural evals are wanted (capped; free route only).
- Choose a host, a domain and a certificate (`docs/HOSTING_PLAN.md`).
- Decide the rest of `docs/OWNER_DECISIONS.md`: the shared quote pool's source, the repository licence, counsel's questions.

## 5. Commands, from a clean clone
As in `docs/FINAL_REPORT.md` section 2, plus:
```sh
python -m pytest -q tests/test_reply_corpus.py tests/test_e2e_money.py tests/test_payments.py tests/test_mail_poll.py tests/test_mail_oauth.py   # the corpus, the money chain, payments, mail
python -m eventmate.messaging.smoke_email     # no send: signs in by the chosen route and opens the inbox read-only
python -m eventmate.assistant.evals --behavioral --max-calls 40   # NOT run here: needs a key you supply; free route only
```
Placeholders added to `.env.example` and `deploy/env.example` (empty): `EVENTMATE_IMAP_SPAM_FOLDER`, `EVENTMATE_MAIL_AUTH`, `EVENTMATE_GMAIL_CLIENT_ID`, `EVENTMATE_GMAIL_CLIENT_SECRET`, `EVENTMATE_GMAIL_REFRESH_TOKEN`, `EVENTMATE_PAYPAL_MODE`, `EVENTMATE_PAYPAL_CLIENT_ID`, `EVENTMATE_PAYPAL_CLIENT_SECRET`, `EVENTMATE_EVAL_MAX_CALLS`. The same names were appended, empty, to the local `.env`; no existing value was read, printed or changed.

## 6. Known issues, by severity
1. **Medium:** nothing here has met a real mailbox, Google, PayPal or a model. The fakes prove what the app asks and does, not that the providers answer the same way (Gmail `SEARCH` quoting, the payouts request body, token refresh).
2. **Medium:** a Google OAuth consent screen in "Testing" status issues refresh tokens that expire in 7 days; whether "In production" for a single-user app avoids it is unconfirmed.
3. **Medium:** a payment whose outcome is unknown can only be settled by the user; there is no provider lookup by our own id (no documented endpoint was found).
4. **Low:** voice-note replies are not built; UPI and gateway links are not built; flights use an unofficial library.
5. **Low:** the 3-day "quiet" threshold, the 200-message poll cap and the per-brief cap of 10 are guesses (D-018, D-015, D-007).
6. **Low:** Arm hosts need an untested Arm image.

## 7. Session log (2026-11-09)
- Owner's new program received through the reviewer; audit A1 to A6 on `49619a5`; `live/integration` cut from `main`; each milestone on its own branch, merged after a PASS.
- LM1 (quote fit), LM2 (held replies, brief links), LM3 (IMAP threads, delivery words), LM4 (OAuth, `docs/INTEGRATIONS.md`) passed; R-019 folded into LM3.
- WP1 and WP3 passed. WP4 failed once (R-020 double payment, R-021 untested freshness) and passed after the fixes; the reviewer verified one payout POST in his scenario.
- WP5 passed; WP6 to WP8 built; this report.
- A process slip to note: none involving secrets this time. The pre-commit hook ran on every commit.
