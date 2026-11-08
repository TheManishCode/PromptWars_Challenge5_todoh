"""Signing in to the mailbox by OAuth 2.0 (SASL XOAUTH2) instead of a password (audit A6). No network: the token service is a fake, and the SMTP and IMAP servers are small real
socket servers on localhost that speak the exchange Google documents (https://developers.google.com/workspace/gmail/imap/xoauth2-protocol), so the standard libraries' own
AUTH and AUTHENTICATE code is what runs."""
import base64
import imaplib
import json
import smtplib
import socketserver
import threading

import pytest

from eventmate.messaging.channels import ChannelError, EmailChannel, EmailConfig, KnownThread
from eventmate.messaging.mail_oauth import OAuth2TokenSource, OAuthConfig, TokenError, xoauth2_string
from eventmate.messaging import smoke_email
from tests.test_channels import NOW

SECRETS = ("client-id-123.apps.example", "client-secret-XYZ", "refresh-token-ABC", "access-token-QRS")
CID, CSEC, RTOK, ATOK = SECRETS
USER = "agent@example.test"
OAUTH = OAuthConfig(CID, CSEC, RTOK)


def cfg(**kw):
    base = {"smtp_host": "127.0.0.1", "imap_host": "127.0.0.1", "user": USER, "from_addr": USER, "auth": "oauth2", "oauth": OAUTH}
    base.update(kw)
    return EmailConfig(**base)


def fetch_ok(calls=None, expires_in=3599):
    def fetch(form):
        (calls if calls is not None else []).append(dict(form))
        return 200, {"access_token": ATOK, "expires_in": expires_in, "token_type": "Bearer"}
    return fetch


# ---- the string and the token

def test_the_initial_client_response_is_exactly_the_one_in_googles_documentation():
    s = xoauth2_string("someuser@example.com", "ya29.vF9dft4qmTc2Nvb3RlckBhdHRhdmlzdGEuY29tCg")
    assert s == "user=someuser@example.com\x01auth=Bearer ya29.vF9dft4qmTc2Nvb3RlckBhdHRhdmlzdGEuY29tCg\x01\x01"
    assert base64.b64encode(s.encode()).decode() == "dXNlcj1zb21ldXNlckBleGFtcGxlLmNvbQFhdXRoPUJlYXJlciB5YTI5LnZGOWRmdDRxbVRjMk52YjNSbGNrQmhkSFJoZG1semRHRXVZMjl0Q2cBAQ=="


def test_the_token_is_fetched_with_the_refresh_grant_cached_and_renewed_before_it_runs_out():
    now, calls = [1000.0], []
    src = OAuth2TokenSource(OAUTH, fetch=fetch_ok(calls, 3600), now=lambda: now[0])
    assert src.token() == ATOK and src.token() == ATOK and len(calls) == 1
    assert calls[0] == {"client_id": CID, "client_secret": CSEC, "refresh_token": RTOK, "grant_type": "refresh_token"}
    now[0] += 3600 - 60  # inside the two-minute margin: renewed
    assert src.token() == ATOK and len(calls) == 2


@pytest.mark.parametrize("status,doc,words", [
    (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, "refresh token"),
    (401, {"error": "invalid_client"}, "client id or secret"),
    (500, {}, "HTTP 500"),
    (200, {"token_type": "Bearer"}, "HTTP 200"),
])
def test_a_token_that_cannot_be_had_says_what_to_do_and_leaks_nothing(status, doc, words):
    with pytest.raises(TokenError, match=words) as e:
        OAuth2TokenSource(OAUTH, fetch=lambda form: (status, doc)).token()
    assert not any(secret in str(e.value) for secret in SECRETS) and "Token has been" not in str(e.value), "the provider's own words are not passed on"


def test_a_network_failure_is_a_token_error_and_an_incomplete_setting_names_what_is_missing():
    def down(form):
        raise OSError("no route")

    with pytest.raises(TokenError, match="could not reach Google"):
        OAuth2TokenSource(OAUTH, fetch=down).token()
    with pytest.raises(TokenError, match="EVENTMATE_GMAIL_REFRESH_TOKEN"):
        OAuth2TokenSource(OAuthConfig(CID, CSEC, ""), fetch=fetch_ok()).token()


