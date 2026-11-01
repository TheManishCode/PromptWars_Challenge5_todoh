"""A draft is the text the user saw; after a replan it must still be true. Stale drafts are refused at approval and at send time, never rewritten or resent."""

from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from eventmate.messaging.conversations import ConversationService, SendPolicy
from eventmate.messaging.outreach import basis_sha256
from eventmate.schemas import OutreachDraft, Disruption, DisruptionKind as K, VendorCategory as C
from eventmate.service import EventMate
from eventmate.state import build_state
from tests.conftest import EXAMPLE, TODAY
from tests.test_conversations import Clock, FakeChannel

CONTACT = "sales@annapurna.in"
STALE = "The plan changed after this was written. Draft it again."


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def chans():
    return {"email": FakeChannel("email"), "whatsapp": FakeChannel("whatsapp")}


@pytest.fixture
def em(tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="stale", channels=chans)
    e.outreach_store.now = e.conversations.now = clock
    e.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=50, whatsapp_gap_s=0)
    return e


@pytest.fixture
def pid(em):
    return em.plan(EXAMPLE, today=TODAY).plan.plan_id


def caterer_id(em, pid):
    return next(s.vendor_id for s in em.store.latest(pid).selections if s.category == C.CATERER)


def status(em, pid, d):
    return em.outreach_store.get(pid, d.draft_id).status


def drop_vendor(em, pid, vendor_id):
    out = em.replan(pid, Disruption(kind=K.VENDOR_CANCELLATION, vendor_id=vendor_id), today=TODAY)
    assert out.status == "replanned" and vendor_id not in {s.vendor_id for s in out.plan.selections}


def test_a_draft_for_a_vendor_the_plan_dropped_is_refused_and_marked_stale(em, pid, chans):  # R4 1
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    assert d.plan_version == 1 and d.for_plan_pick and len(d.basis_sha256) == 64
    drop_vendor(em, pid, d.vendor_id)
    assert status(em, pid, d) == "stale"
    with pytest.raises(ValueError, match=STALE):
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert status(em, pid, d) == "stale" and chans["email"].sent == []


def test_approval_itself_checks_even_if_nothing_marked_the_draft_first(em, pid, chans, monkeypatch):
    monkeypatch.setattr(em.conversations, "supersede_stale", lambda plan_id: [])  # the sweep did not run
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    drop_vendor(em, pid, d.vendor_id)
    assert status(em, pid, d) == "prepared"
    with pytest.raises(ValueError, match=STALE):
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert status(em, pid, d) == "stale" and chans["email"].sent == []


def test_an_approved_but_unsent_draft_is_not_sent_after_a_replan(em, pid, chans, monkeypatch):  # R4 2
    em.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=0, whatsapp_gap_s=0)  # approved, but held back by the daily limit
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    assert em.approve_outreach(pid, d.draft_id, d.content_hash).status == "approved"
    monkeypatch.setattr(em.conversations, "supersede_stale", lambda plan_id: [])  # prove dispatch alone catches it
    drop_vendor(em, pid, d.vendor_id)
    assert status(em, pid, d) == "approved"
    em.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=50, whatsapp_gap_s=0)
    em.poll_conversations()
    assert chans["email"].sent == [] and status(em, pid, d) == "stale"


def test_the_sweep_also_catches_an_approved_unsent_draft_straight_after_the_replan(em, pid, chans):
    em.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=0, whatsapp_gap_s=0)
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    em.approve_outreach(pid, d.draft_id, d.content_hash)
    drop_vendor(em, pid, d.vendor_id)
    assert status(em, pid, d) == "stale"
    em.conversations.policy = SendPolicy(per_vendor_per_day=10, per_day=50, whatsapp_gap_s=0)
    em.poll_conversations()
    assert chans["email"].sent == []


def test_a_replan_that_keeps_the_vendor_and_the_facts_leaves_the_draft_valid(em, pid, chans):  # R4 3
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    out = em.replan(pid, Disruption(kind=K.BUDGET_CHANGE, new_budget=190_000), today=TODAY)
    assert out.plan.version == 2 and caterer_id(em, pid) == d.vendor_id  # a new version, the same vendor, the same event facts
    assert status(em, pid, d) == "prepared"
    assert em.approve_outreach(pid, d.draft_id, d.content_hash).status == "sent" and len(chans["email"].sent) == 1


