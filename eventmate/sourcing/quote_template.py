"""The quote request's fixed reply template, and a reader for what comes back in it.

A vendor is asked to answer on labelled lines ("Price per plate (INR): 650"). Labelled lines are read by rules, one field each, with the line itself kept as the evidence. A line that does not
parse safely (two amounts, a range, a number where a yes or no belongs) is ``unclear``, never guessed; a blank one is ``missing``. Anything the vendor writes outside the labels is not read
into a field at all, so an instruction hidden in a reply has no field to land in. A reply that ignores the template falls back to the existing price-claim reader (one clear amount with its
unit) and a sentence that says "available" or "not available", each again with its words as evidence. Nothing here records a quote: the user confirms each field (``QuoteConfirmationStore``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from eventmate.followthrough.claims import _MULT, amount_is_in_text, extract_claims
from eventmate.schemas import MAX_GUESTS, InboundMessage, VendorCategory

State = Literal["read", "unclear", "missing"]
MONEY = ("price", "deposit")  # an amount: confirmed one at a time, never in a batch
# field key -> (label the vendor sees, kind). The label is what is asked and what is matched: change it and old replies still read by the old text, so labels are only ever added to.
FIELDS: dict[str, tuple[str, str]] = {
    "available": ("Available on the date (yes/no)", "yesno"),
    "price": ("Price per plate (INR)", "money"),
    "price_total": ("Price for the day (INR)", "money"),
    "package": ("Package price (INR)", "money"),
    "taxes_included": ("Taxes (GST) included (yes/no)", "yesno"),
    "min_guests": ("Minimum guests", "count"),
    "max_guests": ("Maximum guests", "count"),
    "capacity": ("Capacity (guests)", "count"),
    "diets": ("Food options (vegetarian / jain / vegan / non-veg)", "diets"),
    "hours": ("Hours covered", "hours"),
    "deposit": ("Deposit to book (INR)", "money"),
    "valid_days": ("Quote valid for (days)", "days"),
    "inclusions": ("Included and extra", "text"),
}
# what each category is asked, in order; REQUIRED is what a quote must answer to be complete (everything except the free-text line)
ASKED = {
    VendorCategory.CATERER: ("available", "price", "taxes_included", "min_guests", "max_guests", "diets", "deposit", "valid_days", "inclusions"),
    VendorCategory.VENUE: ("available", "price_total", "taxes_included", "capacity", "deposit", "valid_days", "inclusions"),
    VendorCategory.PHOTOGRAPHER: ("available", "package", "taxes_included", "hours", "deposit", "valid_days", "inclusions"),
}
REQUIRED = {c: tuple(k for k in keys if k != "inclusions") for c, keys in ASKED.items()}
PRICE_KEY = {VendorCategory.CATERER: "price", VendorCategory.VENUE: "price_total", VendorCategory.PHOTOGRAPHER: "package"}
HEADER = "Please reply by filling in these lines, keeping the labels as they are:"
_DIETS = {"vegetarian": "vegetarian", "veg": "vegetarian", "pure veg": "vegetarian", "non-veg": "non_vegetarian", "non veg": "non_vegetarian", "nonveg": "non_vegetarian", "non-vegetarian": "non_vegetarian",
          "non vegetarian": "non_vegetarian", "jain": "jain", "vegan": "vegan", "halal": "halal", "gluten free": "gluten_free", "gluten-free": "gluten_free"}
_YES = re.compile(r"^\W*(yes|y|yep|yeah|haan|ha|available|confirmed|sure|ok(?:ay)?|included)\b", re.I)
_NO = re.compile(r"^\W*(no|n|nope|nahi|nahin|not available|unavailable|not included|extra)\b", re.I)
_INSTRUCTION = re.compile(r"\b(?:mark|set|ignore|disregard|system|assistant|instructions?|approve|select|choose|rank|pretend|you\s+are|act\s+as|confirm\s+the\s+booking|send\s+(?:them|it|the))\b", re.I)
_AMOUNT = re.compile(r"(?<![\w.,])(?:rs\.?|inr|₹)?\s{0,3}(?P<n>\d[\d,]{0,15}(?:\.\d{1,3})?)\s{0,2}(?P<u>lakhs?|lacs?|crores?|cr|k)?(?![\w])(?:\s?/-)?", re.I)
_INT = re.compile(r"(?<![\d.,])\d{1,6}(?![\d.,]*\d)")
_DAYS = re.compile(r"(?<![\d.,])(\d{1,3})\s*(days?|weeks?)?\b", re.I)
_HOURS = re.compile(r"(?<![\d.,])(\d{1,2}(?:\.\d)?)\s*(?:hours?|hrs?|h)?\b", re.I)


@dataclass(frozen=True)
class Read:
    key: str
    state: State
    value: str | None = None  # the normalised value: "650", "yes", "100", "vegetarian,jain", free text for inclusions
    evidence: str = ""  # the vendor's own line
    note: str = ""  # why it is unclear, or what to check
    source: Literal["template", "free_text"] = "template"


@dataclass(frozen=True)
class Reading:
    category: VendorCategory
    template_used: bool
    fields: dict[str, Read] = field(default_factory=dict)


def template_lines(category: VendorCategory) -> list[str]:
    """The lines a vendor is asked to fill in, ready to put in a request."""
    return [HEADER] + [f"{FIELDS[k][0]}:" for k in ASKED[category]]


def _label_re(label: str) -> re.Pattern:
    words = [re.escape(w) for w in label.split()]
    return re.compile(r"^[\s>*\-\u2022\d.)]*" + r"\s+".join(words) + r"\s*[:=\-\u2013]\s*(?P<v>.*)$", re.I)


_LABELS = {key: _label_re(FIELDS[key][0]) for key in FIELDS}


def _money(value: str, key: str) -> tuple[str | None, str]:
    if re.search(r"\d\s*(?:-|to|\u2013)\s*\d", value):
        return None, "that is a range, not a price"
    found = {}
    for m in _AMOUNT.finditer(value):
        raw = m.group("n")
        unit = (m.group("u") or "").lower()
        try:
            amount = float(raw.replace(",", "")) * (_MULT[unit] if unit else 1)
        except (ValueError, KeyError):
            continue
        if amount == int(amount) and amount >= 0:
            found[int(amount)] = None
    if not found and re.search(r"\b(?:nil|none|no deposit|free|zero)\b", value, re.I) and key == "deposit":
        return "0", ""
    if len(found) != 1:
        return None, "more than one amount on that line" if found else "no amount on that line"
    (amount,) = found
    if amount == 0 and key != "deposit":
        return None, "an amount of zero is not a price"
    if amount > 0 and not amount_is_in_text(amount, value):
        return None, "the digits are not clearly in the text"
    if re.search(r"\b(?:total|whole event|for the event|all[- ]inclusive)\b", value, re.I) and key == "price":
        return None, "you asked for a price per plate and the line says total"
    if re.search(r"\bper\s+(?:plate|head|person)\b", value, re.I) and key in ("price_total", "package"):
        return None, "you asked for a price for the day and the line says per plate"
    return str(amount), ""


def _parse(key: str, kind: str, value: str) -> tuple[str | None, str]:
    """(normalised value, "") or (None, why it is unclear)."""
    value = value.strip()
    if kind == "text":
        return (" ".join(value.split())[:300], "") if value else (None, "")
    if kind == "yesno":
        y, n = _YES.match(value), _NO.match(value)
        if bool(y) == bool(n):
            return None, "answer yes or no"
        return ("yes" if y else "no"), ""
    if kind == "money":
        return _money(value, key)
    if kind == "count":
        nums = _INT.findall(value)
        if len(nums) != 1:
            return None, "one number, please" if nums else "no number on that line"
        n = int(nums[0])
        return (str(n), "") if 1 <= n <= MAX_GUESTS else (None, "that number is not a plausible guest count")
    if kind == "hours":
        nums = _HOURS.findall(value)
        if len(nums) != 1:
            return None, "one number of hours, please"
        h = float(nums[0])
        return (nums[0], "") if 0.5 <= h <= 24 else (None, "hours must be between 0.5 and 24")
    if kind == "days":
        m = _DAYS.findall(value)
        if len(m) != 1:
            return None, "one number of days, please"
        n = int(m[0][0]) * (7 if (m[0][1] or "").lower().startswith("week") else 1)
        return (str(n), "") if 1 <= n <= 365 else (None, "validity must be between 1 and 365 days")
    if kind == "diets":
        low = " ".join(re.sub(r"[/,;&]|\band\b|\bor\b", " , ", value.lower()).split())
        got = []
        for phrase in sorted(_DIETS, key=len, reverse=True):
            if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", low) and _DIETS[phrase] not in got:
                got.append(_DIETS[phrase])
                low = re.sub(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", " ", low)
        if not got:
            return None, "no food option I know on that line"
        return ",".join(sorted(got)), ""
    return None, "unknown field"


def _sentence(body: str, pattern: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n+", body):
        if re.search(pattern, s, re.I):
            return " ".join(s.split())[:200]
    return ""


def read_message(msg: InboundMessage, category: VendorCategory) -> Reading:
    """What a reply says, field by field. The reply is never changed; every value carries the line it was read from."""
    asked = ASKED[category]
    out: dict[str, Read] = {}
    lines = list(msg.body.splitlines())
    for key in asked:
        label_re, kind = _LABELS[key], FIELDS[key][1]
        hits = [(ln, m.group("v").strip()) for ln in lines if (m := label_re.match(ln.strip()))]
        filled = [(ln, v) for ln, v in hits if v]
        if not filled:
            out[key] = Read(key, "missing", evidence="", note="left blank" if hits else "not answered")
            continue
        parsed = [(ln, v, *_parse(key, kind, v)) for ln, v in filled]
        values = {p[2] for p in parsed if p[2] is not None}
        if len(values) > 1:
            out[key] = Read(key, "unclear", evidence=" | ".join(" ".join(p[0].split())[:120] for p in parsed)[:300], note="the reply gives this line more than one answer")
        elif values:
            ln = next(p for p in parsed if p[2] is not None)
            out[key] = Read(key, "read", value=ln[2], evidence=" ".join(ln[0].split())[:200])
        else:
            ln = parsed[0]
            out[key] = Read(key, "unclear", evidence=" ".join(ln[0].split())[:200], note=ln[3] or "could not be read safely")
    template_used = any(r.state != "missing" for r in out.values())
    if not template_used:  # the vendor ignored the template: read what the free text clearly says, and nothing more
        from eventmate.messaging.conversations import analyze_reply  # here, not at the top: the conversations module reads this one

        clean = "\n".join(ln for ln in lines if not ln.lstrip().startswith(">") and not any(r.match(ln.strip()) for r in _LABELS.values()))  # not our own quoted request
        own = msg.model_copy(update={"body": clean, "analysis": analyze_reply(clean, ())})
        claims, problem = extract_claims(own)
        pk = PRICE_KEY[category]
        want = "per_plate" if category == VendorCategory.CATERER else "total"
        if claims and claims[0].basis == want:
            out[pk] = Read(pk, "read", value=str(claims[0].amount_inr), evidence=claims[0].snippet, source="free_text", note="read from free text, not from the template")
            if claims[0].stated_valid_days and "valid_days" in out:
                out["valid_days"] = Read("valid_days", "read", value=str(claims[0].stated_valid_days), evidence=_sentence(clean, r"\bvalid|validity|holds?\b"), source="free_text")
        elif claims:
            out[pk] = Read(pk, "unclear", evidence=claims[0].snippet, source="free_text", note=f"the amount is written as {claims[0].basis.replace('_', ' ')}; this line asks for a {'price per plate' if want == 'per_plate' else 'price for the day'}")
        elif problem:
            out[pk] = Read(pk, "unclear", source="free_text", note=problem)
        sentences = [x for x in re.split(r"(?<=[.!?])\s+|\n+", clean) if x.strip()]
        talk = [x for x in sentences if _INSTRUCTION.search(x)]  # a sentence that gives an order is not an answer, whatever words it contains
        said = [x for x in sentences if x not in talk]
        no = [x for x in said if re.search(r"\bnot\s+available|\bunavailable\b|\bbooked\b|can'?t\s+make\s+it", x, re.I)]
        yes = [x for x in said if re.search(r"(?<!not\s)(?<!un)\bavailable\b|\bfree\s+on\b", x, re.I)]
        if bool(no) != bool(yes):
            out["available"] = Read("available", "read", value="yes" if yes else "no", evidence=" ".join((yes or no)[0].split())[:200], source="free_text", note="read from free text, not from the template")
        elif not no and not yes and any(re.search(r"\bavailable\b", x, re.I) for x in talk):
            out["available"] = Read("available", "unclear", evidence=" ".join(next(x for x in talk if re.search(r"\bavailable\b", x, re.I)).split())[:200], source="free_text", note="that sentence reads like an instruction, not an answer")
    return Reading(category, template_used, out)