def test_no_secret_appears_in_a_repr_or_the_status():
    c = cfg()
    assert not any(s in repr(c) or s in repr(c.oauth) for s in SECRETS)
    st = EmailChannel(c).status()
    assert st.can_send and st.can_receive and not any(s in st.model_dump_json() for s in SECRETS)


# ---- configuration

def test_the_sign_in_route_comes_from_the_environment_and_a_typo_never_switches_it():
    env = {"EVENTMATE_SMTP_HOST": "h", "EVENTMATE_SMTP_USER": USER, "EVENTMATE_GMAIL_CLIENT_ID": CID, "EVENTMATE_GMAIL_CLIENT_SECRET": CSEC, "EVENTMATE_GMAIL_REFRESH_TOKEN": RTOK}
    assert EmailConfig.from_env(env).auth == "password"
    assert EmailConfig.from_env({**env, "EVENTMATE_MAIL_AUTH": "OAuth2"}).auth == "oauth2"
    assert EmailConfig.from_env({**env, "EVENTMATE_MAIL_AUTH": "oauth"}).auth == "password" and EmailConfig.from_env({**env, "EVENTMATE_MAIL_AUTH": ""}).auth == "password"
    assert EmailConfig.from_env({**env, "EVENTMATE_MAIL_AUTH": "oauth2"}).oauth == OAUTH


def test_oauth_needs_all_three_settings_and_says_which_are_missing():
    st = EmailChannel(cfg(oauth=OAuthConfig(CID, "", ""))).status()
    assert not st.can_send and "EVENTMATE_GMAIL_CLIENT_SECRET" in st.detail and "EVENTMATE_GMAIL_REFRESH_TOKEN" in st.detail and "PASSWORD" not in st.detail
    assert EmailChannel(cfg(auth="password", password="", oauth=OAUTH)).status().can_send is False, "the password route does not borrow the OAuth settings"


# ---- a small SMTP server that speaks AUTH XOAUTH2 as the documentation shows

class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self):
        s = self.server
        self.reply("220 test ESMTP")
        while True:
            line = self.rfile.readline().decode().rstrip("\r\n")
            if not line:
                return
            s.seen.append(line.split(" ")[0].upper())
            cmd = line.split(" ")
            if cmd[0].upper() == "EHLO":
                self.reply("250-test at your service")
                self.reply("250-AUTH LOGIN PLAIN XOAUTH2")
                self.reply("250 8BITMIME")
            elif cmd[0].upper() == "AUTH":
                assert cmd[1] == "XOAUTH2" and len(cmd) == 3, "the initial response goes on the AUTH line"
                s.received = base64.b64decode(cmd[2]).decode()
                if s.accept:
                    self.reply("235 2.7.0 Accepted")
                else:
                    err = base64.b64encode(json.dumps({"status": "401", "schemes": "bearer", "scope": "https://mail.google.com/"}).encode()).decode()
                    self.reply("334 " + err)
                    s.answer_to_error = self.rfile.readline().decode().rstrip("\r\n")  # the client must send an empty line
                    self.reply("535-5.7.1 Username and Password not accepted.")
                    self.reply("535 5.7.1 https://support.google.com/mail/?p=BadCredentials")
            elif cmd[0].upper() == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, accept=True):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.accept, self.seen, self.received, self.answer_to_error = accept, [], None, None
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


@pytest.fixture
def smtp_server():
    srv = _Server()
    yield srv
    srv.stop()


def channel_for(port, fetch=None, **kw):
    ch = EmailChannel(cfg(smtp_port=port, **kw), smtp_factory=lambda: smtplib.SMTP("127.0.0.1", port, timeout=5), now=lambda: NOW)
    ch.tokens = OAuth2TokenSource(OAUTH, fetch=fetch or fetch_ok())
    return ch


