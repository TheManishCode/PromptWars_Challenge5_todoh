# Blocked

Nothing blocked at L0. Items needing the owner, from `docs/LAUNCH_PLAN.md` section 6, are tracked there.

## Needs the owner

- **Shared earlier quotes** (R-013): the feature cannot receive a record in any shipped configuration. Decide: wire a mailbox-fed source the operator runs, or remove the feature. Until then it is dormant, and the docs say so. Nothing else depends on it.
- **Screen-reader pass** (L5, a human task): neither of us can do this honestly. With NVDA (Windows, Firefox or Chrome) or VoiceOver (macOS, Safari), go from `/` to `/plan` to a built plan using only the keyboard and the reader. Tick each: (1) the page title and the one `main` landmark are announced; (2) the brand link is read as "to;doh, back to the start"; (3) "Tell us your idea" is reachable and its textarea is labelled; (4) the review question and Yes/No read as a group named "Is this right?"; (5) each findings step announces its name and the active one is marked; (6) every button reads its action ("Plan with this vendor", "Build the plan", "I sent it"); (7) an error (a refused request, a failed build) is announced without moving focus away; (8) the built plan's vendors, total and timeline read in order and "Nothing has been booked or paid" is heard. Record the reader, browser and what failed.

## Not run: the mailbox (LM3, LM4)

Everything below is built and tested against fakes and local protocol servers. **None of it has been run against a real mailbox, Google, or any vendor.** The owner's approval of the sender account, the recipient address and the exact words is needed first (the owner's own addresses only).
- **Real round trip, password or OAuth:** `python -m eventmate.messaging.smoke_email` (no send) with real settings, then one approved request to the owner's own second address through the app's normal approval path, a reply from that address, a bounce to a non-existent address of the owner's own domain, and a STOP reply.
- **Google OAuth:** create the client and consent screen, obtain the refresh token (`docs/EMAIL_SETUP.md`); confirm the real refresh request's fields and answer; confirm how long the token lasts (a "Testing" consent screen gives 7 days) and whether "In production" for an unverified, single-user app stops that.
- **IMAP search against Gmail:** the app asks for `SINCE`, `FROM "addr"`, `HEADER In-Reply-To "<id>"` and `HEADER References "<id>"`; the fake proves the app asks, not that Gmail answers the same. Check the replies are found, the overflow note appears, and `[Gmail]/Spam` is readable by `EVENTMATE_IMAP_SPAM_FOLDER` if wanted.
- **Sending limits:** Gmail allows 500 a day from a personal account (read 2026-11-09); the app's own limits are lower (30 a day, 3 a vendor, 10 a brief).

## Payments (WP4): built, not run, and the route is the owner's to choose

Built and tested against fakes: terms from a confirmed quote, approvals bound to a hash and expiring, the desk, the manual instruction, a PayPal **sandbox** adapter (off). **No payment route is chosen and none has been run.**
Questions only the owner can answer, before any provider is switched on (`docs/LAUNCH_PLAN`-style list, from the live prompt):
1. Which country is the owner's PayPal account in, and is it a business account? (PayPal Payouts cannot send from an account registered in India: `docs/INTEGRATIONS.md`.)
2. Do the vendors have PayPal accounts, and are they in India? Domestic India-to-India PayPal payments are not supported per the owner's note (the PayPal India page could not be read: HTTP 403).
3. For India-to-India vendors the route is **unverified**: UPI (NPCI's linking specification could not be read, HTTP 403) or a gateway (a merchant collecting, not paying). Choose, then the specification must be read before anything is built.
4. Who is the payee in each case: the vendor, or the owner collecting from customers (then PayPal Orders or Invoicing, or a gateway, might fit and Payouts would not)?
Not run: the PayPal sandbox round trip (needs sandbox credentials from the owner; the request body fields are not verified), any live account, any real payment, any refund.

## Live data and models (WP5): built, not run against any live service

- Service states, the Find-vendors note and the call-capped evals are tested with fakes. **No live Places, Brave, OpenWeather, Overpass, flight or model call was made.**
- **Behavioural evals: not run** (`prompts/assistant.lock.json` stays `not_run`). Needs a key the owner supplies through the secure route, on a free route only; the run stops at `EVENTMATE_EVAL_MAX_CALLS` (default 120). The owner should confirm the free-tier terms of the chosen provider first (OpenRouter's free-model limits were not rendered, NVIDIA's pages gave no text: `docs/INTEGRATIONS.md`).
- Owner actions that may involve an account or a payment method: a Google Maps Platform key (billing account), a Brave key (prepaid credits), an OpenWeather key (free, email sign-up). Nothing was created.

## Hosting (WP6): nothing run, nothing chosen
- **The host is the owner's to choose.** `docs/HOSTING_PLAN.md` has what free hosts say (2026-11-09) and the ordered steps. Render's free tier cannot keep this app's data; Fly has no free tier; the free options that fit are a VM (Google e2-micro, Oracle Always Free), each needing a card and a check of its own terms. An Arm host needs an Arm image, untested.
- Not run: any deploy, any real host, DNS, a certificate, persistence across a restart, outbound mail from a host, a restore on a real host.

## Payments: reconciliation
- Reconciliation of an unknown payment outcome is the user's two steps ("it went through" / "it did not") until a documented lookup by sender batch id is confirmed. No unverified endpoint is called on a money path.

