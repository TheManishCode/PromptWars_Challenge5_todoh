# Blocked

Nothing blocked at L0. Items needing the owner, from `docs/LAUNCH_PLAN.md` section 6, are tracked there.

## Needs the owner

- **Shared earlier quotes** (R-013): the feature cannot receive a record in any shipped configuration. Decide: wire a mailbox-fed source the operator runs, or remove the feature. Until then it is dormant, and the docs say so. Nothing else depends on it.
- **Screen-reader pass** (L5, a human task): neither of us can do this honestly. With NVDA (Windows, Firefox or Chrome) or VoiceOver (macOS, Safari), go from `/` to `/plan` to a built plan using only the keyboard and the reader. Tick each: (1) the page title and the one `main` landmark are announced; (2) the brand link is read as "to;doh, back to the start"; (3) "Tell us your idea" is reachable and its textarea is labelled; (4) the review question and Yes/No read as a group named "Is this right?"; (5) each findings step announces its name and the active one is marked; (6) every button reads its action ("Plan with this vendor", "Build the plan", "I sent it"); (7) an error (a refused request, a failed build) is announced without moving focus away; (8) the built plan's vendors, total and timeline read in order and "Nothing has been booked or paid" is heard. Record the reader, browser and what failed.

