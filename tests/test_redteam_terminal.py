"""Red-team finding: the CLI printed inbound email and web text raw, so a sender could use escape sequences against the operator's terminal."""

from datetime import datetime, UTC
from types import SimpleNamespace


from eventmate.__main__ import main
from eventmate.service import EventMate
from eventmate.untrusted import strip_terminal

HOSTILE = "Price is 50000\x1b[2K\x1b[1A\x1b[2KALL PAID, nothing to approve\x1b]0;owned\x07\x1b]52;c;ZXZpbA==\x07 \u202egnp.exe\x9b31m end\r\nSECOND LINE"


def test_strip_terminal_removes_escape_sequences_controls_and_bidi_and_keeps_one_line():
    out = strip_terminal(HOSTILE)
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out and "\u202e" not in out and "\n" not in out and "\r" not in out
    assert "owned" not in out and "ZXZpbA" not in out  # the payload of an OSC sequence goes with it
    assert out.startswith("Price is 50000") and "end" in out and "SECOND LINE" in out
    assert strip_terminal("plain text, ₹1,20,000 and हिन्दी") == "plain text, ₹1,20,000 and हिन्दी"
    assert strip_terminal(None) == "None" and strip_terminal("\x1b") == "" and strip_terminal("\x1b[") == ""


def test_the_inbox_command_prints_a_hostile_reply_as_inert_text(tmp_path, monkeypatch, capsys):
    reply = SimpleNamespace(read=False, received_at=datetime(2026, 11, 1, tzinfo=UTC), body=HOSTILE, analysis=SimpleNamespace(amounts=[]))
    thread = SimpleNamespace(request=SimpleNamespace(vendor_name="Royal\x1b[31m Rasoi", channel="email", status="sent"), drafts=[], replies=[reply])
    box = SimpleNamespace(channels=[], unread_total=1, approvals_total=0, threads=[thread])
    monkeypatch.setattr(EventMate, "inbox", lambda self, plan_id: box)
    assert main(["--plan-dir", str(tmp_path / "p"), "--log-dir", str(tmp_path / "l"), "inbox", "show", "anyplan"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "\u202e" not in out and "Royal" in out and "Price is 50000" in out
