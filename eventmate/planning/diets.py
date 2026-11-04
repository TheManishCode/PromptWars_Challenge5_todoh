"""Food requirements the vendor records cannot answer (sattvic, no onion or garlic, kosher, no beef, diabetic-friendly, fasting menus...), read from the user's words.

``DietaryRequirement`` covers the diets a caterer record lists (vegetarian, vegan, jain, halal, gluten-free); those are hard checks. Everything here is a requirement the
records say nothing about, so it can be neither passed nor failed: it is kept as the user's own label, shown, and keeps the plan provisional until the caterer has confirmed
it in writing. A restriction worded in a way this reader does not know ("can't eat X", "dietary restriction: Y") is kept as ``unclear``, never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eventmate.planning import allergies

_F = re.I | re.A
_NEG = re.compile(r"\b(?:no|not|without|never|nahi|nahin|nope)\b", _F)
_BREAK = re.compile(r"[.;,!?\n]")
# (words, the label kept). A positive word is ignored when the clause before it says no/not/without ("no satvik" is not a request for it).
_POSITIVE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:satt?v(?:ik|ic)|satwik)\b", _F), "sattvic"),
    (re.compile(r"\bkosher\b", _F), "kosher"),
    (re.compile(r"\b(?:diabet\w+|sugar[- ]free|low[- ]sugar)\b", _F), "diabetic-friendly"),
    (re.compile(r"\b(?:fasting|vrat|upvas|navrat\w+|ekadashi|ramadan|ramzan|roza)\b", _F), "fasting menu"),
    (re.compile(r"\beggless\b", _F), "eggless"),
    (re.compile(r"\b(?:low[- ](?:salt|sodium)|keto|low[- ]carb)\b", _F), "low salt or low carb"),
]
# a requirement worded as an exclusion: it is the request itself, so no negation check
_EXCLUDE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:no|without|avoid|skip|zero)\s+(?:\w+\s+)?(?:onions?|garlic|pyaa?z|lehsun)\b|\b(?:onion|garlic)(?:[- ](?:and|&|or)?[- ]?(?:onion|garlic))?[- ]free\b", _F), "no onion or garlic"),
    (re.compile(r"\b(?:no|without|avoid|skip)\s+(?:\w+\s+)?beef\b|\bbeef[- ]free\b", _F), "no beef"),
    (re.compile(r"\b(?:no|without|avoid|skip)\s+(?:\w+\s+)?(?:pork|ham|bacon)\b|\bpork[- ]free\b", _F), "no pork"),
    (re.compile(r"\b(?:no|without|avoid)\s+(?:any\s+)?alcohol\b|\balcohol[- ]free\b|\bdry\s+(?:event|party|wedding)\b", _F), "no alcohol"),
]
# "dietary restriction: X" in words this reader may not know. X is kept unless it is something already read elsewhere ("can't eat X" and "avoid X" are read as allergies, which is the safer reading).
_OPEN = re.compile(
    r"\b(?:"
    r"(?:dietary|food|diet)\s+(?:restrictions?|requirements?|needs?|issues?|rules?)(?:\s*(?:is|are|:|-))?|medical\s+diet)\s+([^.;!?\n,]{1,60})",
    _F,
)
_COVERED = re.compile(
    r"\b(?:veg\w*|non|vegan|jain|halal|hallal|halaal|gluten|meat|chicken|mutton|lamb|fish|egg\w*|dairy|milk|onions?|garlic|beef|pork|alcohol|sugar|salt|oil|spicy|spice|sweets?)\b", _F
)
_LEFTOVER_STOP = allergies._STOP | {"any", "all", "anything", "things", "thing", "food", "foods", "items", "item", "type", "types", "kind", "kinds", "stuff", "dishes", "dish", "menu", "restrictions", "restriction"}
MAX_NOTES, MAX_PHRASE = 5, 60


@dataclass(frozen=True)
class DietReading:
    notes: tuple[str, ...] = ()  # the requirements the vendor records cannot answer, as fixed labels
    unclear: tuple[str, ...] = ()  # the user's own words for a restriction this reader could not place


def _negated(text: str, start: int) -> bool:
    return bool(_NEG.search(_BREAK.split(text[max(0, start - 40) : start])[-1]))


def read(text: str) -> DietReading:
    notes: list[str] = []
    unclear: list[str] = []
    for rx, label in _POSITIVE:
        if any(not _negated(text, m.start()) for m in rx.finditer(text)):
            notes.append(label)
    for rx, label in _EXCLUDE:
        if rx.search(text):
            notes.append(label)
    for m in _OPEN.finditer(text):
        phrase = m.group(1)
        if allergies._named(phrase) or _COVERED.search(phrase) or any(rx.search(phrase) for rx, _ in _POSITIVE):
            continue  # an allergen, a diet or a requirement read above: not lost
        words = [w for w in re.findall(r"[^\W\d_][\w'-]*", phrase, re.A) if w.lower() not in _LEFTOVER_STOP]
        if words:
            unclear.append(" ".join(words)[:MAX_PHRASE])
    clean = tuple(dict.fromkeys(re.sub(r"[^A-Za-z' -]", "", u).strip() for u in unclear if re.sub(r"[^A-Za-z' -]", "", u).strip()))[:MAX_NOTES]
    return DietReading(tuple(dict.fromkeys(notes))[:MAX_NOTES], clean)
