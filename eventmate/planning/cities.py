"""One spelling per Indian city, so "Bangalore", "Bengaluru" and "BLR" are the same place and a spelling variant never loops the intake."""

from __future__ import annotations

import re

ALIASES: dict[str, str] = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "blr": "Bengaluru",
    "bombay": "Mumbai", "mumbai": "Mumbai", "bom": "Mumbai",
    "poona": "Pune", "pune": "Pune",
    "madras": "Chennai", "chennai": "Chennai",
    "calcutta": "Kolkata", "kolkata": "Kolkata",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "baroda": "Vadodara", "vadodara": "Vadodara",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    "cochin": "Kochi", "kochi": "Kochi",
    "mysore": "Mysuru", "mysuru": "Mysuru",
    "new delhi": "Delhi", "delhi": "Delhi",
    "hyderabad": "Hyderabad", "hyd": "Hyderabad",
}
_ALTERNATION = "|".join(sorted(map(re.escape, ALIASES), key=lambda a: (-len(a), a)))
# a city written on its own, with no "in" before it: the longer spellings, any case, and the airport-style codes only in capitals (a lowercase "bom" is not Mumbai)
BARE = re.compile(rf"(?<!\w)(?:{'|'.join(sorted((a for a in ALIASES if len(a) >= 4), key=lambda a: (-len(a), a)))}|(?-i:BLR|HYD|BOM))(?!\w)", re.I)
AFTER_PREPOSITION = re.compile(rf"\b(?:in|at|near)\s+({_ALTERNATION})\b", re.I | re.A)


def canonical(name: str | None) -> str | None:
    """The one spelling for a known alias, else the name as given (stripped)."""
    if name is None:
        return None
    key = " ".join(name.split()).lower()
    return ALIASES.get(key, name.strip())
