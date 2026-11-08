# Decisions for the owner

Each item says what is open, the options as far as they are known, what each option costs or preserves, and how to undo it. **Nothing here is decided by the builder or the reviewer.** The wording of every row of `docs/DECISIONS.md` is the record of what was done meanwhile; the rows marked REVIEW there are the ones below. Counsel questions are at the end. This is not legal advice.

## A. Decisions that were made provisionally and are yours to confirm or change

| Id | What stands now | Options | To change |
|---|---|---|---|
| D-001 | The docs in `docs/` are tracked in git although `.gitignore` says "not published for now". They are public if the repository is ever public (it is on GitHub: `origin`; `main` was pushed on 2026-11-09). | (a) keep them public; (b) untrack the ones you want private; they stay in history, so a history rewrite would be needed to remove them | `git rm --cached docs/<file>`; rewrite history before any further push if needed |
| D-002 | Borrowed art replaced by ink line art we drew, with a party-hat mascot, in `ui/src/components/Art.jsx`. | (a) keep this look; (b) commission or draw a different one; (c) restore any original art only with written rights (the source notes and files are in `../_quarantine-borrowed-art/`) | edit `Art.jsx`, `make ui` |
| D-004 | `# nosec B108` on the sandbox's in-sandbox tmpfs path. | keep, or restructure the sandbox code so the scanner has nothing to flag | delete the comment: the bandit gate goes red |
| D-006 | The landing hero photo and video were removed. | (a) plain gradient (now); (b) a hero with media whose licence you confirm; (c) your own photo or video | restore the tags, list the files in `STATIC_FILES` and `docs/ATTRIBUTIONS.md` |
| D-007 | At most 10 first requests per brief or plan per 24 hours (with 30 a day and 3 a vendor). The number 10 is a guess. | any number; or none beyond the daily cap | `SendPolicy.per_plan_per_day` |
| D-011 | That cap restarts when a brief becomes a plan (two different ids); the global 30 a day is the real bound. | count across the brief-to-plan link (now possible through `planning/brief_links.py`) | small code change plus a test |
| D-012 | Every first request ends with "Reply STOP and we will not write again", including ones the user copies and sends from their own mailbox, where the app never sees the reply. | (a) the same text everywhere; (b) a different footer for manual drafts ("if you would rather not hear from me, just say so"), still covered by the approved hash | `outreach._message` |
| D-013 | A recorded quote prices only an event it was given for (same date; a per-plate price only for the quoted guest count or inside the vendor's own min and max). "Inside min and max" trusts the vendor's catalogue range. | keep; or require the exact guest count | `agents/vendor.py: quote_fits` |
| D-014 | A reply held for the user keeps its words only; its attachment is not read. | keep, or read the attachment after the user picks the event (it is read then, today, only if the reply is later filed normally) | `messaging/reply_review.py` |
| D-015 | Mail is searched by the threads we know, up to 200 messages a poll plus 20 others; an optional read-only spam folder (off). | change the limits; turn the spam folder on | `messaging/channels.py`, `EVENTMATE_IMAP_SPAM_FOLDER` |
| D-017 | OAuth (XOAUTH2) beside the app password; the owner makes the refresh token once outside the app. A consent screen in "Testing" status gives tokens that last 7 days (Google, 2026-11-09). | (a) app password (if the account allows); (b) OAuth in "Testing" (renew weekly); (c) OAuth "In production" for a single-user unverified app (whether that stops the weekly expiry is **not confirmed**) | `EVENTMATE_MAIL_AUTH` |
| D-018 | A request with no reply after 3 days is called quiet in words; nothing chases a vendor. | change the days; or add a chase that goes through the same approval | `EventMate.SILENT_AFTER_DAYS` |
| D-019 | **The payment route is not chosen.** See section C. | see C | `docs/INTEGRATIONS.md` |
| D-022 | No host is chosen. | see `docs/HOSTING_PLAN.md` | n/a |

## B. The shared earlier-quote pool (R-013, D-009)

The pool is kept: quotes are central to the product and the privacy rules stay (the month of the event, never the day; another person's date never leaves the store; pasted replies are never written to it; no names, contacts or words are kept).

**What is true today:** it cannot receive a record in any shipped configuration. It is fed only by a reply that arrives through a configured mailbox; a hosted visitor cannot send or receive mail and pasted replies are excluded on purpose (anyone could type any price); local mode builds no shared store. After a full hosted journey there is no `shared/` directory at all.

**What is missing before it can work, and who has to decide each:**
1. **A real source of records.** The one clean source is a mailbox the owner operates: the vendor's reply to a request the operator's own mailbox sent. That needs the owner's mailbox set up (`docs/EMAIL_SETUP.md`) and a decision to send from it for visitors. Today visitors cannot send (SENDING_ROUTES): this is a separate decision with its own risks (the sender's reputation, volume, vendor consent).
2. **Where it lives and for how long.** `<data>/shared/shared_quotes`, one file; kept until 90 days after a quote stopped holding. The owner decides whether that retention is right, and who may read the file (the operator can).
3. **Consent wording.** Visitors should be told, in the page and the privacy notice, that a quote their reply produced may be shown (as figures only) to the next person who asks the same vendor. Vendors are told nothing today: whether a vendor's price to one person may be shown to another is a question for counsel (below). The current notice text is in `docs/PRIVACY.md`.
4. **Who operates it**, and who answers a vendor who asks for a figure to be removed (there is no per-record owner, so removal means deleting the file or a record by quote id: no tool exists for the second).
5. **Deletion.** Records carry no owner, so "remove my data" cannot find them. Options: keep it as a documented limit; or store a keyed hash of the contributor to allow removal (adds an identifier to a store designed to hold none).

## C. Payments (WP4): the route

Not run, not chosen. The app has a manual instruction (moves nothing) and a PayPal **sandbox** adapter that is off. What the official pages say, with dates and what was not readable, is in `docs/INTEGRATIONS.md`. Questions only the owner can answer:
1. Which country is the owner's PayPal account in, and is it a business account? (PayPal Payouts cannot send from an account registered in India.)
2. Do the vendors have PayPal accounts, and are they in India?
3. For India-to-India vendors: UPI (NPCI's specification could not be read) or a gateway (a merchant collecting, not paying)? Neither is built.
4. Is the app paying vendors, or is the owner collecting from customers? (Different products.)
5. Whether to move any money through the app at all, rather than only recording and instructing.

## D. The repository's own licence

There is **no `LICENSE` file**: the code is "all rights reserved". The container image ships Fast Downward (GPL-3.0-or-later) as a separate program with its source; the project's own code is not linked to it (`docs/LICENSES.md`, audit of 2026-11-04). Options the audit found open: a permissive licence (MIT or Apache-2.0), a copyleft one (GPL-3.0 or AGPL-3.0, since it is a network service), or keeping it closed. Contributor terms (`CONTRIBUTING.md` says nothing) are part of the choice. Counsel can say whether shipping the GPL program beside closed code in one image raises anything more than the audit found.

## E. Questions for counsel (none has been asked)
1. The vendor cards and "Found online" content next to Google Places results: what may be shown and stored (`DECIDED(cards-legal)` in `sourcing/cards.py`; only the place id is stored; the name the user types and the vendor's own page URL are kept as the user's and the vendor's own content).
2. The shared quote pool (section B): consent, a vendor's price shown to another person, a deletion route.
3. Vendor outreach: the sender line and STOP text, an operator's mailbox writing to vendors on visitors' behalf, anti-spam rules where the vendors and the operator are.
4. Payment instructions: whether recording and instructing (not holding money) needs anything, and the wording "the app does not guarantee that a vendor delivers".
5. Privacy notice and retention (`docs/PRIVACY.md`): whether it is enough for the countries visitors are in.
6. WhatsApp: the code uses WAHA (an unofficial WhatsApp Web wrapper); its terms and the account-ban risk were not checked. The official Cloud API gives 1,000 free service conversations a month within the 24-hour window (the owner's note; not re-read).
7. The repository's licence and the GPL program in the image (section D).
