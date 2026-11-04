"""What the rule reader will not guess, and what it reads from a bare answer.

``agents/manager.py`` reads the well-written parts of a brief (``2 lakh``, ``120 guests``, ``12 December``). This module looks at the rest of what a person types and decides, for the
facts a plan is built on, between three outcomes: a value that is plainly there, nothing at all, or something the user wrote that cannot be read *safely*. The third is never
turned into a value. It becomes an ``Unresolved`` question (see ``questions.py``) that quotes the user's words, offers what they may have meant, and stays open until they answer.

Everything here is a pure function of the text. The order the checks run in follows how costly the wrong guess is: a currency or a unit misread changes the budget by a factor of
a hundred thousand, a per-person amount read as a total changes it by the guest count, a guest count read from a range or a minus sign changes who is fed.
"""

from __future__ import annotations

import difflib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from eventmate.agents import manager as mgr
from eventmate.planning.questions import Pick, Unresolved, clip, long_date, money
from eventmate.schemas import MAX_BUDGET_INR, MAX_GUESTS

EXAMPLE_BUDGET = "For example 2 lakh or 200000."
EXAMPLE_GUESTS = "For example 120."
EXAMPLE_DATE = "For example 12 December 2026 or 12/12/2026."

# ---------------------------------------------------------------------------------------------------------- number words

_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_NUMBER_WORDS = {*_ONES, *_TENS, "hundred", "thousand", "half"}
_GLUE = {"and", "a", "an"}
_RUN = re.compile(r"\b(?:(?:" + "|".join(sorted(_NUMBER_WORDS | _GLUE, key=len, reverse=True)) + r")\b[ \t-]*)+", re.I)
_MONEY_UNIT_NEXT = re.compile(r"(?:lakhs?|lacs?|crores?|cr|k)\b", re.I)
_GUEST_NOUN_NEXT = re.compile(r"(?:guests?|people|persons?|pax|ppl|attendees?|heads)\b", re.I)
_BUDGET_WORD_BEFORE = re.compile(r"budget(?:\s+(?:of|is|to|now|at|around|about|upto|up\s+to|under))*[\s:=]*$", re.I)


