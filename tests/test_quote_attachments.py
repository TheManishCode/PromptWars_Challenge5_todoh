"""Quote files attached to a vendor's reply: read only when the user turned it on, only from the address we wrote to, only in the sandbox, and only into unreviewed evidence."""

from datetime import timedelta
from email.message import EmailMessage

import pytest

from eventmate.evidence.ledger import FactState
from eventmate.messaging import channels as ch
from eventmate.messaging.channels import Inbound
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY
from tests.test_autoreply import sent_request
from tests.test_conversations import Clock, FakeChannel
from tests import test_redteam_mail

CSV = b"Item,Qty,Rate,Total\nVeg buffet,120,450,54000\n"


def mail(*attachments, body="Our quote is attached."):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "vendor@example.net", "me@example.com", "Re: quote", "<q@x>"
    m.set_content(body)
    for name, data in attachments:
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return m.as_bytes()


chan = test_redteam_mail.chan  # the IMAP channel fixture


def test_a_small_quote_attachment_is_offered_and_other_files_are_not(chan):
    item = chan._parse(mail(("quote.csv", CSV), ("logo.gif", b"GIF89a"), ("evil.exe", b"MZ"), ("../../etc/passwd.txt", b"root")))
    assert [n for n, _ in item.files] == ["quote.csv", "passwd.txt"] and item.files[0][1] == CSV and item.attachments == 4  # a path in the name is cut to the file name
    assert "GIF89a" not in repr(item) and "files" not in repr(item)  # the bytes never reach a log line


def test_at_most_two_files_and_none_from_a_message_cut_at_the_fetch_limit(chan):
    assert len(chan._parse(mail(*[(f"q{i}.csv", CSV) for i in range(5)])).files) == ch.MAX_QUOTE_FILES
    cut = mail(("quote.csv", CSV * 8000))[: ch.MAX_FETCH_BYTES]  # the attachment is cut off mid-file
    assert chan._parse(cut).attachments == 1 and chan._parse(cut).files == ()  # its bytes are not the file, so it is not offered


@pytest.fixture
def world(tmp_path, monkeypatch):
    clock = Clock()
    chans = {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}

    def build(on):
        if on:
            monkeypatch.setenv("EVENTMATE_READ_QUOTE_ATTACHMENTS", "1")
        else:
            monkeypatch.delenv("EVENTMATE_READ_QUOTE_ATTACHMENTS", raising=False)
        em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="att", channels=chans)
        em.outreach_store.now = em.conversations.now = clock
        pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
        return em, pid, sent_request(em, pid), chans, clock

    return build


def reply(chans, clock, d, files, sender=None):
    chans["email"].inbox = [Inbound(channel="email", sender=sender or d.recipient, external_id="<r1>", received_at=clock() + timedelta(minutes=5), body="See attached.", attachments=len(files),
                                    files=tuple(files))]


def test_it_is_off_unless_the_user_turned_it_on(world):
    em, pid, d, chans, clock = world(False)
    reply(chans, clock, d, [("quote.csv", CSV)])
    em.poll_conversations()
    assert em.outreach_store.inbound(pid)[0].quote_documents == () and em.evidence.records(d.vendor_id) == []


def test_when_on_the_quote_is_read_into_unreviewed_lines_tied_to_the_reply(world):
    em, pid, d, chans, clock = world(True)
    reply(chans, clock, d, [("quote.csv", CSV)])
    em.poll_conversations()
    msg = em.outreach_store.inbound(pid)[0]
    rec = em.evidence.records(d.vendor_id)
    assert len(msg.quote_documents) == 1 and len(rec) == 1 and rec[0].state is FactState.EXTRACTED and rec[0].source == f"doc:{msg.quote_documents[0]}"
    assert em.observations.quotes(d.vendor_id) == []  # nothing is a quote until the user confirms a line
    assert em.confirm_quote_line(d.vendor_id, msg.quote_documents[0], "1", 14).amount_inr == 54_000


def test_a_reply_from_another_address_is_never_opened(world):
    em, pid, d, chans, clock = world(True)
    reply(chans, clock, d, [("quote.csv", CSV)], sender="someone.else@example.org")
    chans["email"].inbox[0] = chans["email"].inbox[0].__class__(**{**chans["email"].inbox[0].__dict__, "in_reply_to": d.external_id})  # right thread, wrong address
    em.poll_conversations()
    got = em.outreach_store.inbound(pid)
    assert len(got) == 1 and got[0].sender_differs and got[0].quote_documents == () and em.evidence.records(d.vendor_id) == []  # stored and shown, never opened


def test_a_file_that_is_not_a_quote_or_is_hostile_does_not_stop_the_reply_being_stored(world):
    em, pid, d, chans, clock = world(True)
    reply(chans, clock, d, [("quote.pdf", b"not really a pdf"), ("notes.csv", b"hello,world\n")])
    em.poll_conversations()
    msg = em.outreach_store.inbound(pid)[0]
    assert msg.body == "See attached." and msg.quote_documents == () and em.evidence.records(d.vendor_id) == []


def test_a_reader_that_blows_up_never_costs_the_reply(world):
    em, pid, d, chans, clock = world(True)
    em.conversations.read_attachment = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    reply(chans, clock, d, [("quote.csv", CSV)])
    assert em.poll_conversations().new_messages == 1
    assert em.outreach_store.inbound(pid)[0].quote_documents == ()
