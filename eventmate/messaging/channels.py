"""Channels the agent talks to vendors through: email over SMTP/IMAP and WhatsApp through a self-hosted WAHA gateway.

A channel only moves text. It decides nothing: what is sent, to whom and when is decided by the approval flow in
`conversations.py`. Secrets come from the environment and never appear in a repr, a status or an error message.
Everything a vendor sends back is untrusted text: it is cleaned, size-capped and returned as data.

WhatsApp note: WAHA drives a WhatsApp Web session. That is not the official Business API and WhatsApp's terms do not
allow unofficial clients, so the account can be restricted. to;doh-the event planner therefore sends slowly, only after approval, and
only to numbers WhatsApp reports as registered.
"""

from __future__ import annotations

import dataclasses
import email
import hashlib
import os
import email.policy
import email.utils
import http.client
import imaplib
import re
import smtplib
import ssl
import unicodedata
from html.parser import HTMLParser
from dataclasses import dataclass, field
from datetime import datetime, timedelta, UTC
from email.message import EmailMessage
from typing import Protocol
from collections.abc import Callable, Mapping, Sequence
from urllib.parse import quote, urlencode, urlsplit

from eventmate.netutil import NETWORK_ERRORS, loads, read_capped
from eventmate.schemas import ChannelStatus, _EMAIL
import contextlib

MAX_BODY_CHARS = 4000
MAX_POLL_MESSAGES = 50  # WhatsApp: the newest this many chats per poll
MAX_THREAD_MESSAGES = 200  # email: the newest this many messages that are replies to, or from the people of, threads we know; anything beyond is counted, never dropped silently
MAX_OTHER_MESSAGES = 20  # email: the newest this many other messages of the window, for stop requests and delivery notices that name nothing we sent
MAX_FETCH_BYTES = 262_144
TIMEOUT_S = 20
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ChannelError(Exception):
    """The message was definitely not sent (or the channel could not be read)."""


class OutcomeUnknown(ChannelError):
    """The message may or may not have gone out. Never resend automatically."""


@dataclass(frozen=True)
class ThreadRef:
    in_reply_to: str = ""
    references: str = ""


@dataclass(frozen=True)
class KnownThread:
    draft_id: str
    recipient: str
    sent_at: datetime
    external_id: str


@dataclass(frozen=True)
class PollReport:
    """What the last poll of a channel did, so a quiet mailbox and a mailbox that could not be read in full do not look the same."""

    examined: int = 0  # messages that are replies to, or from the people of, threads we know
    fetched: int = 0
    overflow: int = 0  # of those, how many were left for the next poll because of the per-poll limit
    spam_found: int = 0  # messages found in the optional spam folder, shown to the user and not filed
    spam_error: str = ""  # why the spam folder could not be read, when it could not
    mode: str = "threads"  # "threads": searched by the threads we know; "window": the server could not search that way, so the newest messages since the oldest open thread were read


@dataclass(frozen=True)
class Bounce:
    """A delivery-failure notice (an RFC 3464 report). Only the structured fields are read; the remote server's own words are not kept."""

    permanent: bool  # Action: failed with a 5.x.x status
    recipient: str  # who the notice says it could not deliver to
    original_message_id: str = ""  # the Message-ID of the message that failed, from the returned headers
    status: str = ""  # the report's Status code, such as 5.1.1
    diagnostic: str = ""  # the remote server's own words, cleaned and capped; untrusted, shown to the user as such


@dataclass(frozen=True)
class Inbound:
    channel: str
    sender: str
    external_id: str
    received_at: datetime
    body: str
    subject: str = ""
    in_reply_to: str = ""
    references: str = ""
    attachments: int = 0
    truncated: bool = False
    bounce: Bounce | None = None
    folder: str = "INBOX"  # where it was found: anything but the inbox (the spam folder) is shown to the user and never filed, read as a quote or acted on
    files: tuple[tuple[str, bytes], ...] = field(default=(), repr=False, compare=False)  # (name, bytes) of up to MAX_QUOTE_FILES attachments a quote could be read from; never stored, only handed to the reader


