"""Outbound safety before any real vendor is written to: one plan cannot use up the day, every first request says who wrote it and how to stop it, and that
line is inside the approved text, so approval covers it and a reply of STOP is read back as a stop request."""
from datetime import timedelta

import pytest
from pydantic import ValidationError

from eventmate.messaging.channels import Inbound
from eventmate.messaging.conversations import SendPolicy
from eventmate.messaging.outreach import STOP_LINE
from eventmate.schemas import OutreachDraft
from tests import test_conversations as tc
from tests.conftest import EXAMPLE, TODAY

em, pid, chans, clock = tc.em, tc.pid, tc.chans, tc.clock  # the fixtures of the conversation tests


def test_the_default_policy_has_a_per_plan_cap_below_the_daily_one():
    p = SendPolicy()
    assert p.per_plan_per_day == 10 < p.per_day == 30


def test_one_plan_cannot_send_more_first_requests_than_its_cap_in_a_day_and_another_plan_still_can(em, pid, chans, clock):
    em.conversations.policy = SendPolicy(per_vendor_per_day=99, per_day=99, per_plan_per_day=2, whatsapp_gap_s=0)
    for n in range(3):
        d = tc.draft(em, pid, recipient=f"v{n}@v.in")
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert [x.status for x in em.outreach(pid)] == ["sent", "sent", "approved"]
    waiting = em.poll_conversations().waiting
    assert any("first requests in 24 hours" in w for w in waiting), waiting
    other = em.plan(EXAMPLE.replace("120-person", "90-person"), today=TODAY).plan.plan_id
    assert other != pid
    d = tc.draft(em, other, recipient="o@v.in")
    em.approve_outreach(other, d.draft_id, d.content_hash)
    assert em.outreach(other)[-1].status == "sent"
    clock.advance(hours=25)
    em.conversations.dispatch()
    assert em.outreach(pid)[-1].status == "sent"  # the cap is per day: what waited goes the next day


@pytest.mark.parametrize("channel,recipient", [("email", "sales@annapurna.in"), ("whatsapp", "+91 98765 43210")])
def test_every_first_request_says_who_wrote_it_and_how_to_stop(em, pid, chans, channel, recipient):
    d = tc.draft(em, pid, channel=channel, recipient=recipient)
    assert d.body.rstrip().endswith(f"(Written with to;doh on behalf of Asha. {STOP_LINE})")
    assert STOP_LINE in d.link or STOP_LINE.replace(" ", "%20") in d.link  # the manual fallback carries it too
    em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert STOP_LINE in chans[channel].sent[0][2]  # and it is what the channel was handed


def test_the_footer_is_inside_the_approved_hash(em, pid):
    d = tc.draft(em, pid)
    without = d.body.replace(STOP_LINE, "")
    assert without != d.body
    with pytest.raises(ValidationError):  # a record with the line taken out no longer matches the hash the user approved
        OutreachDraft.model_validate({**d.model_dump(), "body": without})
    with pytest.raises(ValueError):
        em.approve_outreach(pid, d.draft_id, "0" * 64)


def test_replying_stop_to_the_footer_suppresses_the_address_and_quoting_it_does_not(em, pid, chans, clock):
    d = tc.draft(em, pid)
    sent = em.approve_outreach(pid, d.draft_id, d.content_hash)
    quoted = "Here is our quote: 650 per plate.\n\n> " + STOP_LINE
    chans["email"].inbox.append(Inbound(channel="email", sender=d.recipient, external_id="<q1>", received_at=clock() + timedelta(minutes=5), body=quoted, in_reply_to=sent.external_id))
    em.poll_conversations()
    assert em.suppressions.active_for("email", d.recipient) is None
    chans["email"].inbox.append(Inbound(channel="email", sender=d.recipient, external_id="<s1>", received_at=clock() + timedelta(minutes=9), body="STOP", in_reply_to=sent.external_id))
    em.poll_conversations()
    s = em.suppressions.active_for("email", d.recipient)
    assert s is not None and s.reason == "stop_request"
    with pytest.raises(ValueError):  # nothing more can be written to that address
        tc.draft(em, pid)