def test_a_headcount_change_with_the_vendor_kept_makes_the_draft_stale_and_a_fresh_one_is_needed(em, pid, chans):  # R4 4
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    out = em.replan(pid, Disruption(kind=K.GUEST_COUNT_CHANGE, new_guest_count=110), today=TODAY)
    assert out.status == "replanned" and caterer_id(em, pid) == d.vendor_id
    assert status(em, pid, d) == "stale"
    with pytest.raises(ValueError, match=STALE):
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    fresh = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    assert fresh.draft_id != d.draft_id and "110 guests" in fresh.body and fresh.plan_version == 2 and fresh.content_hash != d.content_hash
    assert em.approve_outreach(pid, fresh.draft_id, fresh.content_hash).status == "sent"  # a fresh draft, fresh exact-text approval
    assert len(chans["email"].sent) == 1 and "110 guests" in chans["email"].sent[0][2]


def test_a_changed_headcount_makes_every_waiting_request_stale_but_a_followup_is_not_tied_to_it(em, pid, chans, clock):
    venue = em.draft_outreach(pid, C.VENUE, "email", "hall@orchid.example", "Asha")
    first = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    sent = em.approve_outreach(pid, first.draft_id, first.content_hash)
    follow = em.followup_outreach(pid, sent.draft_id, "Could you share the total price?", "Asha")
    assert follow.for_plan_pick is True and follow.plan_version == 1 and follow.basis_sha256 == ""
    out = em.replan(pid, Disruption(kind=K.GUEST_COUNT_CHANGE, new_guest_count=110), today=TODAY)
    assert out.status == "replanned" and {s.vendor_id for s in out.plan.selections} >= {venue.vendor_id, sent.vendor_id}
    assert status(em, pid, venue) == "stale"  # it states the guest count
    assert status(em, pid, follow) == "prepared"  # its words do not, and its vendor is still in the plan
    assert status(em, pid, sent) == "sent"  # what was already sent is history, not something to take back


def test_a_discovered_vendor_is_never_rebound_to_a_seed_vendor_and_goes_stale_on_changed_facts(em, pid, chans):  # R4 5
    plan = em.store.latest(pid)
    ext = "ext_" + "a" * 24
    d = em.outreach_store.draft(plan, C.CATERER, "email", CONTACT, "Asha", vendor=(ext, "Garden Caterers", "discovered"))
    assert d.vendor_id == ext and d.vendor_origin == "discovered" and d.for_plan_pick is False
    em.replan(pid, Disruption(kind=K.BUDGET_CHANGE, new_budget=190_000), today=TODAY)
    assert status(em, pid, d) == "prepared"  # not a plan pick, so a changed plan pick does not touch it
    drop_vendor(em, pid, caterer_id(em, pid))
    cur = em.outreach_store.get(pid, d.draft_id)
    assert cur.vendor_id == ext and cur.vendor_origin == "discovered" and cur.recipient == CONTACT  # it is still addressed to the same business
    em.replan(pid, Disruption(kind=K.GUEST_COUNT_CHANGE, new_guest_count=110), today=TODAY)
    assert status(em, pid, d) == "stale"
    with pytest.raises(ValueError, match=STALE):
        em.approve_outreach(pid, d.draft_id, d.content_hash)


def test_an_unreadable_plan_fails_closed_and_a_legacy_draft_without_a_version_is_stale(em, pid):
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    svc = ConversationService(em.outreach_store, {}, lambda plan_id: (_ for _ in ()).throw(FileNotFoundError("gone")))
    assert svc.stale_reason(d) == "the plan could not be read"
    legacy = d.model_copy(update={"plan_version": None})
    assert "before drafts recorded" in svc.stale_reason(legacy)
    for status_ in ("sent", "denied", "marked_sent", "sending", "outcome_unknown", "failed", "expired", "blocked", "stale"):
        assert svc.stale_reason(d.model_copy(update={"status": status_})) is None and svc.stale_reason(legacy.model_copy(update={"status": status_})) is None  # history is never touched


def write_legacy(em, pid, status_="prepared", kind="request", parent=None):
    """A draft exactly as an older version of the app stored it: no plan_version, basis or pick flag."""
    d = em.draft_outreach(pid, C.CATERER, "email", CONTACT, "Asha")
    extra = {"sent_at": d.created_at, "external_id": "<legacy@x.test>"} if status_ in ("sent",) else {}
    old = OutreachDraft.model_validate({**d.model_dump(), "plan_version": None, "basis_sha256": "", "for_plan_pick": False, "status": status_, **extra})
    em.outreach_store._file(pid)._append(old, d.draft_id)
    return old


def reopen(em, tmp_path, chans, clock):
    e = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="stale2", channels=chans)
    e.outreach_store.now = e.conversations.now = clock
    return e


