"""Manager Agent: parses requests, decomposes them into a task graph, runs tasks.

Parsing is deterministic (regex + the known-city list from the database). If a
required fact is missing or ambiguous the parser raises ``RequestParseError``
instead of guessing: facts are never invented. The task graph is checked
against the three AOP principles (solvability, completeness, non-redundancy)
plus necessity (every task must feed the final explanation).
"""

from __future__ import annotations

import contextlib
import re
import unicodedata
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from pydantic import ValidationError

from eventmate.moneytext import negative_before
from eventmate.planning import allergies, diets, negation
from eventmate.planning import cities as city_aliases
from eventmate.planning.community import load_profiles, match_subtype
from eventmate.agents.budget import BudgetAgent
from eventmate.agents.guest import GuestAgent, GuestRepository
from eventmate.agents.vendor import VendorAgent, VendorRepository
from eventmate.arcade_pulse import pulse as _arcade_pulse
from eventmate.decision_logger import DecisionLogger
from eventmate.pricing.reality import reality_check
from eventmate.schemas import (
    MAX_BUDGET_INR,
    MAX_GUESTS,
    MAX_ORGANIZERS,
    AgentName,
    PlanVersion,
    Allergen,
    ConstraintCheck,
    ConstraintViolation,
    DecisionType,
    DietaryRequirement,
    EventRequest,
    CommunityDetails,
    EventType,
    GuestSummary,
    IntakeResult,
    PlanOutcome,
    ReminderKind,
    Task,
    TaskGraph,
    TaskKind,
    TaskStatus,
    VendorCategory,
    VenueSetting,
)

MAX_TEXT_CHARS = 2000
MAX_EVENT_AHEAD_DAYS = 3 * 366  # beyond this the schedule horizon and vendor data are meaningless


class RequestParseError(ValueError):
    """The request is missing required facts or is ambiguous."""

    def __init__(self, problems: Sequence[str]):
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


