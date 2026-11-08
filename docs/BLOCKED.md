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