class Channel(Protocol):
    name: str

    def status(self) -> ChannelStatus: ...

    def send(self, recipient: str, subject: str, body: str, thread: ThreadRef | None = None) -> str: ...

    def poll(self, threads: Sequence[KnownThread]) -> list[Inbound]: ...


# ----------------------------------------------------------------- text hygiene

_QUOTE_START = re.compile(r"^(?:On .{5,200} wrote:|-{2,}\s*Original Message\s*-{2,}|_{5,}|Sent from my .+)\s*$", re.I)
_FROM_LINE = re.compile(r"^From:\s.+$", re.I)
_HEADER_LINE = re.compile(r"^(?:Sent|Date|To|Cc|Subject):\s", re.I)


def _starts_quoted_history(lines: list[str], i: int) -> bool:
    """A line that opens quoted history. "From: ..." counts only when mail-header lines follow it (Sent, To, Subject...): a vendor writing
    "From: INR 66,000 onwards" is giving a price, and cutting the reply there would lose it."""
    line = lines[i].strip()
    if _QUOTE_START.match(line):
        return True
    if _FROM_LINE.match(line):
        following = [x.strip() for x in lines[i + 1:i + 6] if x.strip()][:3]
        return any(_HEADER_LINE.match(x) for x in following)
    return False


def clean_reply_text(raw: str) -> tuple[str, bool]:
    """The vendor's new text only: quoted history and signatures' boilerplate cut, control characters removed, size capped."""
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = "".join(ch for ch in raw if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    kept: list[str] = []
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        if kept and any(k.strip() for k in kept) and _starts_quoted_history(lines, i):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return (text[:MAX_BODY_CHARS], True) if len(text) > MAX_BODY_CHARS else (text, False)


class _MailText(HTMLParser):
    """Text of an HTML mail, line breaks kept at paragraph ends (the quoted-history cut works on lines). Linear time on any markup; script and style
    content is dropped. The earlier regex version took about ten seconds on a body of unclosed <script> tags (red-team finding)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _strip_html(html: str) -> str:
    p = _MailText()
    try:
        p.feed(html)
        p.close()
    except Exception:  # hostile markup: keep what was read
        pass
    return "".join(p.parts)


_SAFE_MESSAGE_ID = re.compile(r"<[^\s\"\\<>()]{1,300}>")  # a Message-ID we made ourselves; anything stranger is not put into a search
_NESTED_PART = re.compile(rb"(?im)^content-type:[ \t]*(?:multipart|message)/")  # header lines only, so a URL in a body that contains "message/" does not count
MAX_QUOTE_FILES = 2
QUOTE_FILE_EXTENSIONS = frozenset({".pdf", ".docx", ".xlsx", ".csv", ".html", ".htm", ".txt", ".png", ".jpg", ".jpeg"})
MAX_MIME_NESTING = 24  # a real reply has a handful of parts; thousands of nested ones exist only to exhaust the parser's stack


# ----------------------------------------------------------------- email


@dataclass(frozen=True)
class EmailConfig:
    smtp_host: str = ""
    smtp_port: int = 465
    imap_host: str = ""
    imap_port: int = 993
    user: str = ""
    password: str = field(default="", repr=False)
    from_addr: str = ""
    spam_folder: str = ""  # optional: a folder to look in, read-only, for replies the provider filed as spam (EVENTMATE_IMAP_SPAM_FOLDER); empty means it is not looked at

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailConfig:
        def num(name: str, default: int) -> int:
            raw = env.get(name, "").strip()
            return int(raw) if raw.isascii() and raw.isdigit() and 0 < int(raw) < 65536 else default

        user = env.get("EVENTMATE_SMTP_USER", "").strip()
        return cls(
            smtp_host=env.get("EVENTMATE_SMTP_HOST", "").strip(), smtp_port=num("EVENTMATE_SMTP_PORT", 465),
            imap_host=env.get("EVENTMATE_IMAP_HOST", "").strip(), imap_port=num("EVENTMATE_IMAP_PORT", 993),
            user=user, password=env.get("EVENTMATE_SMTP_PASSWORD", ""), from_addr=env.get("EVENTMATE_MAIL_FROM", "").strip() or user,
            spam_folder=env.get("EVENTMATE_IMAP_SPAM_FOLDER", "").strip()[:100],
        )


def _imap_date(d: datetime) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


class EmailChannel:
    name = "email"
    last_poll: PollReport = PollReport()

    def __init__(self, config: EmailConfig, smtp_factory: Callable | None = None, imap_factory: Callable | None = None, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.config = config
        self._smtp = smtp_factory or self._default_smtp
        self._imap = imap_factory or self._default_imap
        self._now = now

    def status(self) -> ChannelStatus:
        c = self.config
        can_send = bool(c.smtp_host and c.user and c.password and c.from_addr)
        can_receive = bool(c.imap_host and c.user and c.password)
        missing = [n for n, ok in (("EVENTMATE_SMTP_HOST/USER/PASSWORD", can_send), ("EVENTMATE_IMAP_HOST", can_receive)) if not ok]
        return ChannelStatus(channel="email", can_send=can_send, can_receive=can_receive, detail="ready" if not missing else "not set: " + ", ".join(missing))

    def _default_smtp(self):
        c = self.config
        if c.smtp_port == 465:
            return smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=TIMEOUT_S, context=ssl.create_default_context())
        server = smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=TIMEOUT_S)
        server.starttls(context=ssl.create_default_context())  # never fall back to plain text
        return server

    def _default_imap(self):
        return imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_S)

    def send(self, recipient: str, subject: str, body: str, thread: ThreadRef | None = None) -> str:
        c = self.config
        if not self.status().can_send:
            raise ChannelError("email sending is not configured")
        if not isinstance(recipient, str) or not _EMAIL.fullmatch(recipient.strip()):
            raise ChannelError("the recipient must be one plain email address")  # the channel enforces it too: a list would deliver one message to several people
        recipient = recipient.strip()
        msg = EmailMessage(policy=email.policy.SMTP)
        try:
            msg["From"], msg["To"], msg["Subject"] = c.from_addr, recipient, subject
            msg["Date"] = email.utils.format_datetime(self._now())
            msg["Message-ID"] = email.utils.make_msgid(domain=c.from_addr.rsplit("@", 1)[-1] or None)
            if thread and thread.in_reply_to:
                msg["In-Reply-To"] = thread.in_reply_to
                msg["References"] = (thread.references + " " + thread.in_reply_to).strip()
            msg.set_content(body)
        except (ValueError, TypeError) as exc:  # header injection attempts end here
            raise ChannelError(f"the message could not be built: {exc}") from None
        server = None
        try:
            server = self._smtp()
            server.login(c.user, c.password)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            if server is not None:  # connected but not logged in: do not leave the connection open
                with contextlib.suppress(Exception):
                    server.quit()
            if isinstance(exc, smtplib.SMTPAuthenticationError):
                raise ChannelError("the mail server rejected the login") from None
            raise ChannelError("could not connect to the mail server") from None
        try:
            server.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
            raise ChannelError("the mail server refused the address") from None
        except (smtplib.SMTPException, OSError, TimeoutError):
            raise OutcomeUnknown("the connection failed while sending; check your Sent folder before trying again") from None
        finally:
            try:
                server.quit()
            except Exception:  # the message is already accepted or failed; closing is best effort
                pass
        return msg["Message-ID"]

    def poll(self, threads: Sequence[KnownThread]) -> list[Inbound]:
        """Replies to the threads we know, found by asking the mail server for them (``FROM`` the people we wrote to, ``In-Reply-To`` / ``References`` our message ids, and delivery failure notices),
        not by reading the newest few. Up to ``MAX_THREAD_MESSAGES`` of them newest first; what does not fit is counted in ``last_poll.overflow``. A few of the newest other messages are read as
        well, so a stop request or a failure notice that names nothing we sent still reaches the user. The mailbox is only ever opened read-only."""
        self.last_poll = PollReport()
        if not threads:
            return []
        if not self.status().can_receive:
            raise ChannelError("email reading is not configured")
        since = min(t.sent_at for t in threads) - timedelta(days=1)
        out: list[Inbound] = []
        try:
            box = self._imap()
            try:
                box.login(self.config.user, self.config.password)
                box.select("INBOX", readonly=True)
                mine, window, mode = self._search(box, since, threads)
                take = mine[-MAX_THREAD_MESSAGES:] + [n for n in window if n not in set(mine)][-MAX_OTHER_MESSAGES:]
                out.extend(self._fetch_all(box, take, "INBOX"))
                spam, spam_error = self._spam(box, since, threads)
                out.extend(spam)
                self.last_poll = PollReport(examined=len(mine), fetched=len(take), overflow=max(0, len(mine) - MAX_THREAD_MESSAGES), spam_found=len(spam), spam_error=spam_error, mode=mode)
            finally:
                with contextlib.suppress(Exception):
                    box.logout()
        except imaplib.IMAP4.error:
            raise ChannelError("the mail server rejected the login or the request") from None
        except (OSError, ssl.SSLError):
            raise ChannelError("could not reach the mail server") from None
        return out

    def _search(self, box, since: datetime, threads: Sequence[KnownThread]) -> tuple[list[int], list[int], str]:
        """(numbers of the messages tied to threads we know, numbers of the whole window since the oldest open thread, how they were found). Numbers, oldest first."""
        day = _imap_date(since)

        def run(*criteria: str) -> set[int]:
            typ, data = box.search(None, "SINCE", day, *criteria)
            return {int(x) for x in (data[0] or b"").split()} if typ == "OK" and data else set()

        window = sorted(run())
        try:
            tied: set[int] = set()
            for addr in sorted({t.recipient.strip().lower() for t in threads if _EMAIL.fullmatch(t.recipient.strip())}):
                tied |= run("FROM", f'"{addr}"')
            for t in threads:
                if _SAFE_MESSAGE_ID.fullmatch(t.external_id or ""):
                    tied |= run("HEADER", "In-Reply-To", f'"{t.external_id}"') | run("HEADER", "References", f'"{t.external_id}"')
            tied |= run("FROM", '"mailer-daemon"') | run("FROM", '"postmaster"')  # delivery failure notices
        except imaplib.IMAP4.error:  # a server that cannot search this way: read the newest of the window, as before, and say so
            return window[-MAX_POLL_MESSAGES:], [], "window"
        return sorted(tied), window, "threads"

    def _fetch_all(self, box, numbers: Sequence[int], folder: str) -> list[Inbound]:
        out = []
        for num in numbers:
            typ, parts = box.fetch(str(num).encode(), f"(BODY.PEEK[]<0.{MAX_FETCH_BYTES}>)")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None) if typ == "OK" else None
            item = self._parse(raw) if raw else None
            if item:
                out.append(item if folder == "INBOX" else dataclasses.replace(item, folder=folder, files=()))
        return out

    def _spam(self, box, since: datetime, threads: Sequence[KnownThread]) -> tuple[list[Inbound], str]:
        """Replies from the people we wrote to that the provider filed as spam, read-only and only if a folder is named. They are for the user to look at: nothing in them is filed, read as a quote or obeyed."""
        folder = self.config.spam_folder
        if not folder:
            return [], ""
        try:
            typ, _ = box.select(f'"{folder}"' if " " in folder else folder, readonly=True)
            if typ != "OK":
                return [], "the spam folder could not be opened"
            day, found = _imap_date(since), set()
            for addr in sorted({t.recipient.strip().lower() for t in threads if _EMAIL.fullmatch(t.recipient.strip())}):
                typ, data = box.search(None, "SINCE", day, "FROM", f'"{addr}"')
                found |= {int(x) for x in (data[0] or b"").split()} if typ == "OK" and data else set()
            return self._fetch_all(box, sorted(found)[-MAX_OTHER_MESSAGES:], folder), ""
        except imaplib.IMAP4.error:
            return [], "the spam folder could not be read"

    def _when(self, msg) -> datetime:
        """When a message arrived. The Date header is the sender's own claim: missing, unreadable or out of range it becomes now, and a reply is never dated later than now."""
        now = self._now()
        try:
            when = email.utils.parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else now
            when = (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC)
        except (ValueError, TypeError, OverflowError, IndexError):
            return now
        return min(when, now)

    def _headers_only(self, raw: bytes, why: str) -> Inbound | None:
        """A message whose body cannot be read safely still reaches the person as "could not be read", so a reply, a stop request or a bounce is never
        lost and never stops the poll. Only the header block is parsed: no MIME tree, so nothing to recurse into."""
        try:
            head = re.split(rb"\r?\n\r?\n", raw[:16384], maxsplit=1)[0]
            msg = email.message_from_bytes(head + b"\r\n\r\n", policy=email.policy.default)
            sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
            if not sender or sender == self.config.from_addr.lower():
                return None
            mid = str(msg.get("Message-ID", "")).strip()
            return Inbound(
                channel="email", sender=sender[:254], external_id=(mid or "nomsgid-" + hashlib.sha256(raw).hexdigest()[:32])[:400], received_at=self._when(msg),
                body=f"[This message could not be read safely ({why}). Open your mailbox to read it.]", subject=str(msg.get("Subject", ""))[:200],
                in_reply_to=str(msg.get("In-Reply-To", "")).strip()[:400], references=str(msg.get("References", ""))[:2000], attachments=0, truncated=False,
            )
        except Exception:  # not even the headers are usable: nothing safe to show
            return None

    def _parse(self, raw: bytes) -> Inbound | None:
        if len(_NESTED_PART.findall(raw[:MAX_FETCH_BYTES])) > MAX_MIME_NESTING:
            return self._headers_only(raw, "unusually many nested parts")
        try:
            return self._parse_full(raw)
        except (ValueError, TypeError, KeyError, LookupError, ArithmeticError):
            return None  # one malformed message must not stop the others
        except Exception as exc:  # RecursionError and anything else the parser did not expect: never abort the poll
            return self._headers_only(raw, type(exc).__name__)

    def _parse_full(self, raw: bytes) -> Inbound | None:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        sender = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
        if not sender or sender == self.config.from_addr.lower():
            return None
        bounce = _dsn(msg)
        if bounce is not None:
            mid = str(msg.get("Message-ID", "")).strip()
            return Inbound(
                channel="email", sender=sender[:254], external_id=(mid or "nomsgid-" + hashlib.sha256(raw).hexdigest()[:32])[:400], received_at=self._when(msg),
                body=f"Delivery failure notice ({'permanent' if bounce.permanent else 'temporary'}) for {bounce.recipient[:120]}", subject=str(msg.get("Subject", ""))[:200], bounce=bounce,
            )
        if str(msg.get("Auto-Submitted", "no")).lower() != "no" or str(msg.get("Precedence", "")).lower() in ("bulk", "junk", "list") or msg.get("List-Id"):
            return None  # robots and mailing lists are not vendor replies
        part = msg.get_body(preferencelist=("plain",))
        text = part.get_content() if part is not None else ""
        if not text:
            html = msg.get_body(preferencelist=("html",))
            text = _strip_html(html.get_content()) if html is not None else ""
        body, truncated = clean_reply_text(str(text))
        mid = str(msg.get("Message-ID", "")).strip()
        attachments = sum(1 for _ in msg.iter_attachments())
        return Inbound(
            channel="email", sender=sender[:254], external_id=(mid or "nomsgid-" + hashlib.sha256(raw).hexdigest()[:32])[:400], received_at=self._when(msg), body=body,
            subject=str(msg.get("Subject", ""))[:200], in_reply_to=str(msg.get("In-Reply-To", "")).strip()[:400], references=str(msg.get("References", ""))[:2000],
            attachments=min(attachments, 100), truncated=truncated, files=_quote_files(msg, raw),
        )


def _quote_files(msg, raw: bytes) -> tuple[tuple[str, bytes], ...]:
    """The attachments a quote might be read from. Only when the whole message was fetched: past the fetch limit a part is cut off and its bytes are not the file."""
    if len(raw) >= MAX_FETCH_BYTES:
        return ()
    out = []
    try:
        for part in msg.iter_attachments():
            name = os.path.basename(str(part.get_filename() or ""))[:100]
            if os.path.splitext(name)[1].lower() in QUOTE_FILE_EXTENSIONS and not part.is_multipart():
                data = part.get_payload(decode=True)
                if data:
                    out.append((name, bytes(data)))
            if len(out) >= MAX_QUOTE_FILES:
                break
    except Exception:  # an attachment that cannot be decoded is simply not offered
        return ()
    return tuple(out)


def _dsn(msg) -> Bounce | None:
    """A structured delivery-status report, or None. A plain message that merely looks like a bounce is not one."""
    try:
        if msg.get_content_type() != "multipart/report" or (msg.get_param("report-type") or "").lower() != "delivery-status":
            return None
        status_part, original = None, ""
        for part in msg.iter_parts():
            ct = part.get_content_type()
            if ct == "message/delivery-status":
                status_part = part
            elif ct in ("message/rfc822", "text/rfc822-headers"):
                inner = part.get_payload(0) if ct == "message/rfc822" else email.message_from_string(str(part.get_content()), policy=email.policy.default)
                original = str(inner.get("Message-ID", "")).strip()[:400]
        blocks = status_part.get_payload() if status_part is not None else []
        if len(blocks) < 2:
            return None
        per_recipient = blocks[1]  # the first block describes the message, the next ones each recipient
        recipient = str(per_recipient.get("Final-Recipient") or per_recipient.get("Original-Recipient") or "").split(";", 1)[-1].strip().strip("<>")[:254]
        action, status = str(per_recipient.get("Action", "")).strip().lower(), str(per_recipient.get("Status", "")).strip()
        diagnostic = " ".join("".join(c for c in str(per_recipient.get("Diagnostic-Code", "")) if c.isprintable() or c == " ").split())[:200]
        return Bounce(permanent=action == "failed" and status.startswith("5."), recipient=recipient, original_message_id=original, status=status[:12] if re.fullmatch(r"[245]\.\d{1,3}\.\d{1,3}", status) else "", diagnostic=diagnostic)
    except Exception:  # noqa: BLE001 - a status report that cannot be read is not a bounce; deep nesting must not escape (F-008)
        return None


# ----------------------------------------------------------------- WhatsApp through WAHA


@dataclass(frozen=True)
class WahaConfig:
    url: str = ""
    api_key: str = field(default="", repr=False)
    session: str = "default"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> WahaConfig:
        session = env.get("EVENTMATE_WAHA_SESSION", "").strip()
        return cls(url=env.get("EVENTMATE_WAHA_URL", "").strip().rstrip("/"), api_key=env.get("EVENTMATE_WAHA_API_KEY", ""), session=session if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", session) else "default")


HttpFn = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    parts = urlsplit(url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.hostname, parts.port, timeout=TIMEOUT_S)  # no redirects are followed
    try:
        conn.request(method, parts.path + (f"?{parts.query}" if parts.query else ""), body=body, headers=headers)
        resp = conn.getresponse()
        return resp.status, read_capped(resp, 1_000_000, total_s=30)
    finally:
        conn.close()


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, config: WahaConfig, http_fn: HttpFn = _http, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.config = config
        self._http = http_fn
        self._now = now
        self._chat_ids: dict[str, str] = {}

    def _valid_url(self) -> bool:
        p = urlsplit(self.config.url)
        return p.scheme in ("http", "https") and bool(p.hostname) and not p.username and not p.password

    def status(self) -> ChannelStatus:
        ok = self._valid_url()
        return ChannelStatus(channel="whatsapp", can_send=ok, can_receive=ok, detail="ready" if ok else "not set: EVENTMATE_WAHA_URL (a self-hosted WAHA gateway; see docs)")

    def _call(self, method: str, path: str, query: dict[str, str] | None = None, payload: dict | None = None):
        if not self._valid_url():
            raise ChannelError("WhatsApp gateway is not configured")
        import json

        url = self.config.url + path + (("?" + urlencode(query)) if query else "")
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["X-Api-Key"] = self.config.api_key
        body = None
        if payload is not None:
            body, headers["Content-Type"] = json.dumps(payload).encode("utf-8"), "application/json"
        try:
            status, raw = self._http(method, url, headers, body)
        except NETWORK_ERRORS:
            raise OutcomeUnknown("could not reach the WhatsApp gateway") from None
        if status in (401, 403):
            raise ChannelError("the WhatsApp gateway rejected the API key")
        if status >= 400:
            raise ChannelError(f"the WhatsApp gateway answered HTTP {status}")
        try:
            return loads(raw) if raw else None
        except ValueError:
            raise ChannelError("the WhatsApp gateway sent something that is not JSON") from None

    def _chat_id(self, digits: str) -> str:
        if digits not in self._chat_ids:
            res = self._call("GET", "/api/checkNumberStatus", {"session": self.config.session, "phone": digits})
            if not isinstance(res, dict) or res.get("numberExists") is not True:
                raise ChannelError("that number is not on WhatsApp")
            chat = res.get("chatId")
            self._chat_ids[digits] = chat if isinstance(chat, str) and re.fullmatch(r"[0-9A-Za-z.:_-]{3,60}@(?:c\.us|lid|s\.whatsapp\.net)", chat) else f"{digits}@c.us"
        return self._chat_ids[digits]

    def send(self, recipient: str, subject: str, body: str, thread: ThreadRef | None = None) -> str:
        chat = self._chat_id(recipient)  # refuses numbers that are not on WhatsApp before anything is sent
        res = self._call("POST", "/api/sendText", payload={"session": self.config.session, "chatId": chat, "text": body, "linkPreview": False})
        mid = res.get("id") if isinstance(res, dict) else None
        if isinstance(mid, dict):
            mid = mid.get("_serialized")
        if not isinstance(mid, str) or not mid:
            raise OutcomeUnknown("the gateway accepted the request but gave no message id")
        return mid[:400]

    def poll(self, threads: Sequence[KnownThread]) -> list[Inbound]:
        out: list[Inbound] = []
        for t in threads:
            chat = self._chat_id(t.recipient)
            res = self._call("GET", f"/api/{quote(self.config.session, safe='')}/chats/{quote(chat, safe='@')}/messages", {"limit": "20", "downloadMedia": "false"})
            if not isinstance(res, list):
                raise ChannelError("the WhatsApp gateway sent an unexpected message list")
            for m in res[:MAX_POLL_MESSAGES]:
                if not isinstance(m, dict) or m.get("fromMe") is not False:
                    continue
                ts, mid, text = m.get("timestamp"), m.get("id"), m.get("body")
                if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not isinstance(mid, str) or not isinstance(text, str):
                    continue
                try:
                    when = min(datetime.fromtimestamp(ts, tz=UTC), self._now())  # never later than now
                except (ValueError, OverflowError, OSError):
                    continue  # not a date: skip this message, keep the rest
                if when < t.sent_at - timedelta(seconds=60):
                    continue
                body, truncated = clean_reply_text(text)
                out.append(Inbound(channel="whatsapp", sender=t.recipient, external_id=mid[:400], received_at=when, body=body, attachments=1 if m.get("hasMedia") else 0, truncated=truncated))
        return out


def channels_from_env(env: Mapping[str, str]) -> dict[str, Channel]:
    return {"email": EmailChannel(EmailConfig.from_env(env)), "whatsapp": WhatsAppChannel(WahaConfig.from_env(env))}
