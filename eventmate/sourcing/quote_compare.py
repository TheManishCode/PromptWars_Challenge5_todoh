"""Quotes side by side, ranked by a rule that is fixed before any reply is opened.

The rule is ``RULE`` and is shown with the result: only quotes the user has fully confirmed (``comparable``) are ranked, one per vendor (the newest comparable reply), by what the whole job
would cost for these guests (a caterer's per-plate price times the guests; a venue's or photographer's price as quoted). Nothing else moves a vendor up: not who answered first, not how
long the reply took, not how it was worded. Ties go to the lower vendor id, so the same replies in any order give the same table. A category is ranked only once ``min_quotes`` vendors have
a comparable quote, or the user says they are done waiting (``closed``), so the first answer is never simply the winner. Prices that leave taxes out are ranked apart from those that
include them: no tax rate is assumed. Replies that are not comparable are listed with what is still missing, never ranked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from eventmate.schemas import VendorCategory, _Model
from eventmate.sourcing import quote_template as qt
from eventmate.sourcing.quote_readings import QuoteAssessment

RULE = "Cheapest total for your guest count first, among quotes you have fully confirmed. Reply order and speed play no part. Taxes are not assumed: prices with taxes extra are ranked apart."
MIN_QUOTES = 2


class Ranked(_Model):
    rank: int
    vendor_id: str
    message_id: str
    total_inr: int
    price_inr: int
    basis: str  # "per_plate" or "total"
    taxes_included: bool


class Group(_Model):
    taxes_included: bool
    ranked: tuple[Ranked, ...]


class Pending(_Model):
    vendor_id: str
    message_id: str
    why: str


class CategoryComparison(_Model):
    category: VendorCategory
    status: str  # "ranked", "waiting" (fewer than min_quotes comparable), or "none"
    comparable: int
    needed: int
    groups: tuple[Group, ...] = ()
    pending: tuple[Pending, ...] = ()
    open_quotes: tuple[Ranked, ...] = ()  # while ranking waits: what is confirmed so far, unranked (rank 0, by vendor id so order plays no part), so it can still be chosen
    chosen: tuple[str, ...] = ()  # the replies of this kind the user has already chosen to plan with


class Comparison(_Model):
    rule: str
    guests: int
    categories: tuple[CategoryComparison, ...]


def _why(a: QuoteAssessment) -> str:
    blocking = [i.text for i in a.issues if i.blocking and not i.tentative]
    if blocking:
        return "; ".join(blocking)
    left = a.required_total - a.confirmed_required
    return f"{left} of {a.required_total} required fields not yet confirmed" if left else "the vendor did not say it is available"


def _entry(a: QuoteAssessment, guests: int) -> tuple[int, int, bool]:
    by = {f.key: f for f in a.fields}
    price = int(by[qt.PRICE_KEY[a.category]].value)
    per_plate = a.category == VendorCategory.CATERER
    return (price * guests if per_plate else price), price, by["taxes_included"].value == "yes"


def _best_per_vendor(found: Iterable[QuoteAssessment]) -> list[QuoteAssessment]:
    """The newest comparable reply of each vendor. Ties on time go to the larger message id, so the pick never depends on input order."""
    best: dict[str, QuoteAssessment] = {}
    for a in found:
        cur = best.get(a.vendor_id)
        if cur is None or (a.received_at, a.message_id) > (cur.received_at, cur.message_id):
            best[a.vendor_id] = a
    return list(best.values())


def _groups(found: list[QuoteAssessment], guests: int) -> tuple[Group, ...]:
    rows = defaultdict(list)
    for a in found:
        total, price, taxed = _entry(a, guests)
        rows[taxed].append((total, a.vendor_id, a.message_id, price, a.category))
    out = []
    for taxed in (True, False):
        ordered = sorted(rows[taxed], key=lambda r: (r[0], r[1]))
        if ordered:
            out.append(Group(taxes_included=taxed, ranked=tuple(
                Ranked(rank=i, vendor_id=v, message_id=m, total_inr=t, price_inr=p, basis="per_plate" if c == VendorCategory.CATERER else "total", taxes_included=taxed)
                for i, (t, v, m, p, c) in enumerate(ordered, 1))))
    return tuple(out)


def compare(assessments: Iterable[QuoteAssessment], guests: int, closed: bool = False, min_quotes: int = MIN_QUOTES, chosen: frozenset[str] = frozenset()) -> Comparison:
    by_cat: dict[VendorCategory, list[QuoteAssessment]] = defaultdict(list)
    for a in assessments:
        by_cat[a.category].append(a)
    cats = []
    for cat in sorted(by_cat, key=lambda c: c.value):
        mine = by_cat[cat]
        ok = _best_per_vendor(a for a in mine if a.comparable)
        pending = tuple(sorted((Pending(vendor_id=a.vendor_id, message_id=a.message_id, why=_why(a)) for a in mine if not a.comparable and a.vendor_id not in {o.vendor_id for o in ok}),
                               key=lambda p: (p.vendor_id, p.message_id)))
        status = "none" if not ok else "ranked" if closed or len(ok) >= min_quotes else "waiting"
        unranked = tuple(Ranked(rank=0, vendor_id=a.vendor_id, message_id=a.message_id, total_inr=t, price_inr=p, basis="per_plate" if a.category == VendorCategory.CATERER else "total", taxes_included=x)
                         for a in sorted(ok, key=lambda a: a.vendor_id) for t, p, x in [_entry(a, guests)]) if status == "waiting" else ()
        cats.append(CategoryComparison(category=cat, status=status, comparable=len(ok), needed=min_quotes, groups=_groups(ok, guests) if status == "ranked" else (), pending=pending,
                                       open_quotes=unranked, chosen=tuple(sorted(a.message_id for a in mine if a.message_id in chosen))))
    return Comparison(rule=RULE, guests=guests, categories=tuple(cats))
