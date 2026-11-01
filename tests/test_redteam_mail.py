"""Red-team findings in the inbound mail parser: one hostile message must not abort the poll or stall it, and must not vanish."""

import time

import pytest

from eventmate.messaging import channels as ch

HEAD = b"From: vendor@example.net\r\nTo: owner@example.com\r\nSubject: Re: quote\r\nMessage-ID: <a@b>\r\nDate: Sat, 01 Nov 2026 10:00:00 +0000\r\n"


@pytest.fixture
def chan():
    return ch.EmailChannel(ch.EmailConfig(smtp_host="s", imap_host="i", user="u", password="p", from_addr="owner@example.com"))


def nested_multipart(depth):
    return HEAD + b"MIME-Version: 1.0\r\n" + b"".join(f"Content-Type: multipart/mixed; boundary=b{i}\r\n\r\n--b{i}\r\n".encode() for i in range(depth)) + b"Content-Type: text/plain\r\n\r\nSTOP\r\n"


@pytest.mark.parametrize("depth", [30, 3000, 8000])
def test_deeply_nested_mime_becomes_a_could_not_be_read_notice_not_a_crash(chan, depth):
    item = chan._parse(nested_multipart(depth)[: ch.MAX_FETCH_BYTES])
    assert item is not None and item.sender == "vendor@example.net" and "could not be read safely" in item.body and item.external_id == "<a@b>"


def test_nested_message_rfc822_is_handled_the_same_way(chan):
    raw = HEAD + b"MIME-Version: 1.0\r\n" + b"Content-Type: message/rfc822\r\n\r\n" * 8000
    assert "could not be read safely" in chan._parse(raw[: ch.MAX_FETCH_BYTES]).body


def test_a_reply_with_a_few_parts_and_urls_that_contain_message_is_parsed_normally(chan):
    links = b" ".join(b"https://x.example/message/%d" % i for i in range(100))
    raw = HEAD + b"Content-Type: text/plain\r\n\r\nPrice is 50000 " + links
    item = chan._parse(raw)
    assert item is not None and "could not be read" not in item.body and "50000" in item.body


def test_one_poison_message_does_not_stop_the_messages_after_it(chan, monkeypatch):
    class Box:
        def login(self, *a): pass
        def select(self, *a, **k): pass
        def search(self, *a): return "OK", [b"1 2"]
        def fetch(self, num, spec): return "OK", [(b"x", nested_multipart(5000)[: ch.MAX_FETCH_BYTES] if num == b"1" else HEAD + b"\r\nPrice 50000")]
        def logout(self): pass

    chan._imap = lambda: Box()
    from datetime import datetime, timezone
    out = chan.poll([ch.KnownThread(draft_id="d", recipient="vendor@example.net", sent_at=datetime(2026, 10, 30, tzinfo=timezone.utc), external_id="<x@y>")])
    assert len(out) == 2 and "could not be read safely" in out[0].body and "50000" in out[1].body


@pytest.mark.parametrize("body", [b"<script>" * 20000, b"<" * 200000, b"<style><script>" * 10000, b"<div>" * 40000])
def test_hostile_html_is_stripped_in_linear_time(chan, body):
    started = time.perf_counter()
    item = chan._parse(HEAD + b"Content-Type: text/html\r\n\r\n" + body[: ch.MAX_FETCH_BYTES - 400])
    assert time.perf_counter() - started < 2.0 and item is not None


def test_html_text_keeps_paragraph_breaks_and_drops_scripts():
    assert ch._strip_html("<p>Price 50000</p><script>evil()</script><div>second&amp;third</div>a<br>b").split() == ["Price", "50000", "second&third", "a", "b"]
    assert ch._strip_html("<p>one</p><p>two</p>").count("\n") == 2