def test_a_waiting_legacy_draft_is_refused_and_marked_stale_at_approval(em, pid, chans):
    d = write_legacy(em, pid)
    with pytest.raises(ValueError, match=STALE):
        em.approve_outreach(pid, d.draft_id, d.content_hash)
    assert status(em, pid, d) == "stale" and chans["email"].sent == []


def test_an_approved_but_unsent_legacy_draft_is_never_sent(em, pid, chans):
    d = write_legacy(em, pid, "approved")
    em.poll_conversations()
    assert chans["email"].sent == [] and status(em, pid, d) == "stale"


def test_start_up_marks_waiting_and_approved_legacy_drafts_stale_and_leaves_sent_history_alone(em, pid, chans, clock, tmp_path):
    waiting = write_legacy(em, pid, "prepared")
    approved = write_legacy(em, pid, "approved")
    sent = write_legacy(em, pid, "sent")
    denied = write_legacy(em, pid, "denied")
    before = {x.draft_id: x for x in em.outreach_store.list(pid) if x.draft_id == sent.draft_id or x.draft_id == denied.draft_id}
    again = reopen(em, tmp_path, chans, clock)  # the app restarts on the same directories
    st = {d.draft_id: d.status for d in again.outreach(pid)}
    assert st[waiting.draft_id] == "stale" and st[approved.draft_id] == "stale" and st[sent.draft_id] == "sent" and st[denied.draft_id] == "denied"
    assert again.outreach_store.get(pid, sent.draft_id) == before[sent.draft_id] and again.outreach_store.get(pid, denied.draft_id) == before[denied.draft_id]
    again.poll_conversations()
    assert chans["email"].sent == []  # nothing was sent by the restart or by polling
    assert reopen(em, tmp_path, chans, clock).conversations.supersede_all_stale() == []  # and the sweep is idempotent


def test_a_legacy_followup_to_a_sent_request_is_stale_too_but_the_sent_request_is_not(em, pid, chans):
    parent = write_legacy(em, pid, "sent")
    f = em.outreach_store.followup(pid, parent.draft_id, "Could you share the total price?", "Asha")
    legacy_follow = f.model_copy(update={"plan_version": None, "for_plan_pick": False})
    em.outreach_store._file(pid)._append(legacy_follow, f.draft_id)
    assert em.conversations.supersede_stale(pid) == [f.draft_id] and status(em, pid, parent) == "sent"


def fake_plan(guests=100, vendor="v1", date="2026-12-12", diets=(), city="Pune", kind="wedding"):
    req = NS(event_type=NS(value=kind), city=city, event_date=NS(isoformat=lambda: date), guest_count=guests, dietary=[NS(value=d) for d in diets])
    return NS(request=req, selections=[NS(vendor_id=vendor)], version=1)


def test_the_basis_covers_exactly_the_facts_the_text_states():
    base = basis_sha256(fake_plan(), C.VENUE)
    assert basis_sha256(fake_plan(), C.VENUE) == base
    for changed in (fake_plan(guests=101), fake_plan(date="2026-12-13"), fake_plan(city="Mumbai"), fake_plan(kind="birthday")):
        assert basis_sha256(changed, C.VENUE) != base
    assert basis_sha256(fake_plan(diets=("vegan",)), C.VENUE) == base  # a venue request does not mention diets
    assert basis_sha256(fake_plan(diets=("vegan",)), C.CATERER) != basis_sha256(fake_plan(), C.CATERER)  # a caterer request does
    assert basis_sha256(fake_plan(diets=("vegan", "jain")), C.CATERER) == basis_sha256(fake_plan(diets=("jain", "vegan")), C.CATERER)  # in any order


def test_a_stale_draft_shows_up_in_the_state_and_in_a_quote_job_as_something_to_redo_not_as_a_send(em, pid, chans, clock):
    em.jobs_clock = clock
    job = em.jobs.start_job(pid, C.CATERER, {caterer_id(em, pid): ("email", CONTACT)}, "get a quote", clock() + timedelta(days=3), "Asha", min_quotes=1)
    em.replan(pid, Disruption(kind=K.GUEST_COUNT_CHANGE, new_guest_count=110), today=TODAY)
    [draft] = list(em.outreach(pid))
    assert draft.status == "stale"
    items = [i for i in em.jobs.store.get(job.job_id).items if i.kind == "request_stale" and not i.resolved]
    assert len(items) == 1 and "Draft it again" in items[0].text and "nothing was sent" in items[0].text
    assert chans["email"].sent == []
    s = build_state(em, pid, now=clock())
    assert any(i.kind == "job_item" and "out of date" in i.label for i in s.open_items) and s.vendors[1].conversation == "problem"
