"""Two things a rupee amount in free text can carry that a bare number reader drops: a minus sign, and a percent. Every parser that reads money asks here first (F3, F8).

A negative amount is never a price, a budget or a deposit, and a percentage is never a rupee amount: "25%" is not Rs 25 and "-5 lakh" is not 5 lakh.
"""

from __future__ import annotations

import re

NEGATIVE_NOTE = "a negative amount: type the amount without a minus sign"
PERCENT_NOTE = "a percentage: type the amount in rupees"

# a sign written against the number ("-5000", "−5 lakh", "-Rs 500", "minus 5000"); a hyphen after a word, a digit or a bracket is a range or a separator ("2-3 lakh", "Budget - 2 lakh"), not a sign
_SIGN_BEFORE = re.compile(r"(?:(?<![\w.,)\]])[-\u2212\uFE63\uFF0D]|\b(?:minus|negative)\b\s{1,3})(?:(?:rs\.?|inr|\u20b9|rupees?)\s{0,3})?$", re.I)
_PERCENT_AFTER = re.compile(r"^\s{0,3}(?:%|\uFF05|per\s?-?\s?cent\b|pct\b|percent\b)", re.I)


def negative_before(text: str, start: int) -> bool:
    """The number that starts at ``start`` is written with a minus sign ("-5000", "minus 5 lakh", "-Rs 500")."""
    return bool(_SIGN_BEFORE.search(text[max(0, start - 24): start]))


def percent_after(text: str, end: int) -> bool:
    """The number that ends at ``end`` is a percentage ("25%", "25 percent", "30 per cent")."""
    return bool(_PERCENT_AFTER.match(text[end: end + 12]))