def test_smtp_signs_in_with_xoauth2_exactly_as_documented(smtp_server):
    ch = channel_for(smtp_server.server_address[1])
    server = ch._smtp()
    try:
        ch.login_smtp(server)
    finally:
        server.quit()
    assert smtp_server.received == f"user={USER}\x01auth=Bearer {ATOK}\x01\x01" and "AUTH" in smtp_server.seen


def test_smtp_answers_an_error_challenge_with_an_empty_line_then_fails_cleanly():
    srv = _Server(accept=False)
    try:
        ch = channel_for(srv.server_address[1])
        server = ch._smtp()
        with pytest.raises(smtplib.SMTPAuthenticationError):
            ch.login_smtp(server)
        assert srv.answer_to_error == ""
    finally:
        srv.stop()


def test_a_send_with_a_rejected_token_is_a_clean_failure_that_names_no_secret():
    srv = _Server(accept=False)
    try:
        ch = channel_for(srv.server_address[1])
        with pytest.raises(ChannelError, match="rejected the login") as e:
            ch.send("sales@v.test", "Quote", "Hello")
        assert not isinstance(e.value, type(None)) and not any(s in str(e.value) for s in SECRETS)
    finally:
        srv.stop()


def test_a_send_when_no_token_can_be_had_sends_nothing_and_says_why(smtp_server):
    def revoked(form):
        return 400, {"error": "invalid_grant"}

    ch = channel_for(smtp_server.server_address[1], fetch=revoked)
    with pytest.raises(ChannelError, match="refresh token") as e:
        ch.send("sales@v.test", "Quote", "Hello")
    assert type(e.value) is ChannelError, "definitely not sent (never the unknown-outcome kind)"
    assert smtp_server.seen == [] or "DATA" not in smtp_server.seen and "MAIL" not in smtp_server.seen


# ---- IMAP: the same string through imaplib's AUTHENTICATE

class _ImapHandler(socketserver.StreamRequestHandler):
    def handle(self):
        s = self.server
        self.wfile.write(b"* OK IMAP4rev1 ready\r\n")
        while True:
            line = self.rfile.readline().decode().rstrip("\r\n")
            if not line:
                return
            tag, _, rest = line.partition(" ")
            word = rest.split(" ")[0].upper()
            if word == "CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1 AUTH=XOAUTH2\r\n" + tag.encode() + b" OK Completed\r\n")
            elif word == "AUTHENTICATE":
                assert rest.split(" ")[1] == "XOAUTH2"
                self.wfile.write(b"+ \r\n")
                s.received = base64.b64decode(self.rfile.readline().strip()).decode()
                if s.accept:
                    self.wfile.write(tag.encode() + b" OK Success\r\n")
                else:
                    self.wfile.write(b"+ " + base64.b64encode(json.dumps({"status": "401", "schemes": "bearer", "scope": "https://mail.google.com/"}).encode()) + b"\r\n")
                    s.answer_to_error = self.rfile.readline().decode().rstrip("\r\n")
                    self.wfile.write(tag.encode() + b" NO SASL authentication failed\r\n")
            elif word == "LOGOUT":
                self.wfile.write(b"* BYE\r\n" + tag.encode() + b" OK done\r\n")
                return
            else:
                self.wfile.write(tag.encode() + b" OK\r\n")


class _ImapServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, accept=True):
        super().__init__(("127.0.0.1", 0), _ImapHandler)
        self.accept, self.received, self.answer_to_error = accept, None, None
        threading.Thread(target=self.serve_forever, daemon=True).start()


def test_imap_signs_in_with_xoauth2_and_a_failure_is_answered_with_an_empty_line():
    for accept in (True, False):
        srv = _ImapServer(accept)
        try:
            port = srv.server_address[1]
            ch = EmailChannel(cfg(imap_port=port), imap_factory=lambda port=port: imaplib.IMAP4("127.0.0.1", port, timeout=5), now=lambda: NOW)
            ch.tokens = OAuth2TokenSource(OAUTH, fetch=fetch_ok())
            box = ch._imap()
            if accept:
                ch.login_imap(box)
            else:
                with pytest.raises(imaplib.IMAP4.error):
                    ch.login_imap(box)
                assert srv.answer_to_error == ""
            assert srv.received == f"user={USER}\x01auth=Bearer {ATOK}\x01\x01"
        finally:
            srv.shutdown()
            srv.server_close()


