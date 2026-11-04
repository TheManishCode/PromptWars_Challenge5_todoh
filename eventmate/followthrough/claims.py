"""Turn a vendor reply into price claims the user can confirm. Nothing here records a quote (ADR-025 still holds).

Rules: a claim needs exactly one clear amount whose digits really appear in the vendor's text, and a unit we can map to a basis.
Ranges, unclear units and amounts that are not in the text become a question for the user, never a claim.
A bare "66" never vouches for 66,000: a multiplier counts only when the vendor wrote it.
"""

from __future__ import annotations

import re
import uuid

from eventmate.followthrough.jobs import QuoteClaim
from eventmate.schemas import InboundMessage

_TOTAL_WORDS = re.compile(r"\b(?:in total|total|overall|flat|for the (?:whole |entire |full )?event|all[- ]inclusive)\b", re.I)
_PER_WORDS = re.compile(r"\b(?:per|a|each|/)\s?(?:plate|head|person|guest|pax)\b", re.I)
_WRITTEN = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)(?:\s?(lakhs?|lacs?|crores?|cr|k)\b)?", re.I)
_MULT = {"k": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}
_VALID_FOR = re.compile(r"\b(?:valid|validity|holds?|good|open)\s+(?:for|up to|upto|of)\s+(?:the next\s+)?(?:(\d{1,3})|(a|one|two|three|four))\s*(day|week)s?\b", re.I)
_SMALL = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_BASIS = {"per_plate": "per_plate", "per_person": "per_plate", "per_event": "total"}  # "unspecified" is deliberately absent


def amount_is_in_text(amount: int, text: str) -> bool:
    """The amount must be written out as such: 66000, 66,000, or with its own suffix (66k, 1.5 lakh). A number that only looks like it
    after a multiplier the vendor did not write (a 66 in "66 guests") does not count."""
    for m in _WRITTEN.finditer(text):
        try:
            value = float(m.group(1).replace(",", "")) * (_MULT[m.group(2).lower()] if m.group(2) else 1)
        except ValueError:
            continue
        if abs(value - amount) < 0.5:
            return True
    return False


def stated_validity_days(text: str) -> int | None:
    """How long the vendor said the price holds, in days, only if they wrote it ("valid for 10 days", "valid for two weeks"). Otherwise None: unknown."""
    m = _VALID_FOR.search(text)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else _SMALL[m.group(2).lower()]
    days = n * (7 if m.group(3).lower() == "week" else 1)
    return days if 1 <= days <= 365 else None


# Every money amount the vendor wrote, in any of the usual forms: "Rs 78,000", "INR 1.5 lakh", "₹95k", "84000 rupaye". The reply analyser knows fewer forms than people write, so this
# scan keeps a second price in the same reply from going unseen.
_MONEY_ANY = re.compile(
    r"(?:(?:rs\.?|inr|₹)\s{0,3}(?P<a>\d[\d,]{0,15}(?:\.\d{1,3})?)\s{0,2}(?P<ua>lakhs?|lacs?|crores?|cr|k)?\b|(?<![\d.,])(?P<b>\d[\d,]{0,15}(?:\.\d{1,3})?)\s{0,2}(?P<ub>lakhs?|lacs?|crores?|cr|k)?\s{0,2}(?:rupees?|rupaye|rs|inr)\b)",
    re.I,
)
_SENTENCES = re.compile(r"(?<![Rr][Ss]\.)(?<=[.!?])\s+|\n+")
# An amount the vendor names but does not offer as the price of the service: a deposit, an advance, an extra, a competitor's or last year's price, a penalty, overtime.
_BEFORE = re.compile(
    r"(?:advance|deposit|refundable|token|booking\s+amount|competitors?\w*|other\s+(?:vendors?|caterers?|quotes?)|last\s+(?:year|time|month|season)|previous\w*|earlier|cancell?ation|penalt\w+|overtime|"
    r"decor\w*|transport\w*|travel\w*|additional|extra\s+\w+|add[- ]?ons?|surcharge|late\s+fee|discount\w*|refund\w*)\W+(?:\w+\W+){0,4}$",
    re.I,
)
_AFTER = re.compile(r"^\W{0,3}(?:extra\b|more\b|as\s+(?:an?\s+)?(?:advance|deposit|token)|advance\b|deposit\b|per\s+(?:hour|km|day|extra)|an\s+hour|off\b|discount)", re.I)


def _value(num: str, unit: str | None) -> int | None:
    try:
        return round(float(num.replace(",", "")) * (_MULT[unit.lower()] if unit else 1))
    except (ValueError, OverflowError):
        return None


def _stated_amounts(body: str) -> tuple[set[int], set[int]]:
    """(amounts offered as a price, amounts named as something else) in the vendor's own words."""
    offered: set[int] = set()
    other: set[int] = set()
    for sentence in _SENTENCES.split(body):
        for m in _MONEY_ANY.finditer(sentence):
            v = _value(m.group("a") or m.group("b"), m.group("ua") or m.group("ub"))
            if v is None or v <= 0:
                continue
            (other if _BEFORE.search(sentence[max(0, m.start() - 70): m.start()]) or _AFTER.match(sentence[m.end(): m.end() + 40]) else offered).add(v)
    return offered, other


def extract_claims(msg: InboundMessage) -> tuple[list[QuoteClaim], str | None]:
    """Returns (claims, problem). ``problem`` is a plain sentence for the user when the price is not clear enough to propose."""
    amounts = msg.analysis.amounts
    if not amounts:
        return [], None
    offered, other = _stated_amounts(msg.body)
    if len(offered | other) > 1 and not any(a.low_inr != a.high_inr for a in amounts):
        return [], "The reply mentions several different amounts. Please read it and say which one is the quote."  # a price next to a deposit is still two amounts: a person says which
    if not offered and other:
        return [], "The reply names an amount, but it reads as a deposit, an extra or somebody else's price rather than the price. Please read it and say which one is the quote."
    if len(offered) == 1:
        (only,) = offered
        amounts = [a for a in amounts if a.low_inr == a.high_inr == only]
        if not amounts:
            return [], "The reply mentions an amount, but it was not clear enough to propose. Please read it and say which one is the quote."
    if len(amounts) > 1 and len({(a.low_inr, a.high_inr) for a in amounts}) > 1:
        return [], "The reply mentions several different amounts. Please read it and say which one is the quote."
    a = amounts[0]
    if a.low_inr != a.high_inr:
        return [], f"The reply gives a range (INR {a.low_inr:,} to {a.high_inr:,}), not a price."
    basis = _BASIS.get(a.unit)
    if basis is None and a.unit == "unspecified" and _TOTAL_WORDS.search(a.snippet) and not _PER_WORDS.search(a.snippet):
        basis = "total"  # the vendor wrote "total" or "for the event" next to the amount; the parser only knows explicit "per ..." units
    if basis is None:
        return [], f"The reply says INR {a.low_inr:,} but not what it covers (per plate, or the whole event?)."
    if not amount_is_in_text(a.low_inr, msg.body):
        return [], "A price was detected but its digits are not clearly in the text. Please read the reply."
    claim = QuoteClaim(claim_id=uuid.uuid4().hex[:16], message_id=msg.message_id, vendor_id=msg.vendor_id, snippet=a.snippet[:200], amount_inr=a.low_inr, basis=basis, stated_valid_days=stated_validity_days(msg.body))
    return [claim], None
