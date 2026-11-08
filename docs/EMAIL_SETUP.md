# Email setup (vendor messages and the optional owner copy)

Email works through a mailbox you own, over SMTP (sending) and IMAP (reading replies). **Status: tested with fake SMTP/IMAP and with local servers that speak the XOAUTH2 dialogue; not yet validated against a real mailbox** (`docs/BLOCKED.md`).

## 1. Use a dedicated mailbox
Create a mailbox just for this, not your main one. Vendors will see its address, and the app password sits on this machine.

## 2. Choose how the app signs in: an app password, or OAuth
`EVENTMATE_MAIL_AUTH=password` (the default) or `oauth2`. Google's own page says app passwords "aren't recommended and are unnecessary in most cases" and that they exist only for accounts with 2-Step Verification (https://support.google.com/accounts/answer/185833, read 2026-11-09); OAuth is the supported route. Both are built; use the one your account allows. Sources and dates for everything below are in `docs/INTEGRATIONS.md`.

**Password route.** Turn on 2-Step Verification, create an app password for this app (Google shows it once; changing the account password revokes it), and put it in `EVENTMATE_SMTP_PASSWORD` (step 3). Never use the normal account password.

**OAuth route (Gmail), one time, on your own computer.** The three secrets never go into chat, source, a browser page or a log.
1. In the Google Cloud console create a project and set up the OAuth consent screen (https://developers.google.com/workspace/guides/configure-oauth-consent): user type **External**, add the dedicated mailbox as a **test user**. The console's menu names change; follow Google's guide.
2. Create an OAuth client ID (a "Web application" or "Desktop app" client) and copy its **client ID** and **client secret** into `.env` as `EVENTMATE_GMAIL_CLIENT_ID` and `EVENTMATE_GMAIL_CLIENT_SECRET`.
3. Authorize the scope `https://mail.google.com/` for the dedicated mailbox with `access_type=offline` and keep the **refresh token** it returns (returned only on the first authorization). Google's OAuth 2.0 Playground can do this with your own client credentials (https://developers.google.com/oauthplayground/): set it to use your own credentials, authorize that scope, exchange the code, copy only the refresh token into `EVENTMATE_GMAIL_REFRESH_TOKEN`.
4. Set `EVENTMATE_MAIL_AUTH=oauth2`. Leave `EVENTMATE_SMTP_PASSWORD` empty.
5. **Expiry, read this:** while the consent screen's publishing status is **Testing**, Google issues refresh tokens that **expire in 7 days**; a token unused for **six months**, a password change on the account, or revoking access also ends it (https://developers.google.com/identity/protocols/oauth2, 2026-11-09). When it ends the app says "Google rejected the refresh token" in the Replies step and in `smoke_email`; repeat step 3. Whether moving the app to "In production" (an unverified app, only for your own mailbox) stops the weekly expiry is for you to confirm on the real account: it was not tested here.
6. **Revoke:** remove the app at https://myaccount.google.com/permissions, or delete the OAuth client in the console. Both stop it at once. For an app password: remove it under App Passwords.
7. **Isolation:** use this mailbox for nothing else. If it is lost, only vendor mail from this mailbox is exposed, and nothing of your main account.

## 3. Put the settings in `.env` only
Copy `.env.example` to `.env`, then `chmod 600 .env`. Fill in:

```
EVENTMATE_SMTP_HOST=smtp.gmail.com
EVENTMATE_SMTP_PORT=465
EVENTMATE_SMTP_USER=your.dedicated.mailbox@example.com
EVENTMATE_SMTP_PASSWORD=<the app password>       # password route only
EVENTMATE_IMAP_HOST=imap.gmail.com
EVENTMATE_IMAP_PORT=993
# OAuth route instead of the password:
EVENTMATE_MAIL_AUTH=oauth2
EVENTMATE_GMAIL_CLIENT_ID=<from step 2>
EVENTMATE_GMAIL_CLIENT_SECRET=<from step 2>
EVENTMATE_GMAIL_REFRESH_TOKEN=<from step 3>
```

`.env` is git-ignored. Do not put these values in code, tests, docs, logs or commits. Port 465 uses TLS from the start; any other SMTP port uses STARTTLS and never falls back to plain text.

## 4. Check the settings without sending anything
```
python -m eventmate.messaging.smoke_email
```
It logs in to SMTP and IMAP by the route you chose (for OAuth it asks Google for one access token, which is a read-only call; it sends no mail) and opens the inbox read-only, and prints only PASS or FAIL and the host. It never prints the user name, the password or the server's own error text. A real one-message test needs both `--send-test-to ADDRESS --i-own-this-address`. **That test message is a direct send, outside the app's sending controls:** it goes straight to the mail server, so the pause, the kill switch, the do-not-contact list, the daily limits and the approval of exact text do not apply to it, and it is recorded in no plan. It prints a `DIRECT TEST SEND` line before it sends. Use it only to check your own mailbox.

## 5. Sending limits
Providers limit how much you can send. Gmail's limits (500 messages a day for a personal account, read 2026-11-09) are at https://support.google.com/mail/answer/22839. to;doh-the event planner's own limits are lower: 3 messages per vendor and 30 per day, and the optional owner copy at most 4 a day, 30 minutes apart. Do not raise them without checking your provider's rules.

## 6. How to stop
- **Pause (holds everything):** the **Sending** chip in the dashboard, `POST /api/sending/pause`, or `python -m eventmate inbox pause`. It is workspace-wide: every plan. Nothing that would leave goes out, whoever approved it: messages you approved, messages sent under a standing permission, and the owner email copy. Nothing is lost: approved messages stay approved and wait. An approval lasts **48 hours** (**provisional**: chosen as longer than the 24-hour send limits so ordinary waiting never lapses, and not validated against real use); a message held longer (paused, held by a limit, the app down) is **not sent** and needs a fresh draft, which is a new approval of its text, and the dashboard lists what ran out. Page watches keep reading public pages while paused; they send nothing, and the emails about what they find are held. Lifting the pause sends what is waiting, so it is two steps: the dashboard lists the held messages (vendor, channel, subject, how old the approval is) and sends nothing until you confirm; the API and CLI must name the exact queue they reviewed by its snapshot id (`GET /api/sending` gives `held_snapshot`; `{"snapshot": "..."}` on resume; `inbox unpause --snapshot ID`). A different queue, even one of the same size, releases nothing and answers 409 (the CLI exits 3) with the current list. The check and the lift of the pause are one step under the store lock that every approval and every send takes, so an approval from the web server, the command line or the worker cannot slip in between: what is approved after the lift is an ordinary approval with sending on. The released messages then go through the same checks as any message (the daily and per-vendor caps, the plan still matching the text, the do-not-contact list, the 48-hour approval), so the answer reports how many were **queued** and how many were sent, are still waiting, or were stopped and why; queued is not a promise that many will go. **Where the pause takes effect.** A message's send is claimed under the store lock, and the pause is read there (so a sender that read it earlier still sends nothing), and again immediately before the message is handed to the channel: if it came in between, the claim is backed out and the approval time is kept. The owner email sender checks the switch and the pause again right before it sends. What cannot be done: a message already handed to the channel (the SMTP or WhatsApp call under way) cannot be recalled, so at most the sends in flight at that instant, one per sending process, still go out; there is a window of microseconds between the last check and the call that no lock can close without holding the lock across the network call. **What was checked:** the only two ways a draft becomes approved (`approve`, and a standing permission's follow-up) both go through `transition()`, which takes the cross-process file lock; drafts are sent only from the dispatcher; the owner email sender is a separate path: it had no lock, and a test showed two flushes at once both emailing the same digest, so it now takes its own non-blocking cross-process lock and a second flusher steps aside (the owner gets one copy); the `smoke_email` test message (a command-line tool that mails only an address you give it) does not honour the pause. A test with a second operating-system process shows its approval waiting for the lift (and, with the lock removed, being written while the pause is still on). It does not touch the kill switch.
- **Kill switch (automatic messages only):** `python -m eventmate inbox kill`, `POST /api/autoreply/kill`, or **Automatic messages only** in the Sending dialog. It stops follow-ups sent under a standing permission, the owner email copy and page watches, and denies automatic messages already queued. A message you approved yourself **still goes out**: pause to hold those too. `inbox resume` lifts it. Pausing never sets it and resuming never clears it.
- **Stop file:** set `EVENTMATE_WORKER_STOP=/path/to/STOP` and create that file. The worker loop and the owner email copy stop while it exists.
- **Off for one plan:** turn the automatic follow-up permission off in the Vendor chats tab.
- **Owner email copy:** off by default; turn it off in the Updates box at any time.

## 7. What is never sent
Request templates and automatic follow-ups never contain your budget or a price. Text you type yourself is sent as you wrote it. Vendor replies are untrusted text: they never record a quote, change a plan, or approve anything.
