"""Quotes ranked by a rule fixed in advance: only fully confirmed quotes, cheapest total for the guests first, and the answer never depends on who replied first."""

import itertools
from datetime import datetime, timedelta, UTC

from eventmate.schemas import VendorCategory as C
from eventmate.sourcing import quote_compare as qc
from eventmate.sourcing.quote_readings import FieldView, QuoteAssessment

T0 = datetime(2026, 11, 7, 9, tzinfo=UTC)


def quote(vendor, price, minutes=0, cat=C.CATERER, taxes="yes", comparable=True, confirmed=8, mid=None):
    key = qc.qt.PRICE_KEY[cat]
    fields = tuple(FieldView(key=k, label=k, money=False, state="confirmed", value=v) for k, v in ((key, str(price)), ("taxes_included", taxes)))
    return QuoteAssessment(message_id=mid or f"m_{vendor}", vendor_id=vendor, category=cat, received_at=T0 + timedelta(minutes=minutes), template_used=True, fields=fields, required_total=8,
                           confirmed_required=confirmed, read_required=8, index=confirmed / 8, comparable=comparable)


def ids(c):
    return [[r.vendor_id for r in g.ranked] for g in c.categories[0].groups]


def test_cheapest_total_first_and_a_caterer_is_priced_for_these_guests():
    res = qc.compare([quote("a", 700), quote("b", 650), quote("c", 900)], guests=40)
    (g,) = res.categories[0].groups
    assert [(r.vendor_id, r.total_inr, r.rank) for r in g.ranked] == [("b", 26000, 1), ("a", 28000, 2), ("c", 36000, 3)] and res.rule == qc.RULE


def test_the_table_is_the_same_in_every_reply_order_and_speed_does_not_matter():
    qs = [quote("a", 650, 0), quote("b", 650, 90), quote("c", 600, 5000), quote("d", 700, 1)]
    seen = {tuple(map(tuple, ids(qc.compare(list(p), 40)))) for p in itertools.permutations(qs)}
    assert seen == {(("c", "a", "b", "d"),)}, "ties go to the lower vendor id, never to the earlier reply"


def test_nothing_is_ranked_until_enough_vendors_have_answered_unless_the_user_closes_it():
    res = qc.compare([quote("a", 650)], 40)
    c = res.categories[0]
    assert (c.status, c.comparable, c.needed, c.groups) == ("waiting", 1, 2, ())
    c = qc.compare([quote("a", 650)], 40, closed=True).categories[0]
    assert c.status == "ranked" and c.groups[0].ranked[0].vendor_id == "a"


def test_unconfirmed_replies_are_listed_with_what_is_missing_and_never_ranked():
    res = qc.compare([quote("a", 650), quote("b", 600, comparable=False, confirmed=3), quote("c", 700)], 40)
    c = res.categories[0]
    assert ids(res) == [["a", "c"]] and [(p.vendor_id, p.why) for p in c.pending] == [("b", "5 of 8 required fields not yet confirmed")]


def test_a_vendors_newest_comparable_reply_stands_and_an_old_one_is_not_a_second_entry():
    res = qc.compare([quote("a", 650, 0, mid="m1"), quote("a", 600, 60, mid="m2"), quote("b", 700)], 40)
    assert [(r.vendor_id, r.price_inr) for r in res.categories[0].groups[0].ranked] == [("a", 600), ("b", 700)]


def test_prices_that_leave_taxes_out_are_ranked_apart_and_no_rate_is_assumed():
    res = qc.compare([quote("a", 500, taxes="no"), quote("b", 650), quote("c", 700)], 40)
    g = res.categories[0].groups
    assert [(x.taxes_included, [r.vendor_id for r in x.ranked]) for x in g] == [(True, ["b", "c"]), (False, ["a"])]


def test_a_venue_is_priced_as_quoted_and_categories_are_kept_apart():
    res = qc.compare([quote("v1", 90000, cat=C.VENUE), quote("v2", 80000, cat=C.VENUE), quote("a", 650), quote("b", 600)], 40)
    by = {c.category: c for c in res.categories}
    assert [r.total_inr for r in by[C.VENUE].groups[0].ranked] == [80000, 90000] and [r.total_inr for r in by[C.CATERER].groups[0].ranked] == [24000, 26000]


def test_no_replies_is_an_empty_table():
    assert qc.compare([], 40).categories == ()


def test_a_single_quote_can_be_chosen_while_ranking_waits_and_what_is_chosen_is_reported():
    c = qc.compare([quote("a", 650)], 40).categories[0]
    assert c.status == "waiting" and c.groups == () and [(r.vendor_id, r.rank, r.total_inr) for r in c.open_quotes] == [("a", 0, 26_000)] and c.chosen == ()
    c = qc.compare([quote("a", 650), quote("b", 700, comparable=False)], 40, chosen=frozenset({"m_a", "m_elsewhere"})).categories[0]
    assert c.chosen == ("m_a",), "only a reply of this kind in this brief counts as chosen"
    assert [r.vendor_id for r in qc.compare([quote("z", 650), quote("a", 700)], 40, closed=False).categories[0].groups[0].ranked] == ["z", "a"]
