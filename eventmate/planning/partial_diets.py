"""A diet said about some of the guests is not a diet for all of them.

"a few guests are vegan", "about 30% are jain", "12 of them eat only veg" say who the diet is for. The draft keeps one list of foods for the whole event, so setting it from those words
would plan every plate as vegan. Each such mention is asked about instead: everyone, or only some. "Everyone" sets the diet; "only some" keeps the user's words as a food note the
caterer must confirm in writing, and sets nothing for the rest. A diet written with no such words ("vegetarian catering", "all guests are vegetarian", "100 guests vegetarian") is unchanged.

The draft has no field for "12 of 40", so the number stays inside the user's own words and is not counted.
"""

from __future__ import annotations

import re

from eventmate.agents import manager as mgr
from eventmate.planning import negation
from eventmate.planning.questions import Pick, Unresolved, clip
from eventmate.schemas import DietaryRequirement

_F = re.I | re.A
_QUANT_WORD = re.compile(r"\b(?:some|few|several|many|most|half|couple|majority|minority|one|two|three|four|five|six|seven|eight|nine|ten)\b", _F)
# a number is about "some" only when it is followed by who it counts or what they do ("12 of them", "40 are"), or is a percentage; "budget 1 lakh vegetarian" is not
_QUANT_NUM = re.compile(r"(?<![\w.,/-])\d+(?:\.\d+)?\s?%|(?<![\w.,/%-])\d+\s+(?:of\s+(?:them|us|the|our|my|these|those)|are|is|r|will|eat|eats|prefer|prefers|need|needs|want|wants|follow|follows)\b", _F)
# words that make the quantity about food or vendors, not about guests: "a few vegan options", "two of the dishes", "one of the caterers"
_MENU = re.compile(r"\b(?:caterers?|vendors?|dish(?:es)?|items?|menus?|options?|counters?|stalls?|courses?|snacks?|starters?|desserts?|salads?|meals?|food|chefs?|kitchens?)\b", _F)
_DIET_WORDS = r"(?:non[- ]?veg\w*|veg\w*|vegan|jain|halal|gluten[- ]free)"
# "some veg and some non veg dishes": the dishes belong to both diets. The pattern starts on a word: the spaces before it are skipped in code, so a search over a run of spaces stays linear.
_MENU_AFTER = re.compile(rf"(?:(?:and|or|&)\s{{1,3}}(?:(?:some|few|a\s{{1,3}}few)\s{{1,3}})?{_DIET_WORDS}\s{{1,3}})*(?:dish(?:es)?|items?|options?|menus?|food|meals?|snacks?|starters?|courses?|counters?|stalls?|preparations?)\b", _F)
_EXCEPTION = re.compile(r"\b(?:except(?:ing)?|apart\s+from|other\s+than|excluding|barring|besides)\b", _F)  # "everyone is vegetarian except 5 people who eat chicken": not for everyone
_CLAUSE = re.compile(r"[.;,!?\n]")
KEY = "diet_group"  # "diet_group:vegan": the open question about who the diet is for


def _clause_before(text: str, start: int) -> str:
    """The words of the clause before ``start``, at most the last eight."""
    return " ".join(_CLAUSE.split(text[max(0, start - 80): start])[-1].split()[-8:])


def _clause_around(text: str, start: int, end: int) -> str:
    """The user's words about this diet: from the start of its clause to the diet word itself, so "4 of them are vegan and one girl cannot have gluten" quotes only the first part."""
    return (_CLAUSE.split(text[max(0, start - 80): start])[-1] + text[start:end]).strip()


def _about_guests(text: str, m: re.Match) -> bool:
    """False when the words around the diet make it a menu or a vendor ("some veg and some non veg dishes", "one of the caterers should be halal"), not a group of guests."""
    before = _clause_before(text, m.start())
    quants = [q for rx in (_QUANT_WORD, _QUANT_NUM) for q in rx.finditer(before)]
    since = before[max((q.start() for q in quants), default=0):]  # only the words between the quantity and the diet: a "food" in an earlier clause says nothing about it
    after = m.end()
    while after < len(text) and text[after].isspace():
        after += 1
    return not (_MENU.search(since) or _MENU_AFTER.match(text, after))


def _clause_full(text: str, start: int) -> str:
    """The whole clause the diet word is in: from the start of its clause to the next break, so an exception after the word is part of what is quoted."""
    return (_CLAUSE.split(text[max(0, start - 80): start])[-1] + _CLAUSE.split(text[start: start + 120], maxsplit=1)[0]).strip()


def split(text: str) -> tuple[tuple[DietaryRequirement, ...], list[Unresolved]]:
    """(the diets written for everyone, a question for each diet written only about some guests). A diet written both ways is set, and asked about too."""
    text = mgr._blank_dates(text)  # a date's numbers are not counts of guests
    counted = mgr.guest_counts(text).subgroups  # "40 people vegetarian" beside a total of 100 is a subgroup, not the whole event
    wide: list[DietaryRequirement] = []
    questions: list[Unresolved] = []
    for rx, diet in mgr._DIETS:
        mentions = [m for m in rx.finditer(text) if not negation.negated(text, m.start())]
        if not mentions:
            continue
        partial = [m for m in mentions if _about_guests(text, m) and (_QUANT_WORD.search(_clause_before(text, m.start())) or _QUANT_NUM.search(_clause_before(text, m.start())) or any(rx.search(said) for _, said in counted))]
        excepted = [m for m in mentions if m not in partial and _EXCEPTION.search(_clause_full(text, m.start()))]
        if len(partial) + len(excepted) < len(mentions):
            wide.append(diet)
        if partial:
            questions.append(question(diet, _clause_around(text, partial[0].start(), partial[0].end())))
        elif excepted:
            questions.append(question(diet, _clause_full(text, excepted[0].start())))
    return tuple(wide), questions


def question(diet: DietaryRequirement, said: str) -> Unresolved:
    label = diet.value.replace("_", " ")
    return Unresolved(
        key=f"{KEY}:{diet.value.replace('_', '-')}", field="dietary", said=clip(said, 60) or label,
        question=f'You wrote "{clip(said, 60)}". Is everyone {label}, or only some of the guests? A food requirement is never guessed, so please press one.',
        picks=(Pick(label=f"Everyone is {label}", field="dietary", value=diet.value), Pick(label="Only some guests (keep my words)", field="dietary", keep_words=True)),
    )


def questions(text: str) -> list[Unresolved]:
    return split(text)[1]
