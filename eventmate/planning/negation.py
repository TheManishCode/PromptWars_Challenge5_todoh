"""Is a word the user wrote negated ("no catering", "without any photographer", "no onion no garlic vegetarian")?

A negation reaches forward only over filler and over a list joined by or/and/nor, never over another topic word: in "no onion no garlic vegetarian" the "no" belongs to garlic,
and vegetarian is a request. The earlier rule (a negation anywhere earlier in the clause) dropped it, so a stated diet disappeared without a trace.
"""

from __future__ import annotations

import re

_NEGATION = re.compile(r"(?:no|not|without|skip|exclude|excluding|never|don't|dont|nahi|nahin|nope)", re.I)
_CONJ = frozenset({"or", "and", "nor", "&", "aur", "plus"})
_BREAK = re.compile(r"[.;,!?\n]")
_TOPIC = re.compile(
    r"(?:onions?|garlic|pork|beef|ham|bacon|alcohol|veg\w*|veggie|jain|hal+a+l|vegan|gluten|non|cater\w*|food|foods|meals?|photo\w*|venue|indoors?|outdoors?|reminders?|satt?v\w*|satwik|"
    r"kosher|eggs?|eggless|nuts?|dairy|fish|meat|sugar|salt|invit\w*|vendors?|sweets?|shakahari)",
    re.I,
)
WINDOW = 4  # words looked at before the match


def negated(text: str, start: int) -> bool:
    """True if the word that starts at ``start`` in ``text`` is negated by a word shortly before it in its clause."""
    clause = _BREAK.split(text[max(0, start - 60) : start])[-1]
    prev_conj = False
    for word in reversed(re.findall(r"[\w'&]+", clause)[-WINDOW:]):
        if _NEGATION.fullmatch(word):
            return True
        if word.lower() in _CONJ:
            prev_conj = True
            continue
        if _TOPIC.fullmatch(word) and not prev_conj:
            return False  # another topic word with no "or"/"and" between: the earlier negation, if any, was about that one
        prev_conj = False
    return False


def mentioned(rx: re.Pattern[str], text: str) -> bool:
    """True if ``rx`` matches somewhere it is not negated."""
    return any(not negated(text, m.start()) for m in rx.finditer(text))
