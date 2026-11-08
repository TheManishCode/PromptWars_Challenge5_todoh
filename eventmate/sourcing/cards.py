"""A vendor as a card: every fact the app holds about a business, each with where it came from, when, and whether the user confirmed it.

The card never holds anything from a Google listing except the place id: a listing's name, address, rating, link, website and phone are fetched again whenever a card is shown (Google's policy lets
only the place id be stored). What is held is the user's own facts (a confirmed name, a confirmed address, a typed website), what a business itself published (its own site), what an open
map listing says (OpenStreetMap), the requests the user approved, and what the vendor wrote back. Facts are claims: two that disagree are both shown, flagged as a conflict, and neither wins.
This is the one shape the quote template, the completeness score and the comparison read.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from eventmate.pricing.observations import old_reasons
from eventmate.schemas import ExternalBusiness, InboundMessage, OutreachDraft, QuoteObservation, ShortlistItem, VendorCategory, _Model

# FUTURE(places-refresh): Google advises refreshing a stored place id older than 12 months (ids can change). ExternalBusiness.created_at is the age; refresh through a search and show "no longer listed" on NOT_FOUND (done) until then.
# DECIDED(cards-legal): two things are kept that sit next to a Google listing: the name the user types for their own record, and the URL of the vendor's own page where an address was read (provenance of what the vendor published). Both are treated as the user's and the vendor's own content, not Google's. Counsel to confirm before a public launch.
# FUTURE(cards-dedupe): one business that matches two searches (a caterer that is also a venue) is two cards. Merge by place id, then phone, then name and postcode, always with the user confirming a merge.
# FUTURE(cards-contact-log): writing to a vendor yourself (mailto, WhatsApp) is outside the app: it is not recorded and the reply does not come back. Offer "I contacted them" so the card can show it, and a way to paste the reply in.


SourceType = Literal["user_confirmed", "vendor_website", "osm_listing", "vendor_quote", "vendor_reply", "app_request", "earlier_quote"]
CONTACT_FIELDS = ("contact.email", "contact.whatsapp", "website", "name")  # one value each: two different ones are a conflict
_SOURCE_OF = {"listing_field": "osm_listing", "page_mailto": "vendor_website", "page_text": "vendor_website"}


class VendorFact(_Model):
    field: str = Field(max_length=40)  # name | website | contact.email | contact.whatsapp | quote | request | reply
    value: str = Field(max_length=300)
    source_type: SourceType
    source_ref: str | None = Field(default=None, max_length=2048)  # the page, the message id or the draft id it came from
    captured_at: datetime | None = None
    status: str = Field(max_length=30)  # found | confirmed | rejected | held | valid | old | prepared | sent | received ...
    user_confirmed: bool = False
    evidence: str | None = Field(default=None, max_length=300)  # the words it was read from
    conflict: bool = False  # another live claim for the same field says something else
    note: str = Field(default="", max_length=300)
    candidate_id: str | None = Field(default=None, max_length=64)  # a found address: the id the page sends to confirm or reject it


class VendorCard(_Model):
    vendor_id: str
    category: VendorCategory
    origin: Literal["discovered", "test", "user"]
    source: Literal["osm", "gplaces", "test", "user"]
    place_id: str | None = None  # the Google place id, to fetch the listing's own details now; None for any other source
    name: str  # a Google listing's name is only here once the user confirmed one
    name_confirmed: bool = False
    label: str | None = None  # "TEST VENDOR (address owned by ...)"
    blocked: str | None = None
    facts: tuple[VendorFact, ...] = ()
    contact_ready: bool = False  # the user confirmed an address for this business


def _money(q) -> str:  # a QuoteObservation, or the dict of an earlier quote from someone else
    q = q if isinstance(q, dict) else q.model_dump()
    unit = "per plate" if q["basis"] == "per_plate" else "in total"
    return f"INR {q['amount_inr']:,} {unit}" + (f" for {q['guest_count']} guests" if q["guest_count"] else "")


def _snippet(text: str, n: int = 200) -> str:
    return " ".join(text.split())[:n]


def assemble(item: ShortlistItem, quotes: list[QuoteObservation], drafts: list[OutreachDraft], replies: list[InboundMessage], today: date, guests: int | None = None, event_date: date | None = None,
             earlier: list[dict] | None = None) -> VendorCard:
    biz: ExternalBusiness = item.business
    facts: list[VendorFact] = []
    if biz.source == "osm":
        facts.append(VendorFact(field="name", value=biz.name, source_type="osm_listing", source_ref=biz.source_url, captured_at=biz.created_at, status="held"))
    elif biz.name_source == "user_confirmed" or biz.source in ("test", "user"):
        facts.append(VendorFact(field="name", value=biz.name, source_type="user_confirmed", captured_at=biz.created_at, status="confirmed", user_confirmed=True))
    if biz.website:
        mine = biz.website_source == "user_input"
        facts.append(VendorFact(field="website", value=biz.website, source_type="user_confirmed" if mine else "osm_listing", source_ref=biz.source_url, captured_at=biz.created_at, status="confirmed" if mine else "held", user_confirmed=mine))
    seen = set()
    for c in item.candidates:
        value = c.confirmed_value or c.value
        seen.add((c.channel, value.casefold()))
        facts.append(VendorFact(
            field=f"contact.{c.channel}", value=value, source_type=_SOURCE_OF.get(c.source_kind, "vendor_website"), source_ref=c.source_url, captured_at=c.found_at, status=c.status,
            user_confirmed=c.status == "confirmed", note=", ".join(c.flags), candidate_id=c.candidate_id,
        ))
    k = item.contact
    if k is not None:
        for channel, value in (("email", k.email), ("whatsapp", k.whatsapp)):
            if value and (channel, value.casefold()) not in seen:  # a test vendor, or an address the user typed: there is no candidate it came from
                facts.append(VendorFact(field=f"contact.{channel}", value=value, source_type="user_confirmed", source_ref=k.source_url, captured_at=k.confirmed_at, status="confirmed", user_confirmed=True, note=f"confirmed by {k.confirmed_by}"))
    for q in reversed(quotes):
        why = old_reasons(q, today, guests, event_date)
        facts.append(VendorFact(
            field="quote", value=_money(q), source_type="vendor_quote", source_ref=q.evidence_ref or None, captured_at=q.observed_at, status="old" if why else "valid", user_confirmed=True,
            note=("may be out of date: " + "; ".join(why)) if why else f"valid until {q.expires_at.isoformat()}",
        ))
    for e in (earlier or [])[:3]:  # what another person was last quoted by this vendor: there is no source to link and no name to show, by design (pricing/shared_quotes.py)
        facts.append(VendorFact(
            field="earlier_quote", value=_money(e), source_type="earlier_quote", captured_at=datetime.fromisoformat(e["observed_at"]), status="old" if e["old"] else "valid",
            note="an earlier quote to someone else, for orientation only: " + ("may be out of date: " + "; ".join(e["old"]) if e["old"] else f"it held until {e['expires_at']}")))
    for d in sorted((d for d in drafts if d.kind == "request"), key=lambda d: d.created_at, reverse=True):
        facts.append(VendorFact(field="request", value=f"{d.channel} to {d.recipient}", source_type="app_request", source_ref=d.draft_id, captured_at=d.sent_at or d.created_at, status=d.status, user_confirmed=d.approved_by == "user" and d.status in ("approved", "sending", "sent")))
    for m in sorted(replies, key=lambda m: m.received_at, reverse=True):
        a = m.analysis
        said = "; ".join([f"INR {x.low_inr:,}" + (f" to {x.high_inr:,}" if x.high_inr != x.low_inr else "") for x in a.amounts] + (["says it is available"] if a.says_available else []) + (["says it is NOT available"] if a.says_unavailable else [])) or "no price or answer read"
        facts.append(VendorFact(field="reply", value=said, source_type="vendor_reply", source_ref=m.message_id, captured_at=m.received_at, status="received", evidence=_snippet(m.body), note="the vendor's own words; read by rules, not confirmed"))
    live: dict[str, set[str]] = {}
    for f in facts:
        if f.field in CONTACT_FIELDS and f.status != "rejected":
            live.setdefault(f.field, set()).add(f.value.casefold())
    facts = [f.model_copy(update={"conflict": True}) if f.field in live and f.status != "rejected" and len(live[f.field]) > 1 else f for f in facts]
    return VendorCard(
        vendor_id=biz.vendor_id, category=item.category, origin=biz.origin, source=biz.source,
        place_id=biz.external_id.split(":", 1)[1] if biz.source == "gplaces" else None, name=biz.name, name_confirmed=biz.name_source != "placeholder",
        label=f"TEST VENDOR (address owned by {biz.owner_confirmed_by})" if biz.is_test else (f"Added by {biz.owner_confirmed_by}" if biz.source == "user" else None), blocked=item.blocked, facts=tuple(facts), contact_ready=bool(k and (k.email or k.whatsapp)),
    )