def _words_value(tokens: list[str]) -> float | None:
    """"two hundred and fifty" -> 250, "two and a half" -> 2.5. None for anything that is not a number."""
    total = current = 0
    half = False
    for w in tokens:
        if w in _ONES:
            current += _ONES[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w == "thousand":
            total, current = total + (current or 1) * 1000, 0
        elif w == "half":
            half = True
        elif w not in _GLUE:
            return None
    value = total + current + (0.5 if half else 0)
    return value if value or half or "zero" in tokens else None


def number_words_to_digits(text: str) -> str:
    """"two lakh" -> "2 lakh", "a hundred guests" -> "100 guests", "budget five thousand" -> "budget 5000". A number word is changed only where a unit, a guest word or "budget" shows
    it is a quantity, so "one guest is allergic" stays as it is."""
    out, last = [], 0
    for m in _RUN.finditer(text):
        raw = list(re.finditer(r"[A-Za-z]+", m.group(0)))
        lo, hi = 0, len(raw)
        while hi > lo and raw[hi - 1].group(0).lower() in _GLUE:
            hi -= 1  # a trailing "and" or "a" belongs to the sentence
        while lo < hi and raw[lo].group(0).lower() in _GLUE and not (lo + 1 < hi and raw[lo + 1].group(0).lower() in ("hundred", "thousand", "half")):
            lo += 1
        if lo >= hi:
            continue
        tokens = [r.group(0).lower() for r in raw[lo:hi]]
        value = _words_value(tokens)
        if value is None:
            continue
        start, end = m.start() + raw[lo].start(), m.start() + raw[hi - 1].end()
        after, before = text[end:], text[:start]
        quantity = bool(_MONEY_UNIT_NEXT.match(after.lstrip())) or (value >= 10 and bool(_GUEST_NOUN_NEXT.match(after.lstrip()))) or bool(_BUDGET_WORD_BEFORE.search(before[-40:]))
        if not quantity or (len(tokens) == 1 and tokens[0] in _GLUE):
            continue
        digits = str(int(value)) if value == int(value) else str(value)
        out.append(text[last:start] + digits)
        last = end
    return "".join(out) + text[last:] if out else text


# ---------------------------------------------------------------------------------------------------------- guests

_NOUN = r"(?:persons?|people|guests?|pax|ppl|attendees?|heads|invitees?|log|mehmaan|mehman|aadmi)"
_N = r"\d[\d,]*(?:\.\d+)?"
_GUEST_RANGE = re.compile(rf"(?<![\w.,/-])(?P<a>{_N})\s*(?:-|–|—|to|and|or)\s*(?P<b>{_N})\s*-?\s*{_NOUN}\b", re.I | re.A)
_GUEST_NEGATIVE = re.compile(rf"(?<![\w.,])[-−–]\s*(?P<n>{_N})\s*-?\s*{_NOUN}\b", re.I | re.A)
_GUEST_DECIMAL = re.compile(rf"(?<![\w.,/-])(?P<n>\d+\.\d+)\s*-?\s*{_NOUN}\b", re.I | re.A)
_GUEST_ANY = re.compile(rf"(?<![\w.,/-])(?P<n>\d[\d,]*)\s*-?\s*{_NOUN}\b", re.I | re.A)


def _int(n: str) -> int:
    return int(n.replace(",", ""))


def _guest_question(said: str, why: str) -> Unresolved:
    return Unresolved(key="guest_count", field="guest_count", said=clip(said, 40), question=f'I read "{clip(said, 40)}", but {why}. How many guests are you expecting? {EXAMPLE_GUESTS}')


def screen_guests(text: str) -> Unresolved | None:
    """A guest count written in a way that has no single reading: a range, a minus sign, a fraction, zero, or more than the planner handles."""
    if m := _GUEST_RANGE.search(text):
        low, high = sorted((_int(m.group("a")), _int(m.group("b"))))
        if low != high and low >= 1 and high <= MAX_GUESTS and "." not in m.group("a") + m.group("b"):
            picks = tuple(Pick(label=f"{n} guests", field="guest_count", value=str(n)) for n in (low, high))
            return Unresolved(key="guest_count", field="guest_count", said=clip(m.group(0), 40), question="Which guest count is right?", picks=picks)
    if m := _GUEST_NEGATIVE.search(text):
        return _guest_question(m.group(0), "a guest count cannot be negative")
    if m := _GUEST_DECIMAL.search(text):
        return _guest_question(m.group(0), "a guest count has to be a whole number")
    for m in _GUEST_ANY.finditer(text):
        n = _int(m.group("n"))
        if not 1 <= n <= MAX_GUESTS:
            return _guest_question(m.group(0), f"I can plan for 1 to {MAX_GUESTS:,} guests")
    return None


# ---------------------------------------------------------------------------------------------------------- budget

_FOREIGN = {"$": "dollars", "us$": "dollars", "usd": "dollars", "dollar": "dollars", "dollars": "dollars", "€": "euros", "eur": "euros", "euro": "euros", "euros": "euros",
            "£": "pounds", "gbp": "pounds", "pound": "pounds", "pounds": "pounds", "aed": "dirhams", "dirham": "dirhams", "dirhams": "dirhams",
            "sgd": "Singapore dollars", "cad": "Canadian dollars", "aud": "Australian dollars", "chf": "Swiss francs"}
_RUPEE = {"₹", "rs", "inr", "rupee", "rupees", "rupaye"}
_CUR = r"(?:₹|rs\.?|inr|rupees?|rupaye|us\$|\$|usd|€|eur|£|gbp|aed|sgd|cad|aud|chf|dollars?|euros?|pounds?|dirhams?)"
_AMOUNT = re.compile(rf"(?:(?P<c1>{_CUR})\s*)?(?<![\w,])(?P<n>{mgr._NUM})(?P<exp>e\d+)?", re.I | re.A)
_WORD = re.compile(r"([A-Za-z]+)")  # no leading \s*: a search over a long run of spaces is quadratic; ``_at`` skips the spaces in code
_UNIT_VALUE = {"lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "cr": 10_000_000, "k": 1_000, "l": 100_000, "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000, "hajaar": 1_000, "hajar": 1_000}
_UNIT_NAME = {"lakh": "lakh", "lac": "lakh", "crore": "crore", "thousand": "thousand", "hazaar": "thousand"}  # what a near miss is offered as
_UNSUPPORTED_UNITS = {"million", "millions", "mn", "mil", "billion", "bn", "trillion", "m", "b"}
_PER_PERSON = re.compile(r"(?:(?:rs\.?|inr|₹) ?)?[/-]? ?(?:(?:per|a|an|each|every) ?-? ?(?:person|head|guest|plate|pax|people|adult|kid|child|member|delegate|student)s?\b|pp\b|pph\b)", re.I)  # spaces are single: the text is collapsed first
_RANGE_TO = re.compile(rf"(?:-|–|—|to|and|or)\s*(?:{_CUR}\s*)?(?P<n2>{mgr._NUM})\s*(?P<u2>[A-Za-z]+)?", re.I | re.A)
_BUDGET_CONTEXT = re.compile(r"budget(?:\s+(?:of|is|to|now|at|around|about|upto|up\s+to|under|max|maximum|only|between))*[\s:=]*$", re.I)
_SMALLEST_TOTAL = 1_000  # "budget 50" and "budget 2" are not a total: the unit was left off


def _at(rx: re.Pattern[str], text: str) -> re.Match[str] | None:
    """``rx`` matched at the start of ``text`` after any spaces. The spaces are skipped here, not in the pattern, so no pattern starts with a ``\\s*`` that a search could make quadratic."""
    return rx.match(text, len(text) - len(text.lstrip()))


def _unit_of(word: str) -> str | None:
    """The unit this word is, written correctly ("lakhs" -> "lakh"), else None."""
    w = word.lower()
    return w if w in _UNIT_VALUE else w[:-1] if w.endswith("s") and w[:-1] in _UNIT_VALUE else None


def _value(num: str, unit: str | None) -> int | None:
    try:
        v = Decimal(num.replace(",", "")) * (_UNIT_VALUE[unit] if unit else 1)
    except (InvalidOperation, KeyError):
        return None
    return int(v) if v == v.to_integral_value() and 0 < v <= MAX_BUDGET_INR else None


def _budget_question(said: str, why: str) -> str:
    return f'I read "{clip(said, 40)}", but {why}. What is the total budget in rupees? {EXAMPLE_BUDGET}'


def screen_budget(text: str, guests: int | None = None) -> Unresolved | None:
    """The first thing in a budget that cannot be read safely: a foreign currency, a misspelled unit, an amount per person, a range, or a number too small to be a total."""
    text = " ".join(text.split())  # one space between words, so the patterns below need no unbounded \s*
    per_person: list[Unresolved] = []
    clean_total = False
    for m in _AMOUNT.finditer(text):
        before, rest = text[max(0, m.start() - 40): m.start()], text[m.end(): m.end() + 40]
        c1, word = (m.group("c1") or "").lower().strip("."), _at(_WORD, rest)
        word_text = word.group(1) if word else ""
        unit = _unit_of(word_text)
        c2 = word_text.lower() if word_text.lower() in _FOREIGN or word_text.lower() in _RUPEE else ""
        at_budget = bool(_BUDGET_CONTEXT.search(before))
        if not (c1 or c2 or unit or at_budget):
            continue  # a number that is not money here: a date, a guest count, a phone number
        shown = clip(text[m.start(): m.end() + (word.end() if word and (unit or c2) else 0)].strip(), 40)
        currency = _FOREIGN.get(c1) or _FOREIGN.get(c2)
        if currency:
            return Unresolved(key="budget_total", field="budget_total", said=shown, question=_budget_question(shown, f"that is an amount in {currency}, and this planner works in rupees and does not convert"))
        after_unit = rest[word.end():] if word and unit else rest
        if m.group("exp"):
            return Unresolved(key="budget_total", field="budget_total", said=shown, question=_budget_question(shown, "I do not read numbers written like 1e6"))
        if (rng := _at(_RANGE_TO, after_unit)) and (u2 := _unit_of(rng.group("u2") or "")) and (unit is None or unit == u2 or _UNIT_VALUE[unit] == _UNIT_VALUE[u2]):
            low, high = _value(m.group("n"), unit or u2), _value(rng.group("n2"), u2)
            if low and high and low != high:
                picks = tuple(Pick(label=money(v), field="budget_total", value=str(v)) for v in sorted((low, high)))
                return Unresolved(key="budget_total", field="budget_total", said=clip(text[m.start(): m.end() + (word.end() if word and unit else 0) + rng.end()].strip(), 40), question="Which budget is right?", picks=picks)
        if word_text and not unit and not c2 and (c1 or at_budget):
            with_unit = clip(f"{m.group(0).strip()} {word_text}", 40)
            if word_text.lower() in _UNSUPPORTED_UNITS:
                return Unresolved(key="budget_total", field="budget_total", said=with_unit, question=_budget_question(with_unit, f'I do not know the unit "{word_text}"'))
            near = difflib.get_close_matches(word_text.lower(), list(_UNIT_NAME) + ["lakhs", "lacs", "lack", "lacks", "crores"], n=1, cutoff=0.7)
            if near and not _at(_PER_PERSON, rest):
                name = _UNIT_NAME.get(near[0].rstrip("s")) or _UNIT_NAME.get(near[0]) or "lakh"
                value = _value(m.group("n"), name)
                picks = (Pick(label=f"{m.group('n')} {name} ({money(value)})", field="budget_total", value=str(value)),) if value else ()
                why = f'I do not know the unit "{word_text}"' + (f" (did you mean {name}?)" if picks else "")
                return Unresolved(key="budget_total", field="budget_total", said=with_unit, question=_budget_question(with_unit, why), picks=picks)
        if pp := _at(_PER_PERSON, after_unit):
            rate = _value(m.group("n"), unit)
            picks: tuple[Pick, ...] = ()
            if rate and guests and rate * guests <= MAX_BUDGET_INR:
                total = rate * guests
                picks = (Pick(label=f"{money(total)} total ({guests} guests × {money(rate)})", field="budget_total", value=str(total)),)
            said = clip(text[m.start(): m.end() + (word.end() if word and unit else 0) + pp.end()].strip(), 40)
            per_person.append(Unresolved(key="budget_total", field="budget_total", said=said, question=_budget_question(said, "that is an amount for each person, not the total"), picks=picks))
            continue
        value = _value(m.group("n"), unit)
        if value is not None and unit is None and not c1 and not c2 and at_budget and value < _SMALLEST_TOTAL:
            return Unresolved(key="budget_total", field="budget_total", said=shown, question=_budget_question(shown, f"{money(value)} is too small to be the budget for an event; the unit may be missing"))
        if value is not None and (unit or c1 in _RUPEE or c2 or at_budget):
            clean_total = True
    return None if clean_total or not per_person else per_person[0]  # a stated total beside "1500 per plate" is the total


# ---------------------------------------------------------------------------------------------------------- bare answers

_FILLER = r"(?:actually|sorry|oh|no|ok|okay|well|so|now|make|change|set|update|it'?s|its|should\s+be|let'?s\s+say|say|is|be|to|it|that|this|the|number|count|total|about|around|approx(?:imately)?\.?|roughly|nearly|~)"
_LONE_NUMBER = re.compile(rf"^\W*(?:{_FILLER}\b[\s,:-]*)*(?P<n>\d[\d,]*)\W*$", re.I | re.A)
_WHOLE_COUNT = re.compile(rf"^\W*(?:{_FILLER}\b[\s,:-]*)*(?P<n>{_N})\s*(?:-?\s*{_NOUN})?\W*$", re.I | re.A)


def lone_number(text: str) -> int | None:
    """The whole message is one number with at most a few filler words ("make it 150", "now 80", "about 100"): the number, else None."""
    m = _LONE_NUMBER.match(text.strip())
    return _int(m.group("n")) if m else None


def bare_guests(text: str) -> tuple[int | None, Unresolved | None]:
    """The whole message as the answer to "how many guests?"."""
    t = number_words_to_digits(mgr._ascii_digits(text.strip(), [], []))
    if unclear := screen_guests(t if re.search(_NOUN, t, re.I) else f"{t} guests"):
        return None, unclear
    m = _WHOLE_COUNT.match(t)
    if not m or "." in m.group("n"):
        return None, None
    n = _int(m.group("n"))
    return (n, None) if 1 <= n <= MAX_GUESTS else (None, _guest_question(t, f"I can plan for 1 to {MAX_GUESTS:,} guests"))


def bare_budget(text: str, guests: int | None = None) -> tuple[int | None, Unresolved | None]:
    """The whole message as the answer to "what is the budget?": "2 lakh", "200000", "₹2,00,000", "two lakh". A bare number under ₹1,000 or in another currency is a question."""
    t = number_words_to_digits(mgr._ascii_digits(text.strip(), [], []))
    if not t or len(t) > 40 or not re.search(r"\d", t) or len(re.findall(r"[A-Za-z]+", t)) > 4:
        return None, None
    probe = t if re.search(r"budget", t, re.I) else f"budget {t}"
    if unclear := screen_budget(probe, guests):
        return None, unclear
    problems: list[str] = []
    return mgr._parse_budget(probe, problems), None


def number_question(n: int, guests: int | None, budget: int | None) -> Unresolved:
    """A bare number with nothing saying what it counts ("make it 150"): which fact is it? Never guessed."""
    picks = []
    if 1 <= n <= MAX_GUESTS:
        picks.append(Pick(label=f"{n} guests", field="guest_count", value=str(n)))
    if 1_000 <= n <= MAX_BUDGET_INR:  # under ₹1,000 is never offered as a budget (the same floor screen_budget uses)
        picks.append(Pick(label=f"{money(n)} budget", field="budget_total", value=str(n)))
    return Unresolved(
        key="number", field="number", said=str(n),
        question=(f"You wrote {n} with no word saying what it counts. Did you mean {n} guests or a budget of {money(n)}? " if len(picks) > 1 else f"You wrote {n} with no word saying what it counts. Did you mean {n} guests? ") + f"Press one, or write it out, for example {n} guests.",
        picks=tuple(picks),
    )


# ---------------------------------------------------------------------------------------------------------- dates

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_DAY_THEN_WORD = re.compile(r"\b(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<w>[A-Za-z]{4,10})\b(?:,?\s+(?P<y>20\d{2})(?!\d))?", re.A)
_RELATIVE = re.compile(
    r"\b(?:tomorrow|today|tonight|yesterday|day\s+after(?:\s+tomorrow)?|(?:next|this|coming|last)\s+(?:week(?:end)?|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:in|after)\s+\d+\s+(?:days?|weeks?|months?))\b",
    re.I | re.A,
)
_MONTH_ONLY = re.compile(r"\b(?:in|during|sometime\s+in)\s+(?P<m>" + "|".join(_MONTH_NAMES) + r")(?:\s+20\d{2})?\b(?!\s*\d)", re.I | re.A)


def month_typo(text: str, today: date) -> Unresolved | None:
    """"12 Decmber 2026": a word that is not a month but looks like one. Offered as the date it would be, and applied only when the user presses it."""
    for m in _DAY_THEN_WORD.finditer(text):
        word = m.group("w").lower()
        if re.fullmatch(mgr._MONTH_RE, word, re.I) or word in {"guests", "people", "persons", "lakh", "lakhs"}:
            continue
        near = difflib.get_close_matches(word, _MONTH_NAMES, n=1, cutoff=0.8)
        if not near:
            continue
        month, day = _MONTH_NAMES.index(near[0]) + 1, int(m.group("d"))
        years = [int(m.group("y"))] if m.group("y") else range(today.year, today.year + 3)
        found = next((date(y, month, day) for y in years if mgr._valid(y, month, day) and date(y, month, day) >= today), None)
        if found:
            return Unresolved(
                key="event_date", field="event_date", said=clip(m.group(0), 40),
                question=f'I do not recognise "{m.group("w")}" as a month. Did you mean {near[0].capitalize()}? Press the date to use it, or write the date another way. {EXAMPLE_DATE}',
                picks=(Pick(label=long_date(found), field="event_date", value=found.isoformat()),),
            )
    return None


def date_hint(text: str) -> str | None:
    """Why a date that was written could not be used, when it is a kind this reader does not work out: "tomorrow", "next Saturday", "in December"."""
    if m := _RELATIVE.search(text):
        return f'I cannot work out "{m.group(0)}" yet, so please give the date itself. {EXAMPLE_DATE}'
    if m := _MONTH_ONLY.search(text):
        return f'I need the day as well as the month ("{m.group(0).strip()}"). {EXAMPLE_DATE}'
    return None
