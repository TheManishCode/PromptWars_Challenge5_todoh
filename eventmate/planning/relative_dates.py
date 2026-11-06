"""Dates written in terms of today ("next Saturday", "month end"), two dates offered as one ("19th or 20th"), and a weekday that does not match its date.

What a person means by "next Saturday" depends on who says it, so none of these is turned into a date. Each becomes an ``Unresolved`` question whose buttons are the real dates it could
be, and the date stays empty (an earlier date does not stand in) until the user presses one. A phrase that is not one day at all ("first week of January") is a question with nothing
to press. Pure functions of the text and of today's date.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from eventmate.agents import manager as mgr
from eventmate.planning.questions import Pick, Unresolved, clip

EXAMPLE_DATE = "For example 12 December 2026 or 12/12/2026."
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_NAME = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tues|thurs)"
_F = re.I | re.A


def _weekday(word: str) -> int:
    w = word.lower()
    return next(i for i, d in enumerate(_DAYS) if d.startswith(w[:3]))


def _label(d: date) -> str:
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B %Y')}"


def _question(said: str, why: str, days: list[date]) -> Unresolved:
    days = [d for d in dict.fromkeys(days) if d >= date.min]
    picks = tuple(Pick(label=_label(d), field="event_date", value=d.isoformat()) for d in days[:4])
    ask = "Press the date you mean, or write it. " if picks else "Which day is it? "
    return Unresolved(key="event_date", field="event_date", said=clip(said, 40), question=f'You wrote "{clip(said, 40)}", {why}. {ask}{EXAMPLE_DATE}', picks=picks)


def _coming(today: date, weekday: int, after: int = 0) -> date:
    """The first ``weekday`` strictly after today (today is not offered: nobody plans for today in a message), then ``after`` weeks later."""
    ahead = (weekday - today.weekday() - 1) % 7 + 1
    return today + timedelta(days=ahead + 7 * after)


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_after(today: date, n: int) -> tuple[int, int]:
    index = today.year * 12 + today.month - 1 + n
    return index // 12, index % 12 + 1


_TODAY = re.compile(r"\btonight\b", _F)
_TOMORROW = re.compile(r"\b(?:day\s+after\s+tomorrow|tomorrow|tmrw)\b", _F)
_IN_N = re.compile(r"\b(?:in|after)\s+(\d{1,3})\s+(days?|weeks?)\b", _F)
_WEEKEND = re.compile(r"\b(?P<w>this|next|coming)\s+weekend\b", _F)
_NEXT_WEEK_DAY = re.compile(rf"\b(?:next\s+week(?:'?s)?\s+(?P<d1>{_DAY_NAME})|(?P<d2>{_DAY_NAME})\s+(?:of\s+)?next\s+week)\b", _F)
_WEEKDAY = re.compile(rf"\b(?P<w>next|this|coming|on|is|ko)?\s*(?P<day>{_DAY_NAME})\b", _F)
_MONTH_END = re.compile(r"\b(?:(?P<a>end\s+of\s+(?:the\s+)?(?:(?P<n1>next)\s+|this\s+)?month)|(?P<b>(?:(?P<n2>next)\s+|this\s+)?month[- ]?end))\b", _F)
_FUZZY = re.compile(
    r"\b(?:(?:first|second|third|last|1st|2nd|3rd)\s+(?:week|half)\s+(?:of\s+)?(?:next\s+month|this\s+month|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"|(?:next\s+month|this\s+month|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(?:first|second|third|last|1st|2nd|3rd|mid|end)\s*(?:week|half)?"
    r"|(?:early|mid|late)\s+(?:next\s+month|this\s+month|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
    r"|next\s+month|next\s+week)\b",
    _F,
)


def relative(text: str, today: date) -> Unresolved | None:
    """The date written relative to today in ``text`` as a question, else None. The first phrase found is the one asked about."""
    if m := _NEXT_WEEK_DAY.search(text):
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return _question(m.group(0), "which is the week after this one (Monday to Sunday)", [monday + timedelta(days=_weekday(m.group("d1") or m.group("d2")))])
    if m := _WEEKEND.search(text):
        saturday = _coming(today, 5) if m.group("w").lower() != "next" else today - timedelta(days=today.weekday()) + timedelta(days=12)
        return _question(m.group(0), "which could be either day", [saturday, saturday + timedelta(days=1)])
    if m := _MONTH_END.search(text):
        n = 1 if (m.group("n1") or m.group("n2")) else 0
        year, month = _month_after(today, n)
        end = _last_day(year, month)
        if end < today:
            end = _last_day(*_month_after(today, n + 1))
        return _question(m.group(0), "which is not a day I can pick for you", [end])
    if m := _TOMORROW.search(text):
        return _question(m.group(0), "which depends on today's date", [today + timedelta(days=2 if "after" in m.group(0).lower() else 1)])
    if m := _IN_N.search(text):
        n = int(m.group(1)) * (7 if m.group(2).lower().startswith("week") else 1)
        return _question(m.group(0), "which depends on today's date", [today + timedelta(days=n)])
    if m := _TODAY.search(text):
        return _question(m.group(0), "which depends on today's date", [today])
    if m := _WEEKDAY.search(text):
        lead = (m.group("w") or "").lower()
        wd = _weekday(m.group("day"))
        if lead == "next":
            return _question(m.group(0), 'which could be the coming one or the one after, so please say which', [_coming(today, wd), _coming(today, wd, 1)])
        if lead in ("this", "coming", "is", "on", "ko", ""):
            return _question(m.group(0), "which depends on today's date", [_coming(today, wd)])
    if m := _FUZZY.search(text):
        return _question(m.group(0), "which is not one day", [])
    return None


# "19th or 20th December", "dec 12 or 13", "12/13 dec": two days of one month offered as one date
_MONTH = mgr._MONTH_RE
_ORD = r"(?:st|nd|rd|th)?"
_EITHER_AFTER = re.compile(rf"\b(?P<a>\d{{1,2}}){_ORD}\s*(?:or|ya|/)\s*(?P<b>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m>{_MONTH})\b(?:,?\s+(?P<y>20\d{{2}})(?!\d))?", _F)
_EITHER_BEFORE = re.compile(rf"\b(?P<m>{_MONTH})\.?\s+(?P<a>\d{{1,2}}){_ORD}\s*(?:or|ya|/)\s*(?P<b>\d{{1,2}}){_ORD}\b(?!\s*:)(?:,?\s+(?P<y>20\d{{2}})(?!\d))?", _F)
_EITHER_SPLIT = re.compile(rf"\b(?P<a>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m1>{_MONTH})\s*(?:or|ya)\s*(?P<b>\d{{1,2}}){_ORD}\s+(?:of\s+)?(?P<m2>{_MONTH})\b(?:,?\s+(?P<y>20\d{{2}})(?!\d))?", _F)


def _next(today: date, year: int | None, month: int, day: int) -> date | None:
    years = [year] if year else range(today.year, today.year + 4)
    return next((date(y, month, day) for y in years if mgr._valid(y, month, day) and date(y, month, day) >= today), None)


def either(text: str, today: date) -> Unresolved | None:
    """Two days offered as one ("maybe 19th or 20th December"): both are options, neither is the date."""
    for rx in (_EITHER_AFTER, _EITHER_BEFORE, _EITHER_SPLIT):
        if m := rx.search(text):
            groups = m.groupdict()
            year = int(groups["y"]) if groups.get("y") else None
            month_a = mgr._MONTHS[(groups.get("m") or groups["m1"])[:3].lower()]
            month_b = mgr._MONTHS[(groups.get("m") or groups["m2"])[:3].lower()] if groups.get("m") or groups.get("m2") else month_a
            days = [d for d in (_next(today, year, month_a, int(groups["a"])), _next(today, year, month_b, int(groups["b"]))) if d]
            if len(set(days)) == 2:
                days.sort()
                return Unresolved(
                    key="event_date", field="event_date", said=clip(m.group(0), 40), question="You gave two dates. Which day is it?",
                    picks=tuple(Pick(label=_label(d), field="event_date", value=d.isoformat()) for d in days),
                )
    return None


_WEEKDAY_WITH_DATE = re.compile(rf"\b(?P<d1>{_DAY_NAME})\b[\s,]+(?:the\s+|on\s+)?\d{{1,2}}\s*{_ORD}\s+(?:of\s+)?(?:{_MONTH})\b|\b(?P<d2>{_DAY_NAME})\b[\s,]+(?:{_MONTH})\.?\s+\d{{1,2}}", _F)


def weekday_mismatch(text: str, when: date | None, today: date) -> Unresolved | None:
    """"Saturday 15th Nov" when 15 November is a Sunday: the weekday and the date disagree, so one of them is wrong. Both readings are offered."""
    if when is None:
        return None
    m = _WEEKDAY_WITH_DATE.search(mgr._ascii_digits(text, [], []))
    if not m:
        return None
    said = _weekday(m.group("d1") or m.group("d2"))
    if when.weekday() == said:
        return None
    before, after = when - timedelta(days=(when.weekday() - said) % 7), when + timedelta(days=(said - when.weekday()) % 7)  # the nearest day with the weekday written, on either side
    options = sorted({d for d in (when, before, after) if d >= today})
    return Unresolved(
        key="event_date", field="event_date", said=clip(m.group(0), 40),
        question=f'You wrote "{clip(m.group(0), 40)}", but {when.day} {when.strftime("%B %Y")} is a {when.strftime("%A")}. Which is right?',
        picks=tuple(Pick(label=_label(d), field="event_date", value=d.isoformat()) for d in options[:4]),
    )


_DAY_ONLY = re.compile(r"(?<![\w/.-])(?P<d>\d{1,2})(?:st|nd|rd|th)(?![\w/.-])", _F)
_DAY_CUE = re.compile(r"\b(?:lock|locked|fix|fixed|make|shift|move|change|set|keep|final|finalise|finalize|confirm|confirmed|only|date|on)\b", _F)
_NOT_A_DATE = re.compile(r"\b(?:birthday|b'?day|anniversary|wedding|reunion|year|years|floor|time|batch|edition)\b", _F)


def day_only(text: str, anchor: date | None, today: date) -> date | None:
    """"lets lock 13th", "wait, family says 12th only": a day of the month with no month, read in the month of the date the user already gave. Needs that date and a word that fixes
    a date, and is never used for a count of years ("my 50th birthday")."""
    if anchor is None or _NOT_A_DATE.search(text) or not _DAY_CUE.search(text) and not re.search(r"\b\d{1,2}(?:st|nd|rd|th)\s+only\b", text, _F):
        return None
    days = {int(m.group("d")) for m in _DAY_ONLY.finditer(mgr._blank_dates(text))}
    if len(days) != 1 or re.search(r"\d", _DAY_ONLY.sub(" ", mgr._blank_dates(text))):
        return None
    day = days.pop()
    if not mgr._valid(anchor.year, anchor.month, day):
        return None
    found = date(anchor.year, anchor.month, day)
    return found if found >= today else None
