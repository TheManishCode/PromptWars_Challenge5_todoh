"""Reading the mailbox: replies are found by asking the server for the threads we know, not by reading the newest few (audit A3); what could not be read is counted and shown;
a spam folder is read only if named, read-only, and only into the user's review; a server that cannot search that way is said to be reading less."""
import email.utils
from datetime import timedelta
from email.message import EmailMessage

import pytest

from eventmate.messaging.channels import MAX_THREAD_MESSAGES, EmailConfig
from eventmate.messaging.conversations import SendPolicy
from eventmate.schemas import VendorCategory as C
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY
from tests.mailfake import CFG, MailWorld
from tests.test_conversations import Clock
from tests.test_jobs_worker import NoWhatsApp

VENDOR = "cat@vendor.test"


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def world(clock):
    return MailWorld(clock)


@pytest.fixture
def em(tmp_path, world, clock):
    e = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="poll", channels={"email": world.channel(), "whatsapp": NoWhatsApp()}, require_symbolic=False)
    e.outreach_store.now = e.conversations.now = clock
    e.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=100, whatsapp_gap_s=0)
    return e


@pytest.fixture
def pid(em, world):
    plan = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    d = em.draft_outreach(plan, C.CATERER, "email", VENDOR, "Asha")
    em.approve_outreach(plan, d.draft_id, d.content_hash)
    assert world.sent_to(VENDOR)
    return plan


def stranger_mail(world, n, clock, sender=None):
    """Mail from other people: it fills the mailbox and has nothing to do with our requests."""
    for i in range(n):
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = sender or f"news{i}@elsewhere.test", CFG.from_addr, f"Newsletter {i}"
        m["Date"] = email.utils.format_datetime(clock() + timedelta(minutes=20))
        m["Message-ID"] = f"<news-{i}@elsewhere.test>"
        m.set_content("hello")
        world.folders["INBOX"].append(m.as_bytes())


def test_a_reply_buried_under_a_busy_mailbox_is_still_found(em, world, clock, pid):
    world.reply(VENDOR, "Price per plate (INR): 650")  # the vendor answers first; sixty other messages arrive after it
    stranger_mail(world, 60, clock)
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert res.new_messages == 1 and [m.vendor_id for m in em.outreach_store.inbound(pid)] and res.overflow == 0, "the newest-fifty rule dropped this one silently"


def test_a_poll_that_leaves_replies_behind_says_how_many_and_the_next_poll_goes_on(em, world, clock, pid):
    for n in range(MAX_THREAD_MESSAGES + 7):
        world.reply(VENDOR, f"Price per plate (INR): {600 + n}", message_id=f"<many-{n}@vendor.test>")
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert res.overflow == 7 and any("7 more replies" in w for w in res.warnings), res
    assert res.new_messages == MAX_THREAD_MESSAGES
    again = em.poll_conversations()
    assert again.overflow == 7 and again.new_messages == 0, "the oldest are what was left: they are counted again, not lost"


def test_a_quiet_mailbox_reports_nothing_left_behind(em, world, clock, pid):
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert (res.new_messages, res.overflow, res.warnings, res.held) == (0, 0, (), 0)


def test_the_search_asks_the_server_for_the_threads_we_know_and_never_opens_anything_for_writing(em, world, clock, pid):
    world.reply(VENDOR, "Price per plate (INR): 650")
    clock.advance(minutes=30)
    em.poll_conversations()  # (the fake asserts every select is read-only)
    asked = [c for c in world.searches if c[0] == "SINCE"]
    assert any("FROM" in c and f'"{VENDOR}"' in c for c in asked), asked
    assert any("HEADER" in c and "In-Reply-To" in c for c in asked), asked
    assert any("FROM" in c and '"mailer-daemon"' in c for c in asked), "delivery failure notices are looked for too"


def test_stop_requests_from_strangers_still_reach_the_user(em, world, clock, pid):
    stranger_mail(world, 1, clock, sender="somebody@else.test")
    world.folders["INBOX"][-1] = world.folders["INBOX"][-1].replace(b"hello", b"STOP")
    clock.advance(minutes=30)
    em.poll_conversations()
    assert [m.kind for m in em.unmatched_messages()] == ["stop_like"]


def test_a_server_that_cannot_search_by_thread_is_read_by_window_and_the_user_is_told(em, world, clock, pid):
    world.no_header_search = True
    world.reply(VENDOR, "Price per plate (INR): 650")
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert res.new_messages == 1 and any("cannot search by thread" in w for w in res.warnings), res


def test_the_spam_folder_is_not_looked_at_unless_it_is_named(em, world, clock, pid):
    world.folders["Spam"].append(_vendor_reply(world, clock))
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert res.spam == 0 and em.unmatched_messages() == []


def test_a_reply_the_provider_called_spam_goes_to_the_users_review_and_is_filed_nowhere(tmp_path, world, clock, pid, em):
    em.conversations.channels["email"].config = EmailConfig(**{**CFG.__dict__, "spam_folder": "Spam"})
    world.folders["Spam"].append(_vendor_reply(world, clock))
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert res.spam == 1 and res.new_messages == 0 and em.outreach_store.inbound(pid) == []
    (m,) = em.unmatched_messages()
    assert m.kind == "spam" and m.sender == VENDOR and "650" in m.body
    assert em.poll_conversations().spam == 1 and len(em.unmatched_messages()) == 1, "seen again, shown once"


def test_a_spam_folder_that_is_missing_is_said_not_ignored(em, world, clock, pid):
    em.conversations.channels["email"].config = EmailConfig(**{**CFG.__dict__, "spam_folder": "Junk E-mail"})
    clock.advance(minutes=30)
    res = em.poll_conversations()
    assert any("spam folder could not be opened" in w for w in res.warnings), res


def _vendor_reply(world, clock):
    world.reply(VENDOR, "Price per plate (INR): 650")
    return world.folders["INBOX"].pop()  # the provider filed it as spam instead


def test_a_requests_status_never_claims_more_than_is_known(em, world, clock, pid):
    (d,) = em.outreach_with_delivery(pid)
    assert d["status"] == "sent" and d["delivery"]["state"] == "accepted" and "not proof it was delivered" in d["delivery"]["label"]
    world.reply(VENDOR, "Price per plate (INR): 650")
    clock.advance(minutes=30)
    em.poll_conversations()
    (d,) = em.outreach_with_delivery(pid)
    assert d["delivery"] == {"state": "replied", "label": "Replied"}


def test_a_bounce_shows_as_bounced_and_a_queued_request_as_queued(em, world, clock, pid):
    world.bounce(VENDOR)
    clock.advance(minutes=30)
    em.poll_conversations()
    (d,) = em.outreach_with_delivery(pid)
    assert d["delivery"]["state"] == "bounced" and "not be delivered" in d["delivery"]["label"]
    other = em.draft_outreach(pid, C.PHOTOGRAPHER, "email", "photo@vendor.test", "Asha")
    states = {x["draft_id"]: x["delivery"]["state"] for x in em.outreach_with_delivery(pid)}
    assert states[other.draft_id] == "draft"
    em.pause_sending()
    em.approve_outreach(pid, other.draft_id, other.content_hash)
    assert {x["draft_id"]: x["delivery"]["state"] for x in em.outreach_with_delivery(pid)}[other.draft_id] == "queued"


def test_the_route_carries_the_delivery_wording(em, world, clock, pid):
    from eventmate.web.app import create_app

    client = create_app(em, today_fn=lambda: TODAY, poll_seconds=0).test_client()
    (d,) = client.get(f"/api/plans/{pid}/outreach").get_json()
    assert d["delivery"]["state"] == "accepted"