def test_a_poll_when_the_refresh_token_is_revoked_is_a_visible_channel_error():
    ch = EmailChannel(cfg(), imap_factory=lambda: None, now=lambda: NOW)
    ch.tokens = OAuth2TokenSource(OAUTH, fetch=lambda form: (400, {"error": "invalid_grant"}))
    class Box:
        def authenticate(self, *a):
            raise AssertionError("no token, so no sign-in is attempted")
    ch._imap = lambda: Box()
    with pytest.raises(ChannelError, match="refresh token"):
        ch.poll([KnownThread("d1", "sales@v.test", NOW, "<a@x>")])


# ---- the password route is untouched, and the smoke check follows the chosen route without sending

def test_the_password_route_still_logs_in_with_the_password():
    class Smtp:
        def __init__(self):
            self.calls = []

        def login(self, u, p):
            self.calls.append(("login", u, p))

        def auth(self, *a, **k):
            raise AssertionError("not for the password route")

    smtp = Smtp()
    ch = EmailChannel(EmailConfig(smtp_host="h", imap_host="h", user=USER, password="pw-123", from_addr=USER))
    assert ch.tokens is None
    ch.login_smtp(smtp)
    assert smtp.calls == [("login", USER, "pw-123")]


class _SmokeSmtp:
    def __init__(self):
        self.mechanism = None

    def auth(self, mechanism, authobject, initial_response_ok=True):
        self.mechanism = mechanism
        assert authobject() == xoauth2_string(USER, ATOK) and authobject("challenge") == ""

    def noop(self):
        return (250, b"ok")

    def quit(self):
        pass


class _SmokeImap:
    def __init__(self):
        self.selected = None

    def authenticate(self, mechanism, authobject):
        assert mechanism == "XOAUTH2" and authobject(b"") == xoauth2_string(USER, ATOK).encode() and authobject(b"x") == b""

    def select(self, box, readonly=False):
        assert readonly
        self.selected = box
        return "OK", [b"1"]

    def logout(self):
        pass


def smoke(fetch, capsys, extra=()):
    env = {"EVENTMATE_SMTP_HOST": "smtp.example.test", "EVENTMATE_SMTP_USER": USER, "EVENTMATE_IMAP_HOST": "imap.example.test", "EVENTMATE_MAIL_AUTH": "oauth2",
           "EVENTMATE_GMAIL_CLIENT_ID": CID, "EVENTMATE_GMAIL_CLIENT_SECRET": CSEC, "EVENTMATE_GMAIL_REFRESH_TOKEN": RTOK}
    sent = []
    smtp = _SmokeSmtp()
    code = smoke_email.main(list(extra), env=env, smtp_factory=lambda: smtp, imap_factory=_SmokeImap, token_fetch=fetch)
    return code, capsys.readouterr(), smtp, sent


def test_the_smoke_check_signs_in_by_oauth_prints_no_secret_and_sends_nothing(capsys):
    code, out, smtp, _ = smoke(fetch_ok(), capsys)
    assert code == 0 and smtp.mechanism == "XOAUTH2" and "logged in by OAuth" in out.out and "nothing was sent" in out.out
    assert not any(s in out.out + out.err for s in SECRETS)


def test_the_smoke_check_names_a_revoked_token_without_the_providers_words_or_any_secret(capsys):
    code, out, smtp, _ = smoke(lambda form: (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}), capsys)
    assert code == 1 and "FAIL smtp" in out.out and "FAIL imap" in out.out and smtp.mechanism is None
    assert not any(s in out.out + out.err for s in SECRETS) and "Token has been" not in out.out
