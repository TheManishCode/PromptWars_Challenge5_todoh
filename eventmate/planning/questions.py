"""An open question about something the user wrote, and the small formatters every reader uses to talk about values.

When the rule reader cannot read a word safely (an allergy spelled wrongly, a budget in dollars, "2-3 lakh", a city it only half recognises) it does not guess: it
leaves the value out and records an ``Unresolved`` question in the draft. The question travels with the draft the page holds, so it is still open on the next message,
keeps "Build my plan" away, and is closed only by the user: a ``Pick`` they pressed, or a message that gives the value plainly.

This module imports nothing from the readers, so each of them (cities, allergies, diets, reading) can build these without a circular import.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from eventmate.schemas import _Model

# the facts a question can be about; "number" is a number given with no word saying what it counts
QuestionField = Literal["guest_count", "budget_total", "event_date", "city", "allergens", "dietary", "number"]
# the facts a bare answer ("100", "2 lakh", "Pune", "peanuts") can fill: the one the last question was about
EXPECTABLE: tuple[str, ...] = ("guest_count", "budget_total", "event_date", "city", "allergens")


class Pick(_Model):
    """One answer the user can press. ``value`` None means "not that": the question is closed and nothing is set."""

    label: str = Field(min_length=1, max_length=90)
    field: QuestionField
    value: str | None = Field(default=None, max_length=60)  # a number, an ISO date, a city, an allergen or diet name; "a,b" for several allergens
    keep_words: bool = False  # for "something else": keep what the user wrote as an allergy this reader could not name, never as "no allergy"


class Unresolved(_Model):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z_]+(?::[A-Za-z' -]{1,40})?$")  # one open question per key: asking again replaces it
    field: QuestionField
    said: str = Field(min_length=1, max_length=80)  # the user's own words, so the question can quote them
    question: str = Field(min_length=1, max_length=320)
    picks: tuple[Pick, ...] = Field(default=(), max_length=6)


def indian(n: int) -> str:
    """200000 -> '2,00,000' (lakh grouping, as the review card shows money)."""
    s = str(abs(int(n)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join([*parts, tail])
    return ("-" if n < 0 else "") + s


def money(n: int) -> str:
    return "₹" + indian(n)


def long_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B %Y')}"


def clip(text: str, n: int = 60) -> str:
    """The user's words, shortened to fit a field, on one line."""
    one = " ".join(str(text).split())
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


# ------------------------------------------------------------------------------------------------------ saying what a message changed

_LABELS = {"event_type": "Event", "guest_count": "Guests", "city": "City", "event_date": "Date", "budget_total": "Budget", "venue_setting": "Setting"}
_LIST_LABELS = {"dietary": "Food", "allergens": "Allergies", "services": "Services"}


def _show(field: str, value) -> str:
    if field == "budget_total":
        return money(value)
    if field == "event_date":
        return long_date(value)
    return str(getattr(value, "value", value)).replace("_", " ").capitalize() if field in ("event_type", "venue_setting") else str(value)


def describe_changes(before, after) -> tuple[str, ...]:
    """One plain sentence for every fact that differs between two drafts: "Guests changed from 100 to 150", "City removed (was Pune)", "Food added: Vegan"."""
    out: list[str] = []
    for field, label in _LABELS.items():
        a, b = getattr(before, field), getattr(after, field)
        if a == b:
            continue
        if a is None:
            out.append(f"{label} set to {_show(field, b)}")
        elif b is None:
            out.append(f"{label} removed (was {_show(field, a)})")
        else:
            out.append(f"{label} changed from {_show(field, a)} to {_show(field, b)}")
    for field, label in _LIST_LABELS.items():
        a, b = set(getattr(before, field)), set(getattr(after, field))
        name = lambda items: ", ".join(sorted(str(getattr(i, "value", i)).replace("_", " ").capitalize() for i in items))  # noqa: E731
        if b - a:
            out.append(f"{label} added: {name(b - a)}")
        if a - b:
            out.append(f"{label} removed: {name(a - b)}")
    return tuple(out)
