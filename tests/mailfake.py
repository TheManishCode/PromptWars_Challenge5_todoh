"""A fake mail world for tests: the real EmailChannel talks to a fake SMTP server and a fake IMAP mailbox. No network, no credentials.

The vendor is a function: it reads what was sent (the outbox) and writes replies into the mailbox, with real headers, so threading and parsing are exercised.
"""

import email
import email.utils
import imaplib
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from eventmate.messaging.channels import EmailChannel, EmailConfig

CFG = EmailConfig(smtp_host="smtp.fake.test", imap_host="imap.fake.test", user="agent@fake.test", password="not-a-real-password", from_addr="agent@fake.test")


class FakeSMTP:
    def __init__(self, world):
        self.world = world

    def login(self, user, password):
        if self.world.login_error:
            raise self.world.login_error

    def send_message(self, msg):
        if self.world.send_error:
            raise self.world.send_error
        self.world.outbox.append(msg)

    def noop(self):
        if self.world.login_error:
            raise self.world.login_error
        return (250, b"ok")

    def quit(self):
        self.world.smtp_quit = getattr(self.world, "smtp_quit", 0) + 1


class FakeIMAP:
    """A mailbox that answers SEARCH the way an IMAP server does for the criteria the app uses (SINCE, FROM, HEADER) and keeps folders apart. Opening anything but read-only fails the test."""

    def __init__(self, world):
        self.world = world
        self.current = "INBOX"

    def login(self, user, password):
        if getattr(self.world, "imap_error", None):
            raise self.world.imap_error

    def select(self, box, readonly=False):
        assert readonly  # polling never changes the mailbox
        name = box.strip('"')
        if name not in self.world.folders:
            return "NO", [b"no such mailbox"]
        self.current = name
        return "OK", [str(len(self.world.folders[name])).encode()]

    def _messages(self):
        return self.world.folders[self.current]

    def search(self, charset, *criteria):
        self.world.searches.append(criteria)
        crit = list(criteria)
        keep = list(range(1, len(self._messages()) + 1))
        while crit:
            word = crit.pop(0).upper()
            if word == "SINCE":
                day = datetime.strptime(crit.pop(0), "%d-%b-%Y").date()
                keep = [n for n in keep if _headers(self._messages()[n - 1]).get("date-day", day) >= day]
            elif word == "FROM":
                want = crit.pop(0).strip('"').lower()
                keep = [n for n in keep if want in _headers(self._messages()[n - 1]).get("from", "").lower()]
            elif word == "HEADER":
                if self.world.no_header_search:
                    raise imaplib.IMAP4.error("SEARCH HEADER is not supported")
                name, want = crit.pop(0).lower(), crit.pop(0).strip('"')
                keep = [n for n in keep if want in _headers(self._messages()[n - 1]).get(name, "")]
            else:
                raise AssertionError(f"the app searched for something this fake does not know: {word}")
        return "OK", [b" ".join(str(n).encode() for n in keep)]

    def fetch(self, num, spec):
        assert "PEEK" in spec
        return "OK", [(b"1 (BODY[] {n}", self._messages()[int(num) - 1]), b")"]

    def logout(self):
        pass


def _headers(raw: bytes) -> dict[str, str]:
    msg = email.message_from_bytes(raw)
    out = {k.lower(): str(v) for k, v in msg.items()}
    try:
        out["date-day"] = email.utils.parsedate_to_datetime(out["date"]).date()
    except (KeyError, TypeError, ValueError):
        pass
    return out


class MailWorld:
    def __init__(self, clock):
        self.clock = clock
        self.outbox: list[EmailMessage] = []
        self.folders: dict[str, list[bytes]] = {"INBOX": [], "Spam": []}
        self.inbox = self.folders["INBOX"]
        self.searches: list[tuple] = []
        self.no_header_search = False
        self.send_error: Exception | None = None
        self.login_error: Exception | None = None
        self._n = 0

    def channel(self) -> EmailChannel:
        return EmailChannel(CFG, smtp_factory=lambda: FakeSMTP(self), imap_factory=lambda: FakeIMAP(self), now=self.clock)

    def sent_to(self, address: str) -> list[EmailMessage]:
        return [m for m in self.outbox if m["To"] == address]

    def last_to(self, address: str) -> EmailMessage:
        return self.sent_to(address)[-1]

    def reply(self, address: str, body: str, *, sender: str | None = None, minutes: int = 5, message_id: str | None = None, thread: bool = True, extra: dict | None = None) -> str:
        """The vendor at ``address`` answers the newest message sent to them. Returns the new Message-ID."""
        sent = self.last_to(address)
        self._n += 1
        mid = message_id or f"<vendor-{self._n}@vendor.test>"
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = sender or address, CFG.from_addr, "Re: " + str(sent["Subject"])
        m["Date"] = email.utils.format_datetime(self.clock() + timedelta(minutes=minutes))
        m["Message-ID"] = mid
        if thread:
            m["In-Reply-To"] = str(sent["Message-ID"])
        for k, v in (extra or {}).items():
            m[k] = v
        m.set_content(body)
        self.inbox.append(m.as_bytes())
        return mid


def _dsn(world, address, *, permanent=True, original_message_id=None, status=None, action=None, with_original="headers", sender="MAILER-DAEMON@googlemail.com", diagnostic="smtp; 550 {status} user unknown"):
    world._n += 1
    sent = [m for m in world.outbox if m["To"] == address]
    orig = original_message_id if original_message_id is not None else (str(sent[-1]["Message-ID"]) if sent else "<unknown@elsewhere.test>")
    id_line = f"Message-ID: {orig}\n" if orig else ""
    status = status or ("5.1.1" if permanent else "4.2.2")
    action = action or ("failed" if permanent else "delayed")
    original = (
        f"--B\nContent-Type: text/rfc822-headers\n\n{id_line}From: {CFG.from_addr}\nTo: {address}\nSubject: Quote request\n\n" if with_original == "headers"
        else f"--B\nContent-Type: message/rfc822\n\nMessage-ID: {orig}\nFrom: {CFG.from_addr}\nTo: {address}\nSubject: Quote request\n\nbody\n\n" if with_original == "message" else ""
    )
    raw = (
        f"From: {sender}\nTo: {CFG.from_addr}\nSubject: Delivery Status Notification (Failure)\nDate: {email.utils.format_datetime(world.clock() + timedelta(minutes=2))}\n"
        f"Message-ID: <dsn-{world._n}@googlemail.com>\nMIME-Version: 1.0\nContent-Type: multipart/report; report-type=delivery-status; boundary=\"B\"\n\n"
        f"--B\nContent-Type: text/plain; charset=utf-8\n\nYour message could not be delivered.\n\n"
        f"--B\nContent-Type: message/delivery-status\n\nReporting-MTA: dns; googlemail.com\n\nFinal-Recipient: rfc822; {address}\nAction: {action}\nStatus: {status}\nDiagnostic-Code: {diagnostic.format(status=status)}\n\n"
        f"{original}--B--\n"
    )
    world.inbox.append(raw.encode())
    return orig


MailWorld.bounce = lambda self, address, **kw: _dsn(self, address, **kw)


def disconnected():
    return smtplib.SMTPServerDisconnected("connection lost during send")


__all__ = ["CFG", "MailWorld", "datetime", "disconnected"]