# ------------------------------------------------------------------ parsing

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_RE = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_YEAR = r"(?:,?\s+(20\d{2})(?!\d))?"
_DATE_MD = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*:){_YEAR}", re.I | re.A)
_DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b{_YEAR}", re.I | re.A)
# "4-6 may 2027", "7th & 8th jan", "feb 7 to 9 2027": two days of one month. Both ends are read, so the event date is asked about and no end is chosen for the user.
_RANGE_SEP = r"\s{0,2}(?:-|–|—|to|and|&)\s{0,2}"
_DATE_RANGE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?{_RANGE_SEP}(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b{_YEAR}", re.I | re.A)
_DATE_RANGE_MD = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?{_RANGE_SEP}(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\s*:){_YEAR}", re.I | re.A)
_DATE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b", re.A)
# 12/12/2026, 12-12-2026, 12.12.2026: day first (Indian convention), 4-digit year required, same separator twice
_DATE_NUMERIC = re.compile(r"(?<![\d./-])(\d{1,2})([/.-])(\d{1,2})\2(20\d{2})(?![\d/.-])", re.A)
_FULL_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_DIGIT_RUN = re.compile(r"\d+")  # unicode \d: any decimal digit, including Devanagari, Bengali, Arabic-Indic

_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MONEY = re.compile(
    rf"(?:budget(?:\s+(?:of|is|to|now|at|around|about))*[\s:]*)?(?:(?<![A-Za-z])(?P<cur>inr|rs\.?|₹|rupees)\s*)?(?<![\d,.])(?P<num>{_NUM})"
    r"(?:\s*(?P<unit>lakhs?|lacs?|crores?|cr|k|l|thousand|ha[zj]aa?r)\b)?(?:\s*(?P<cur2>inr|rs|rupees?|rupaye|rupay[ae])\b(?!\s*\d))?",  # "2027 rs 10,00,000": that "rs" marks the amount after it, not 2027
    re.I,
)
_ORGANIZERS = re.compile(r"(?<![\d,.])(\d{1,3})\s*-?\s*(?:organi[sz]ers?|coordinators?|planners?)\b", re.I | re.A)
_BUDGET_WORD = re.compile(r"budget", re.I | re.A)
_UNIT = {"k": 1_000, "lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "cr": 10_000_000, "l": 100_000,
         "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000, "hajaar": 1_000, "hajar": 1_000}

_GUESTS = [
    re.compile(r"(?<![\d,.])(\d{1,12})\s*-?\s*(?:person|persons|people|guests?|pax|ppl|heads|attendees?|log|logon|logo|mehmaan|mehman|aadmi)\b", re.I | re.A),
    re.compile(r"\bguests?(?:\s+count)?\s*(?:of|:|=)\s*(\d{1,12})\b", re.I | re.A),
    re.compile(r"\b(?:assum(?:e|ing)|plan(?:ning)?\s+for|count\s+(?:on|as)|take\s+it\s+as|go\s+with)\s+(?:about\s+|around\s+|approx(?:imately)?\s+)?(\d{1,5})(?!\d|[,.]\d|\s*%|\s*(?:lakhs?|lacs?|crores?|cr|k|l|thousand|inr|rs|rupees|days?|hours?|nights?|weeks?|months?|years?)\b)", re.I | re.A),
]
# Used only when no explicit "guests / people / pax" count exists, so "2 kids and 40 guests" is never read as a conflict.
_GUESTS_GROUP = re.compile(r"(?<![\d,.])(\d{1,12})\s*-?\s*(?:friends|relatives|invitees?|colleagues|coworkers|classmates|students|members|residents|employees|staff|ladies|gents|women|men|jana)\b", re.I | re.A)
# "a wedding for 120": a headcount with no noun, used only when nothing says guests, people or pax. Not "for 2 lakh", "for 12 Dec" or "for 3 days".
_GUESTS_FOR = re.compile(
    r"\bfor\s+(?:about\s+|around\s+|approx(?:imately)?\s+|nearly\s+)?(\d{1,5})(?!\d|[,.]\d|\s*%|\s*(?:lakhs?|lacs?|crores?|cr|k|l|thousand|ha[zj]aa?r|inr|rs|rupees|days?|hours?|nights?|mins?|minutes|weeks?|months?|years?"
    r"|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b)",
    re.I | re.A,
)
# "50 kids + 30 adults": two parts of one crowd, and the guests are their sum (a part alone, "4 kids have an egg allergy", is not matched: it has no second part)
_KID_WORD = r"(?:kids?|children|child|boys|girls|youngsters)"
_ADULT_WORD = r"(?:adults?|elders|seniors?|parents|grown[- ]?ups|men|women|ladies|gents)"
_CROWD_PARTS = [re.compile(rf"(?<![\d,.])(\d{{1,5}})\s*{x}\s*(?:\+|and|&|plus)\s*(\d{{1,5}})\s*{y}\b", re.I | re.A) for x, y in ((_KID_WORD, _ADULT_WORD), (_ADULT_WORD, _KID_WORD))]


def crowd_parts(text: str) -> tuple[int, int] | None:
    """(kids, adults) written as "50 kids + 30 adults" (either order), else None. Their sum is the guest count."""
    for rx in _CROWD_PARTS:
        if m := rx.search(text):
            return int(m.group(1)), int(m.group(2))
    return None


# a count about a need of some of the guests, not how many guests there are: "rooms for 20 outstation guests", "parking for 15"
_PART_NEED = re.compile(r"\b(?:rooms?|stay(?:ing)?|accommodat\w*|lodg\w*|hotel|outstation|out[- ]of[- ]town|travell?ing|flying|arriving|coming\s+from|pick[- ]?ups?|transport|parking|vip|speakers?|chief\s+guests?|volunteers?)\b", re.I | re.A)
_CITY_FALLBACK = re.compile(r"\b(?:in|at|near)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\b", re.A)
_NOT_CITIES = frozenset({"rs", "inr", "the", "this", "our", "my", "a", "an"})
_NEGATION = re.compile(r"\b(?:no|not|without|skip|exclude|excluding|never|don't|dont)\b", re.I | re.A)
_CLAUSE_BREAK = re.compile(r"[.;,!?\n]")

_EVENT_TYPES = [
    (re.compile(r"\bengagement\b", re.I | re.A), EventType.ENGAGEMENT_PARTY),
    (re.compile(r"\b(?:wedding|shaa?di|marriage|vivah|nikah|mehn?dee?|mehendi|sangeet|haldi|reception)\b", re.I | re.A), EventType.WEDDING),
    (re.compile(r"\bsagai\b", re.I | re.A), EventType.ENGAGEMENT_PARTY),
    (re.compile(r"\b(?:birthday|b'?day|birth\s+day)\b", re.I | re.A), EventType.BIRTHDAY),
    (re.compile(r"\bconference\b", re.I | re.A), EventType.CONFERENCE),
    (re.compile(r"\b(?:corporate|offsite|company|office\s+(?:ki\s+)?(?:party|lunch|dinner|outing|function|celebration|offsite)|annual\s+day|annual\s+function|team\s+(?:lunch|dinner|outing|party|building|celebration)|staff\s+(?:party|outing|lunch|dinner)|employees)\b", re.I | re.A), EventType.CORPORATE),
]
# Things people host that have no event type of their own: they are named, so the planner treats them as "other" on purpose and the reader does not say it failed to recognise them.
_OTHER_EVENTS = re.compile(
    r"\b(?:farewell|send[- ]?off|reunion|anniversary|pooja|puja|housewarming|house[- ]warming|griha\s*pravesh|baby\s+shower|godh\s*bharai|kitty\s+party|get[- ]?together|retirement|surprise\s+party|"
    r"tournament|diwali\s+party|new\s+year\s+party|christmas\s+party|naming\s+ceremony|namakarana|upanayana|thread\s+ceremony|baptism|satyanarayan|katha|bhajan|jagrata|party)\b", re.I | re.A)
_DIETS = [
    (re.compile(r"\b(?:non[- ]?veg(?:etarian)?|maansahari|mansahari)\b", re.I | re.A), DietaryRequirement.NON_VEGETARIAN),
    (re.compile(r"(?<!non-)(?<!non )\b(?:veg(?:etarian)?|veggie|shakahari|shaakahaari)\b|\b(?:doesn'?t|does\s+not|don'?t|do\s+not)\s+eat\s+(?:any\s+)?(?:meat|non[- ]?veg|chicken|mutton)\b", re.I | re.A), DietaryRequirement.VEGETARIAN),
    (re.compile(r"\bvegan\b", re.I | re.A), DietaryRequirement.VEGAN),
    (re.compile(r"\bjain\b", re.I | re.A), DietaryRequirement.JAIN),
    (re.compile(r"\bhal+a+l\b", re.I | re.A), DietaryRequirement.HALAL),
    (re.compile(r"\bgluten[- ]free\b", re.I | re.A), DietaryRequirement.GLUTEN_FREE),
]
_CATERER = re.compile(r"\b(?:cater(?:er|ers|ing)?|food|meals?)\b", re.I | re.A)
_PHOTO = re.compile(r"\bphoto(?:grapher|graphy|graphers)?\b", re.I | re.A)
_REMINDER = re.compile(r"\breminders?\b", re.I | re.A)
_REM_INVITE = re.compile(r"\binvit(?:e|es|ation|ations)\b", re.I | re.A)
_REM_VENDOR = re.compile(r"\bvendor(?:s)?\s+confirm(?:ation|ations)?\b|\bconfirm(?:ation|ing)?\s+(?:with\s+|of\s+)?vendors?\b", re.I | re.A)
_INDOOR = re.compile(r"\bindoors?\b", re.I | re.A)
_OUTDOOR = re.compile(r"\boutdoors?\b", re.I | re.A)


def parse_allergens(text: str) -> tuple[Allergen, ...]:
    return allergies.read(text).allergens


def _mentioned(rx: re.Pattern[str], text: str) -> bool:
    """True if ``rx`` matches somewhere that is not negated ("no", "without", ...) just before it; see ``planning.negation``."""
    return negation.mentioned(rx, text)


# Digit scripts of Indian languages. Anything else non-ASCII (e.g. Arabic-Indic) stays unread and fails closed.
_INDIC_DIGITS = ("DEVANAGARI", "BENGALI", "GURMUKHI", "GUJARATI", "ORIYA", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM")


def _is_indic_digit(c: str) -> bool:
    return unicodedata.name(c, "").startswith(_INDIC_DIGITS)


def _parse_organizers(text: str, problems: list[str]) -> int:
    counts = {int(m.group(1)) for m in _ORGANIZERS.finditer(text)}
    if len(counts) > 1:
        problems.append("multiple different organizer counts found: " + ", ".join(str(n) for n in sorted(counts)))
        return 1
    n = next(iter(counts), 1)
    if not 1 <= n <= MAX_ORGANIZERS:
        problems.append(f"organizer count must be between 1 and {MAX_ORGANIZERS}, got {n}")
        return 1
    return n


def _ascii_digits(text: str, problems: list[str], assumptions: list[str]) -> str:
    """Read Indic digits as numbers (locale pack). A single number mixing digit scripts is an error, never a guess."""
    changed = False

    def convert(m: re.Match[str]) -> str:
        nonlocal changed
        run = m.group(0)
        if run.isascii() or not all(c.isascii() or _is_indic_digit(c) for c in run):
            return run
        if len({ord(c) - unicodedata.digit(c) for c in run}) > 1:
            problems.append(f"a number mixes digit scripts: {run!r}")
            return run
        changed = True
        return "".join(str(unicodedata.digit(c)) for c in run)

    out = _DIGIT_RUN.sub(convert, text)
    if changed:
        assumptions.append("Indic digits were read as their decimal values")
    return out


def _parse_dates(text: str, today: date, problems: list[str], assumptions: list[str]) -> date | None:
    raw: list[tuple[int | None, int, int]] = []
    for m in _DATE_NUMERIC.finditer(text):
        day, month = int(m.group(1)), int(m.group(3))
        if day <= 12 and month <= 12 and day != month:
            year = int(m.group(4))
            day_first, month_first = f"{day} {_FULL_MONTHS[month - 1]} {year}", f"{month} {_FULL_MONTHS[day - 1]} {year}"
            problems.append(f"ambiguous date {m.group(0)!r} (day-first or month-first); write it as {day_first} or {month_first}")
            return None
        raw.append((int(m.group(4)), month, day))
    ranges = [m.span() for rx in (_DATE_RANGE_DM, _DATE_RANGE_MD) for m in rx.finditer(text)]
    inside = lambda m: any(a <= m.start() and m.end() <= b for a, b in ranges)  # noqa: E731  a day a range already read, with the range's own year
    for m in _DATE_MD.finditer(text):
        if not inside(m):
            raw.append((int(m.group(3)) if m.group(3) else None, _MONTHS[m.group(1)[:3].lower()], int(m.group(2))))
    for m in _DATE_DM.finditer(text):
        if not inside(m):
            raw.append((int(m.group(3)) if m.group(3) else None, _MONTHS[m.group(2)[:3].lower()], int(m.group(1))))
    for m in _DATE_RANGE_DM.finditer(text):  # "4-6 may 2027": both days of the one month, in the one year
        year, month = (int(m.group(4)) if m.group(4) else None), _MONTHS[m.group(3)[:3].lower()]
        raw += [(year, month, int(m.group(1))), (year, month, int(m.group(2)))]
    for m in _DATE_RANGE_MD.finditer(text):  # "feb 7 to 9 2027"
        year, month = (int(m.group(4)) if m.group(4) else None), _MONTHS[m.group(1)[:3].lower()]
        raw += [(year, month, int(m.group(2))), (year, month, int(m.group(3)))]
    for m in _DATE_ISO.finditer(text):
        raw.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    found = set()
    for year, month, day in raw:
        if year is not None:
            try:
                d = date(year, month, day)
            except ValueError:
                problems.append(f"invalid calendar date {year}-{month:02d}-{day:02d}")
                return None
            if d < today:
                problems.append(f"event date {d.isoformat()} is in the past")
                return None
            found.add(d)
        else:
            d = next(
                (date(y, month, day) for y in range(today.year, today.year + 9) if _valid(y, month, day) and date(y, month, day) >= today),
                None,
            )
            if d is None:
                problems.append(f"invalid calendar date {month:02d}-{day:02d}")
                return None
            found.add(d)
            assumptions.append(f"year not given; assumed next occurrence {d.isoformat()}")
    if not found:
        problems.append("event date not found")
        return None
    if len(found) > 1:
        problems.append("multiple different event dates found: " + ", ".join(sorted(d.isoformat() for d in found)))
        return None
    chosen = next(iter(found))
    if (chosen - today).days > MAX_EVENT_AHEAD_DAYS:
        problems.append(f"event date {chosen.isoformat()} is more than 3 years away")
        return None
    return chosen


def _valid(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


_POST_BUDGET = re.compile(r"(?<!\s)\s+budget\b", re.I | re.A)  # not starting inside a run of spaces: a search over a long run stays linear
_MONTH_BEFORE = re.compile(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*$", re.I | re.A)


def _stands_for_budget(text: str, m: re.Match) -> bool:
    """A number written before the word "budget" ("1,80,000 budget"). Not when it is part of a date ("December 12 budget 5L", "2027-02-10 budget 5L") or a year."""
    if not _POST_BUDGET.match(text, m.end()):  # looked at, not consumed: the word may be the cue for the number that follows it
        return False
    before, num = text[max(0, m.start("num") - 24): m.start("num")], m.group("num")  # a few characters are enough to see a month or a dash, and a whole prefix per match is quadratic
    if before[-1:] in ("-", "/") or _MONTH_BEFORE.search(before):
        return False
    return not (len(num) == 4 and num.isdigit() and 1900 <= int(num) <= 2100)


def _blank_dates(text: str) -> str:
    """``text`` with each date written in it replaced by spaces of the same length: the numbers of a date (day, year) belong to the date and are no other fact's candidate."""
    spans = [m.span() for rx in (_DATE_NUMERIC, _DATE_RANGE_DM, _DATE_RANGE_MD, _DATE_MD, _DATE_DM, _DATE_ISO) for m in rx.finditer(text)]
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


# An amount the user says was already paid ("rs 40,000 as advance", "advance of 25000 paid", "20000 is already paid to the hall") is money spent, not the total budget.
_PAID_AMT = rf"(?P<amt>(?:(?<![A-Za-z])(?:rs\.?|inr|₹|rupees?)\s{{0,2}})?(?:{_NUM})(?:\s{{0,2}}(?:lakhs?|lacs?|crores?|cr|k|thousand|l)\b)?(?:\s{{0,2}}(?:rs|inr|rupees?)\b)?)"
_PAID_WORD = r"(?:paid|advance|advanced|deposit|deposited|token|booking(?:\s+amount)?)"
_PAID_BEFORE = re.compile(rf"\b{_PAID_WORD}(?:\s+(?:of|amount\s+of|is|was|as|:))?\s{{0,2}}{_PAID_AMT}", re.I | re.A)
_PAID_AFTER = re.compile(rf"(?<![\d,.]){_PAID_AMT}(?:\s+(?:is|was|has\s+been|have\s+been|already|as|towards|for\s+the|given\s+as)){{0,3}}\s+(?:already\s+)?{_PAID_WORD}\b", re.I | re.A)


def _paid_spans(text: str) -> list[tuple[int, int, str]]:
    return sorted({(m.start(), m.end(), m.group("amt").strip()) for rx in (_PAID_BEFORE, _PAID_AFTER) for m in rx.finditer(text)})


def _blank_paid(text: str) -> str:
    """``text`` with each amount that was already paid replaced by spaces: it is not a candidate for the budget."""
    spans = _paid_spans(text)
    if not spans:
        return text
    chars = list(text)
    for start, end, _ in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def paid_amounts(text: str) -> list[int]:
    """The rupee amounts the user wrote as already paid (advance, deposit, token), so the review can say they were not used as the budget."""
    out = []
    for _, _, amt in _paid_spans(text):
        m = _MONEY.search(amt)
        try:
            value = Decimal(m.group("num").replace(",", "")) * (_UNIT[m.group("unit").lower().rstrip("s")] if m.group("unit") else 1) if m else None
        except (InvalidOperation, KeyError):
            value = None
        if value is not None and value == value.to_integral_value() and value > 0:
            out.append(int(value))
    return list(dict.fromkeys(out))


def _parse_budget(text: str, problems: list[str]) -> int | None:
    text = _blank_paid(_blank_dates(text))
    values = set()
    for m in _MONEY.finditer(text):
        marked = m.group("cur") or m.group("unit") or m.group("cur2") or _stands_for_budget(text, m) or _BUDGET_WORD.match(m.group(0))
        if not marked:
            continue
        if negative_before(text, m.start("num")):
            problems.append("a budget cannot be negative: say the amount without a minus sign")
            return None
        try:
            value = Decimal(m.group("num").replace(",", ""))
            if m.group("unit"):
                value *= _UNIT[m.group("unit").lower().rstrip("s")]
        except (InvalidOperation, KeyError):
            problems.append("budget amount is not a valid number")
            return None
        if value != value.to_integral_value():
            problems.append("budget must be a whole number of rupees")
            return None
        values.add(int(value))
    if not values:
        problems.append("budget not found (e.g. 'under INR 200,000')")
        return None
    if len(values) > 1:
        problems.append("multiple different budgets found: " + ", ".join(f"{v:,}" for v in sorted(values)))
        return None
    value = next(iter(values))
    if not 0 < value <= MAX_BUDGET_INR:
        problems.append(f"budget must be between 1 and {MAX_BUDGET_INR:,}")
        return None
    return value


# A count says who it is about by what follows it ("3 people having an allergy", "6 guests are diabetic", "30 guests are vegetarian") or what precedes it ("some 3 people").
# An allergy or a health condition written that way is a subgroup. A diet or "some" is one only when another count is there to be the total, because "100 guests are
# vegetarian" and "some 100 people" state the total. "3 of the 100 guests have ..." names 100 as the whole the 3 is counted from.
_DIET_BEFORE = re.compile(r"(?:\bveg\w*|\bnon[- ]?veg\w*|\bvegan|\bjain|\bhalal|\bsat+[vw]ik|\bsattvic)(?:\s+(?:food|menu|meals?|counter|thali))?\s*[(\[:~-]+\s*(?:about\s+|around\s+|approx\w*\s+)?$", re.I | re.A)  # "satvik food (~40 ppl)"
_COND = r"\bal+(?:[ea]r?|r)g\w*|\baller\w*|alerji|diabet\w*|intoleran\w*|celiac|coeliac|epipen|wheelchair"
_DIET_WORD = r"non[- ]?veg\w*|veg\w*|vegan|jain|halal|gluten[- ]?free|lactose\w*"
_PREDICATE = r"(?:(?:who|that|which)\s+)?(?:(?:are|is|have|has|having|got|with|suffer\w*|had|need\w*)\s+(?:\w+\s+){{0,3}}?)?(?:{})"
_HEALTH_AFTER = re.compile(_PREDICATE.format(_COND), re.I | re.A)
_DIET_AFTER = re.compile(_PREDICATE.format(_DIET_WORD), re.I | re.A)
_HEALTH_BEFORE = re.compile(rf"(?:{_COND})(?:\s+(?:to|for|in|of|from))?(?:\s+(?!and\b|or\b|with\b)\w+){{0,2}}\s?[:=-]?\s?$", re.I | re.A)  # "Allergic to peanuts: 2 people"
_SOME_BEFORE = re.compile(r"\bsome\b(?:\s+\w+){0,2}\s*$", re.I | re.A)
_EXCEPT_BEFORE = re.compile(r"\b(?:except(?:ing)?|apart\s+from|other\s+than|excluding|besides|barring)\s+(?:for\s+)?(?:about\s+|around\s+)?$", re.I | re.A)  # "everyone eats veg except 5 people": the 5 is the exception
_WHOLE_BEFORE = re.compile(r"\b(?:of|out\s+of|among|amongst)\s+(?:(?:the|these|our|my|all)\s+)?$", re.I | re.A)
_NEGATED_AFTER = re.compile(r"\b(?:no|not|none|never|nahi|nahin|without)\b", re.I | re.A)


class GuestCounts(NamedTuple):
    totals: frozenset[int]  # the counts that may be the number of guests in all
    subgroups: tuple[tuple[int, str], ...]  # (count, the user's words) for counts that are about some of the guests


def _guest_candidates(rx: re.Pattern[str], text: str) -> list[tuple[int, str, str]]:
    """(count, the words around it, what the words say it is: "" for a total, else "health", "diet", "some" or "part") for each match of ``rx``."""
    out = []
    for m in rx.finditer(text):
        rest = text[m.end(): m.end() + 140]  # a clause is enough to read, and the rest of a long text per match would be quadratic
        after_raw = re.split(r"[.;!?\n,\d]", rest, maxsplit=1)[0][:60]  # a comma ends the clause: "100 guests, allergic to peanuts" says nothing about the 100
        after = after_raw.lstrip()  # the spaces are skipped here, not in the patterns: no pattern starts with \s*
        said = text[m.start(): m.end() + len(after_raw)].strip()
        before = text[max(0, m.start(1) - 40): m.start(1)]
        if _WHOLE_BEFORE.search(before) or _NEGATED_AFTER.search(after[:30]):
            kind = ""
        elif _PART_NEED.search(text[max(0, m.start() - 30): m.start()] + " " + after[:40]):
            kind = "part"
        elif _DIET_BEFORE.search(before):
            kind = "diet"
        elif _HEALTH_AFTER.match(after) or _HEALTH_BEFORE.search(before):
            kind = "health"
        elif _DIET_AFTER.match(after):
            kind = "diet"
        elif _SOME_BEFORE.search(before) or _EXCEPT_BEFORE.search(before):
            kind = "some"
        else:
            kind = ""
        out.append((int(m.group(1)), said, kind))
    return out


def guest_counts(text: str) -> GuestCounts:
    """Sort the counts in ``text`` into those that may be the total number of guests and those about some of the guests, from the words around each. When the roles cannot be told
    apart the totals stay empty: a subgroup is never promoted to the total to fill the gap."""
    text = _blank_dates(text)
    if parts := crowd_parts(text):
        return GuestCounts(frozenset({sum(parts)}), ())
    explicit = [c for rx in _GUESTS for c in _guest_candidates(rx, text)]
    group = _guest_candidates(_GUESTS_GROUP, text)
    pool = explicit or group or _guest_candidates(_GUESTS_FOR, text)
    clear = [c for c in pool if not c[2]]
    if not clear and pool is explicit:
        clear, pool = [c for c in group if not c[2]], explicit + group  # "100 students ... 3 people allergic": the total is the group noun's
    if clear:
        return GuestCounts(frozenset(c[0] for c in clear), tuple((c[0], c[1]) for c in pool if c[2]))
    if any(c[2] in ("health", "part") for c in pool) or len({c[0] for c in pool if c[2] == "diet"}) > 1:
        return GuestCounts(frozenset(), tuple((c[0], c[1]) for c in pool))
    return GuestCounts(frozenset(c[0] for c in pool), ())


def _parse_guests(text: str, problems: list[str]) -> int | None:
    counts = guest_counts(text)
    values = counts.totals
    if not values:
        if counts.subgroups:
            problems.append("only some of the guests were counted (" + "; ".join(said for _, said in counts.subgroups) + "); the guest count in all was not found (e.g. '120 guests')")
        else:
            problems.append("guest count not found (e.g. '120 guests')")
        return None
    if len(values) > 1:
        problems.append("multiple different guest counts found: " + ", ".join(map(str, sorted(values))))
        return None
    value = next(iter(values))
    if not 1 <= value <= MAX_GUESTS:
        problems.append(f"guest count must be between 1 and {MAX_GUESTS:,}")
        return None
    return value


def _parse_city(text: str, known: Iterable[str], problems: list[str]) -> str | None:
    cities = sorted({c for c in known if c}, key=lambda c: (-len(c), c))
    found = {m.group(1) for m in city_aliases.AFTER_PREPOSITION.finditer(text)}  # "Bangalore", "Bengaluru" and "BLR" are one place
    for city in cities:
        if re.search(rf"\b(?:in|at|near)\s+{re.escape(city)}\b", text, re.I | re.A):
            found.add(city)
    if not found:
        found = {
            m.group(1)
            for m in _CITY_FALLBACK.finditer(text)
            if not re.fullmatch(_MONTH_RE, m.group(1).split()[0], re.I | re.A)
            and m.group(1).split()[0].lower() not in _NOT_CITIES
        }
    if not found:  # a bare name in a list ("50 students, Bangalore, veg"): only a city this product knows, never a guess
        found = {city_aliases.canonical(m.group(0)) for m in city_aliases.BARE.finditer(text)} | {c for c in cities if re.search(rf"(?<!\w){re.escape(c)}(?!\w)", text, re.I)}
    if not found:
        problems.append("city not found (e.g. 'in Pune')")
        return None
    spellings = {city_aliases.canonical(c) for c in found}
    if len(spellings) > 1:
        problems.append("multiple cities found: " + ", ".join(sorted(found)))
        return None
    (one,) = spellings
    return next((c for c in cities if city_aliases.canonical(c) == one), one)  # the spelling the vendor data uses, when it has the city


def _parse_event_type(text: str, assumptions: list[str]) -> tuple[EventType, CommunityDetails | None]:
    event_type = next((t for rx, t in _EVENT_TYPES if rx.search(text)), None)
    if event_type is not None:
        return event_type, None
    hit = match_subtype(text)  # keywords and subtypes are data (community_profiles.json), so the demo subtype is a config choice
    if hit is None:
        assumptions.append("event type not recognised; treated as 'other'")
        return EventType.OTHER, None
    subtype, keyword = hit
    if "community" in keyword:
        if subtype == load_profiles().default:
            assumptions.append(f"community subtype not specified; using '{subtype}'")
    else:
        assumptions.append(f"treated as a community event ({subtype.replace('_', ' ')}) because the text mentions '{keyword}'; check this")
    return EventType.COMMUNITY, CommunityDetails(subtype=subtype)


def _parse_setting(text: str, assumptions: list[str]) -> VenueSetting:
    wants_in, wants_out = _mentioned(_INDOOR, text), _mentioned(_OUTDOOR, text)
    if wants_in and not wants_out:
        return VenueSetting.INDOOR
    if wants_out and not wants_in:
        return VenueSetting.OUTDOOR
    assumptions.append("venue setting not specified or conflicting; any setting accepted")
    return VenueSetting.ANY


def _parse_services(text: str, assumptions: list[str]) -> tuple[list[VendorCategory], tuple]:
    services = [VendorCategory.VENUE]
    if not re.search(r"\bvenue\b", text, re.I | re.A):
        assumptions.append("a venue is always required; added to services")
    if _mentioned(_CATERER, text):
        services.append(VendorCategory.CATERER)
    if _mentioned(_PHOTO, text):
        services.append(VendorCategory.PHOTOGRAPHER)

    dietary = tuple(d for rx, d in _DIETS if _mentioned(rx, text))
    caterer_refused = bool(_CATERER.search(text)) and VendorCategory.CATERER not in services
    if dietary and VendorCategory.CATERER not in services and not caterer_refused:
        services.append(VendorCategory.CATERER)
        assumptions.append("dietary requirement given; caterer added to services")
    return services, dietary


def _parse_reminders(text: str, assumptions: list[str]) -> list[ReminderKind]:
    reminders: list[ReminderKind] = []
    if _mentioned(_REMINDER, text):
        if _mentioned(_REM_INVITE, text):
            reminders.append(ReminderKind.INVITATION)
        if _mentioned(_REM_VENDOR, text):
            reminders.append(ReminderKind.VENDOR_CONFIRMATION)
        if not reminders:
            assumptions.append("reminders mentioned but kind not recognised; none scheduled")
    return reminders


def parse_request(
    text: str,
    today: date | None = None,
    known_cities: Iterable[str] = (),
    request_id: str | None = None,
) -> EventRequest:
    """Turn free text into a validated ``EventRequest`` or raise ``RequestParseError``."""
    today = today or date.today()
    if not isinstance(text, str) or not text.strip():
        raise RequestParseError(["request text is empty"])
    if len(text) > MAX_TEXT_CHARS:
        raise RequestParseError([f"request text exceeds {MAX_TEXT_CHARS} characters"])

    raw_text = text
    text = unicodedata.normalize("NFKC", text)  # match on a canonical form; raw_text is kept verbatim
    problems: list[str] = []
    assumptions: list[str] = []
    text = _ascii_digits(text, problems, assumptions)
    guests = _parse_guests(text, problems)
    city = _parse_city(text, known_cities, problems)
    event_date = _parse_dates(text, today, problems, assumptions)
    budget = _parse_budget(text, problems)
    organizers = _parse_organizers(text, problems)
    from eventmate.planning import partial_diets  # local: that module reads this one's patterns

    _, groups = partial_diets.split(text)  # the same reading the intake conversation uses, so plain text and the review card agree
    problems.extend(f'"{g.words}" is {g.count} guests, more than the {guests} guests in the request; correct the group or the guest count' for g in groups if g.count and guests and g.count > guests)
    if problems:
        raise RequestParseError(problems)

    event_type, community = _parse_event_type(text, assumptions)
    setting = _parse_setting(text, assumptions)
    services, dietary = _parse_services(text, assumptions)
    allergy = allergies.read(text)
    diet_more = diets.read(text)
    reminders = _parse_reminders(text, assumptions)

    assumptions = list(dict.fromkeys(assumptions))
    try:
        return EventRequest(
            request_id=request_id or uuid.uuid4().hex[:16],
            raw_text=raw_text,
            event_type=event_type,
            guest_count=guests,
            city=city.strip(),
            event_date=event_date,
            budget_total=budget,
            dietary=tuple(dict.fromkeys((*dietary, *(g.diet for g in groups if g.diet)))),
            diet_groups=tuple(groups),
            allergens=allergy.allergens,
            allergy_unclear=allergy.unclear,
            diet_notes=tuple(dict.fromkeys((*diet_more.notes, *partial_diets.label_words(groups))))[:5],
            diet_unclear=diet_more.unclear,
            venue_setting=setting,
            services=tuple(services),
            reminders=tuple(reminders),
            assumptions=tuple(assumptions),
            parse_confidence=round(max(0.3, 1.0 - 0.1 * len(assumptions)), 2),
            organizers=organizers,
            community=community,
        )
    except ValidationError as exc:
        raise RequestParseError([e["msg"] for e in exc.errors()]) from exc


# ------------------------------------------------------------ decomposition

AGENT_CAPABILITIES: dict[AgentName, frozenset] = {
    AgentName.BUDGET: frozenset({TaskKind.ALLOCATE_BUDGET}),
    AgentName.GUEST: frozenset({TaskKind.TRACK_GUESTS}),
    AgentName.VENDOR: frozenset({TaskKind.SOURCE_VENDOR}),
    AgentName.SCHEDULER: frozenset({TaskKind.BUILD_SCHEDULE, TaskKind.SCHEDULE_REMINDER}),
    AgentName.PLANNER: frozenset({TaskKind.BUILD_PLAN_MODEL}),
    AgentName.VALIDATOR: frozenset({TaskKind.VALIDATE_PLAN}),
    AgentName.EXPLAINER: frozenset({TaskKind.EXPLAIN_PLAN}),
}
TERMINAL_TASK_ID = "explain_plan:event"


def _t(kind: TaskKind, subject: str, agent: AgentName, deps: Iterable[str], description: str) -> Task:
    return Task(
        id=f"{kind.value}:{subject}",
        kind=kind,
        subject=subject,
        agent=agent,
        depends_on=tuple(deps),
        description=description,
    )


def decompose(request: EventRequest) -> TaskGraph:
    """Build the minimal task DAG for this request. Only requested work appears."""
    budget = _t(TaskKind.ALLOCATE_BUDGET, "event", AgentName.BUDGET, [], "Split the budget across requested services")
    guests = _t(TaskKind.TRACK_GUESTS, "event", AgentName.GUEST, [], "Track headcount, RSVPs and dietary needs")
    tasks = [budget, guests]

    vendor_ids: list[str] = []
    venue_id = "source_vendor:venue"
    for category in request.services:
        deps = [budget.id, guests.id] if category == VendorCategory.VENUE else [budget.id, guests.id, venue_id]
        task = _t(TaskKind.SOURCE_VENDOR, category.value, AgentName.VENDOR, deps, f"Shortlist {category.value} options")
        tasks.append(task)
        vendor_ids.append(task.id)

    schedule = _t(TaskKind.BUILD_SCHEDULE, "event", AgentName.SCHEDULER, vendor_ids, "Create timeline, durations and resources")
    tasks.append(schedule)
    reminder_ids: list[str] = []
    for kind in request.reminders:
        deps = [guests.id, schedule.id] if kind == ReminderKind.INVITATION else [*vendor_ids, schedule.id]
        task = _t(TaskKind.SCHEDULE_REMINDER, kind.value, AgentName.SCHEDULER, deps, f"Schedule {kind.value} reminder")
        tasks.append(task)
        reminder_ids.append(task.id)

    model = _t(TaskKind.BUILD_PLAN_MODEL, "event", AgentName.PLANNER, [schedule.id, guests.id, budget.id, *reminder_ids], "Build PDDL / CP-SAT input")
    validate = _t(TaskKind.VALIDATE_PLAN, "event", AgentName.VALIDATOR, [model.id], "Check solver output against hard constraints")
    explain = _t(TaskKind.EXPLAIN_PLAN, "event", AgentName.EXPLAINER, [validate.id], "Write the human-readable plan")
    tasks += [model, validate, explain]
    return TaskGraph(tasks=tuple(tasks))


def verify_graph(request: EventRequest, graph: TaskGraph) -> list[ConstraintCheck]:
    """AOP principles + necessity. Structure (cycles, dupes) is enforced by ``TaskGraph``."""
    unsolvable = [
        t.id for t in graph.tasks if t.kind not in AGENT_CAPABILITIES.get(t.agent, frozenset())
    ]
    have = {(t.kind, t.subject) for t in graph.tasks}
    required = {(TaskKind.ALLOCATE_BUDGET, "event"), (TaskKind.EXPLAIN_PLAN, "event")}
    required |= {(TaskKind.SOURCE_VENDOR, c.value) for c in request.services}
    required |= {(TaskKind.SCHEDULE_REMINDER, r.value) for r in request.reminders}
    missing = sorted(f"{k.value}:{s}" for k, s in required - have)

    ids = {t.id for t in graph.tasks}
    if TERMINAL_TASK_ID in ids:
        needed = graph.ancestors(TERMINAL_TASK_ID) | {TERMINAL_TASK_ID}
        unnecessary = sorted(ids - needed)
    else:
        unnecessary = []  # already reported as missing
    return [
        ConstraintCheck(
            name="solvability",
            passed=not unsolvable,
            detail="every task is owned by an agent able to perform it"
            if not unsolvable
            else "no capable owner for: " + ", ".join(sorted(unsolvable)),
        ),
        ConstraintCheck(
            name="completeness",
            passed=not missing,
            detail="every requested service and reminder has a task" if not missing else "missing tasks: " + ", ".join(missing),
        ),
        ConstraintCheck(
            name="necessity",
            passed=not unnecessary,
            detail="every task contributes to the final explanation"
            if not unnecessary
            else "tasks not feeding the final output: " + ", ".join(unnecessary),
        ),
        ConstraintCheck(name="non_redundancy", passed=True, detail="no duplicate (kind, subject) pairs; enforced by TaskGraph"),
    ]


# ------------------------------------------------------------------ manager


class ManagerAgent:
    """Parses, decomposes, dispatches every task in dependency order and tracks completion.

    ``run_intake`` stops after sourcing (no plan). ``run`` continues through
    planning, validation, storage and explanation, and only reports ``planned``
    when the Validator passed and the plan version was written.
    """

    agent_name = AgentName.MANAGER

    def __init__(
        self,
        repo: VendorRepository,
        logger: DecisionLogger,
        budget_agent=None,
        vendor_agent=None,
        guest_agent=None,
        planner=None,
        validator=None,
        explainer=None,
        store=None,
    ):
        self.repo = repo
        self.logger = logger
        self.budget = budget_agent or BudgetAgent(logger=logger)
        self.vendor = vendor_agent or VendorAgent(repo, logger)
        self.guest = guest_agent or GuestAgent(GuestRepository(), logger)
        self.planner, self.validator, self.explainer, self.store = planner, validator, explainer, store

    # ------------------------------------------------------------- intake

    def run_intake(self, text: str, today: date | None = None, guest_list_id: str | None = None) -> IntakeResult:
        start = len(self.logger.entry_ids)
        request = self._parse_and_log(text, today)
        return self.intake_request(request, guest_list_id, start)

    @staticmethod
    def _with_minimum(v: ConstraintViolation, reality) -> ConstraintViolation:
        """When the budget is what failed, say the least total that would work (the budget reality check already worked it out), so the repair is a number and not only "raise it"."""
        least = reality.suggested_minimum_budget or reality.minimum_budget_default_split
        if v.constraint in ("budget_cap", "total_budget", "category_cap") and least and "about INR" not in v.repair:
            return v.model_copy(update={"repair": f"{v.repair}. A total of about INR {least:,} would cover the cheapest vendors for everything you asked for (an estimate from the records, not a quote)"})
        return v

    def parse(self, text: str, today: date | None = None) -> EventRequest:
        """Parse and log a request without running the pipeline."""
        return self._parse_and_log(text, today)

    def intake_request(self, request: EventRequest, guest_list_id: str | None = None, start: int | None = None) -> IntakeResult:
        """Intake for an already-parsed request (a trade-off option the user chose, or a dry run)."""
        start = len(self.logger.entry_ids) if start is None else start
        graph, request, guests, allocation, shortlists = self._execute_until_sourcing(request, guest_list_id)
        assessment = self.budget.assess(request, allocation, shortlists)
        reality = reality_check(request, self.repo, self.budget.policy)
        self._log_reality(request, reality)
        violations: list[ConstraintViolation] = [v for s in shortlists.values() for v in s.violations]
        violations += [v for v in assessment.violations if v not in violations]
        violations += [ConstraintViolation(constraint=c.name, detail=c.detail, repair="raise the guest count or prune the guest list") for c in guests.checks if not c.passed]
        violations = [self._with_minimum(v, reality) for v in violations]
        return IntakeResult(
            request=request,
            task_graph=graph,
            budget_allocation=allocation,
            vendor_shortlists=tuple(shortlists[c] for c in request.services),
            budget_assessment=assessment,
            guests=guests,
            reality=reality,
            status="needs_repair" if violations or assessment.status in ("overspend", "incomplete") else "intake_complete",
            violations=tuple(violations),
            decision_entry_ids=tuple(self.logger.entry_ids[start:]),
        )

    def _log_reality(self, request: EventRequest, reality) -> None:
        self.logger.log(
            request_id=request.request_id,
            agent=AgentName.BUDGET,
            decision_type=DecisionType.ASSESSMENT,
            summary=f"Budget reality check: {reality.verdict}",
            constraints_checked=(ConstraintCheck(name="budget_reality", passed=reality.verdict in ("comfortable", "tight"), detail=reality.message),),
            justification=reality.basis,
            confidence=0.7 if reality.verdict != "unknown" else 0.3,
            source=reality.source,
        )

    def _parse_and_log(self, text: str, today: date | None) -> EventRequest:
        _arcade_pulse.stage_started("parse")
        try:
            request = parse_request(text, today=today, known_cities=self.repo.cities())
        except RequestParseError as exc:
            _arcade_pulse.stage_done("parse")
            self.logger.log(
                request_id="unparsed",
                agent=self.agent_name,
                decision_type=DecisionType.FAILURE,
                summary="Request could not be parsed",
                constraints_checked=tuple(ConstraintCheck(name="parse", passed=False, detail=p) for p in exc.problems[:10]),
                justification="Required facts were missing or ambiguous; nothing was guessed. Ask the user to clarify: "
                + "; ".join(exc.problems[:10]),
                confidence=1.0,
                source="user_input",
            )
            raise
        self.logger.log(
            request_id=request.request_id,
            agent=self.agent_name,
            decision_type=DecisionType.PARSE,
            summary=f"Parsed {request.event_type.value} for {request.guest_count} guests in {request.city} on {request.event_date.isoformat()}",
            constraints_checked=(
                ConstraintCheck(name="required_fields", passed=True, detail="guests, city, date and budget all present and unambiguous"),
            ),
            justification="Facts extracted verbatim from the user's text by deterministic rules. "
            + ("Assumptions: " + "; ".join(request.assumptions) if request.assumptions else "No assumptions were needed."),
            confidence=request.parse_confidence,
            source="user_input",
        )
        _arcade_pulse.stage_done("parse")
        return request

    def _execute_until_sourcing(self, request: EventRequest, guest_list_id: str | None):
        _arcade_pulse.stage_started("decompose")
        graph = decompose(request)
        checks = verify_graph(request, graph)
        failed = [c for c in checks if not c.passed]
        self.logger.log(
            request_id=request.request_id,
            agent=self.agent_name,
            decision_type=DecisionType.FAILURE if failed else DecisionType.DECOMPOSITION,
            summary=f"Decomposed into {len(graph.tasks)} tasks",
            constraints_checked=tuple(checks),
            justification="Tasks are created only for requested services and reminders, each assigned to the agent "
            "whose capabilities cover it.",
            confidence=1.0,
            source="rule:decompose",
        )
        if failed:
            raise RuntimeError("internal error: decomposition violates " + ", ".join(c.name for c in failed))
        _arcade_pulse.stage_done("decompose")

        allocation, guests = None, None
        shortlists: dict[VendorCategory, object] = {}
        for task in graph.topological_order():
            if not all(graph.get(d).status == TaskStatus.DONE for d in task.depends_on):
                continue
            if task.kind == TaskKind.ALLOCATE_BUDGET:
                _arcade_pulse.stage_started("budget")
                allocation = self.budget.allocate(request)
                _arcade_pulse.stage_done("budget")
            elif task.kind == TaskKind.TRACK_GUESTS:
                _arcade_pulse.stage_started("guests")
                guests = self.guest.summarize(request, guest_list_id)
                request = self._with_guest_diets(request, guests)
                _arcade_pulse.stage_done("guests")
            elif task.kind == TaskKind.SOURCE_VENDOR:
                _arcade_pulse.stage_started("vendors")
                category = VendorCategory(task.subject)
                shortlists[category] = self.vendor.shortlist_category(request, category, allocation.cap_for(category))
            else:
                continue  # later stages are executed by ``run``
            graph = graph.with_status(task.id, TaskStatus.DONE)
        _arcade_pulse.stage_done("vendors")
        return graph, request, guests, allocation, shortlists

    def _with_guest_diets(self, request: EventRequest, guests: GuestSummary) -> EventRequest:
        added = [d for d in guests.required_diets if d not in request.dietary]
        if not added:
            return request
        note = "dietary needs from the guest list added to the caterer constraints: " + ", ".join(d.value for d in added)
        return request.with_changes(dietary=tuple(request.dietary) + tuple(added), assumptions=tuple(request.assumptions) + (note,))

    # ------------------------------------------------------------ full run

    def _save(self, plan: PlanVersion) -> None:
        """Write version 1. The decision log already says the plan was chosen (the plan embeds those entry ids), so a failed write is logged before it is raised."""
        try:
            self.store.save(plan)
        except OSError:
            with contextlib.suppress(Exception):
                self.logger.log(request_id=plan.request.request_id, agent=self.agent_name, decision_type=DecisionType.FAILURE,
                                summary=f"Plan {plan.plan_id} could not be written", justification="the storage failed; no plan exists and nothing was changed", confidence=1.0, source="rule:manager")
            raise

    def _explain_after_save(self, plan: PlanVersion) -> None:
        """The plan is already saved and its explanation is derived (``EventMate.explain`` rebuilds it on demand), so a failure here must not turn a saved plan into an error."""
        try:
            self.explainer.explain(plan)
        except Exception as exc:
            with contextlib.suppress(Exception):
                self.logger.log(request_id=plan.request.request_id, agent=self.agent_name, decision_type=DecisionType.FAILURE,
                                summary=f"The explanation for plan {plan.plan_id} could not be written ({type(exc).__name__})",
                                justification="the plan is saved and valid; the explanation is rebuilt when it is asked for", confidence=1.0, source="rule:manager")

    def run(self, text: str, today: date | None = None, guest_list_id: str | None = None, plan_id: str | None = None) -> PlanOutcome:
        """Parse -> decompose -> source -> plan -> validate -> store -> explain."""
        self._require_pipeline()
        start = len(self.logger.entry_ids)
        today = today or date.today()
        request = self._parse_and_log(text, today)
        return self._run_from(request, today, guest_list_id, plan_id, start, "initial plan", persist=True)

    def run_request(
        self,
        request: EventRequest,
        today: date | None = None,
        guest_list_id: str | None = None,
        plan_id: str | None = None,
        change_reason: str = "initial plan",
        persist: bool = True,
    ) -> PlanOutcome:
        """Same pipeline for an already-parsed request. ``persist=False`` is a dry run: nothing is stored."""
        self._require_pipeline()
        # the request was already parsed (intake conversation or a chosen trade-off)
        _arcade_pulse.stage_started("parse")
        _arcade_pulse.stage_done("parse")
        return self._run_from(request, today or date.today(), guest_list_id, plan_id, len(self.logger.entry_ids), change_reason, persist)

    def _require_pipeline(self) -> None:
        if None in (self.planner, self.validator, self.explainer, self.store):
            raise RuntimeError("ManagerAgent.run needs planner, validator, explainer and store")

    def _run_from(self, request, today, guest_list_id, plan_id, start, change_reason, persist) -> PlanOutcome:
        from datetime import datetime  # local: only the full pipeline needs wall-clock origin

        from eventmate.plan_store import assemble_plan, new_plan_id

        intake = self.intake_request(request, guest_list_id, start)
        graph = intake.task_graph
        request = intake.request

        def outcome(violations, plan=None):
            return PlanOutcome(
                status="planned" if plan is not None else "needs_repair",
                intake=intake,
                task_graph=graph,
                plan=plan,
                violations=tuple(violations),
                decision_entry_ids=tuple(self.logger.entry_ids[start:]),
            )

        if intake.status != "intake_complete":
            return outcome(intake.violations or [ConstraintViolation(constraint="intake", detail=intake.budget_assessment.status, repair="see violations")])

        plan_id = plan_id or new_plan_id()
        origin = datetime.combine(today, datetime.min.time())
        planned = self.planner.plan(request, intake.guests, intake.budget_allocation, intake.vendor_shortlists, origin, plan_id)
        if planned.draft is None:
            return outcome(planned.violations)
        for t in graph.tasks:
            if t.kind in (TaskKind.BUILD_SCHEDULE, TaskKind.SCHEDULE_REMINDER, TaskKind.BUILD_PLAN_MODEL):
                graph = graph.with_status(t.id, TaskStatus.DONE)

        _arcade_pulse.stage_started("validate")
        report = self.validator.validate(planned.draft)
        _arcade_pulse.stage_done("validate")
        if not report.passed:
            return outcome(report.violations)
        graph = graph.with_status("validate_plan:event", TaskStatus.DONE)

        plan = assemble_plan(planned.draft, report, 1, None, change_reason, tuple(self.logger.entry_ids[start:]))
        if persist:
            _arcade_pulse.stage_started("store")
            self._save(plan)
            self._explain_after_save(plan)
            _arcade_pulse.stage_done("store")
        graph = graph.with_status(TERMINAL_TASK_ID, TaskStatus.DONE)
        return outcome((), plan)
