"""A diet written about some of the guests is a group: who needs it, and how many.

"40 are jain", "a few guests are vegan", "30% vegetarian", "half non veg half veg" say who needs a diet. The caterer must offer every diet in ``request.dietary`` (the vendor check reads it
as the options a caterer must cover), so a group's diet is in that list too, and the group keeps what was said about its size: a number when there is one, else the user's own word.
"Everyone is vegetarian except 5 who eat chicken" is a whole-event diet plus an exception group of 5 (its diet is read from what they eat; when the words name no diet this product lists,
the group has none and its words go to the caterer as a food note). Nothing is asked: changing the whole event to one diet is a separate edit, said as one ("make it vegan for everyone"),
and it replaces the group (see the intake's merge).

A diet written with no such words ("vegetarian catering", "all guests are vegetarian", "100 guests vegetarian") is for the whole event, and so is a menu word ("some veg and some non veg
dishes", "one of the caterers should be halal").
"""

from __future__ import annotations

import re

from eventmate.agents import manager as mgr
from eventmate.planning import negation
from eventmate.schemas import DietaryRequirement as D
from eventmate.schemas import DietGroup

_F = re.I | re.A
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_QUANT_WORD = re.compile(r"\b(?:some|few|several|many|most|half|couple|majority|minority|one|two|three|four|five|six|seven|eight|nine|ten)\b", _F)
# a number is about "some" only when it is followed by who it counts or what they do ("12 of them", "40 are"), or is a percentage; "budget 1 lakh vegetarian" is not
_QUANT_NUM = re.compile(r"(?<![\w.,/-])\d+(?:\.\d+)?\s?%|(?<![\w.,/%-])\d+\s+(?:of\s+(?:them|us|the|our|my|these|those)|are|is|r|will|eat|eats|prefer|prefers|need|needs|want|wants|follow|follows)\b", _F)
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%", _F)
_DIGITS = re.compile(r"(?<![\w.,/%-])(\d+)(?![\w.,/%-])", _F)
# words that make the quantity about food or vendors, not about guests: "a few vegan options", "two of the dishes", "one of the caterers"
_MENU = re.compile(r"\b(?:caterers?|vendors?|dish(?:es)?|items?|menus?|options?|counters?|stalls?|courses?|snacks?|starters?|desserts?|salads?|meals?|food|chefs?|kitchens?)\b", _F)
_DIET_WORDS = r"(?:non[- ]?veg\w*|veg\w*|vegan|jain|halal|gluten[- ]free)"
# "some veg and some non veg dishes": the dishes belong to both diets. The pattern starts on a word: the spaces before it are skipped in code, so a search over a run of spaces stays linear.
_MENU_AFTER = re.compile(rf"(?:(?:and|or|&)\s{{1,3}}(?:(?:some|few|a\s{{1,3}}few)\s{{1,3}})?{_DIET_WORDS}\s{{1,3}})*(?:dish(?:es)?|items?|options?|menus?|food|meals?|snacks?|starters?|courses?|counters?|stalls?|preparations?)\b", _F)
_EXCEPTION = re.compile(r"\b(?:except(?:ing)?|apart\s+from|other\s+than|excluding|barring|besides)\b", _F)  # "everyone is vegetarian except 5 people who eat chicken"
# what an exception group eats, when the words say it
_EXCEPTION_DIETS = (
    (re.compile(r"\b(?:chicken|mutton|meat|fish|lamb|pork|beef|prawns?|seafood|non[- ]?veg\w*)\b", _F), D.NON_VEGETARIAN),
    (re.compile(r"\bvegan\b", _F), D.VEGAN),
    (re.compile(r"\bjain\b", _F), D.JAIN),
    (re.compile(r"\bhalal\b", _F), D.HALAL),
    (re.compile(r"\bgluten", _F), D.GLUTEN_FREE),
)
_CLAUSE = re.compile(r"[.;,!?\n]")
# a new size for a group the draft already has: "30 vegan", "30 are vegan, not 25" (the number just before the diet), "vegan should be 30", "make the vegan ones 30", "change vegan to 30"
_SIZE_BEFORE = re.compile(r"(?<![\w.,/%-])(\d+)(?:\s+(?:of\s+)?(?:them|us|people|guests?|pax|persons?|ones))?(?:\s+(?:are|is|will\s+be|r))?\s*$", _F)
# matched against the words after the diet with leading space removed; each optional word owns the space after it, so no run of spaces can be split two ways
_SIZE_AFTER = re.compile(r"(?:(?:ones|guests|people|group|count)\s*)?(?:(?:should\s+be|to\s+be|to|is|are|now|=|:|->)\s*)?(\d+)(?![\w.,/%-])(?!\s*(?:lakhs?|lacs?|crores?|k\b|rs\b|inr\b|rupees|thousand|days?|hours?|am\b|pm\b|km\b))", _F)
_OLD_VALUE = re.compile(r"\b(?:not|no|instead\s+of|rather\s+than|from|was|than)\s*$", _F)  # the number after these is the value being replaced
_SAFE = re.compile(r"[^A-Za-z0-9' %.-]")


def _clause_before(text: str, start: int) -> str:
    """The words of the clause before ``start``, at most the last eight."""
    return " ".join(_CLAUSE.split(text[max(0, start - 80): start])[-1].split()[-8:])


def _clause_around(text: str, start: int, end: int) -> str:
    """The user's words about this diet: from the start of its clause to the diet word itself, so "4 of them are vegan and one girl cannot have gluten" quotes only the first part."""
    return (_CLAUSE.split(text[max(0, start - 80): start])[-1] + text[start:end]).strip()


def _clause_full(text: str, start: int) -> str:
    """The whole clause the diet word is in: from the start of its clause to the next break, so an exception after the word is part of what is quoted."""
    return (_CLAUSE.split(text[max(0, start - 80): start])[-1] + _CLAUSE.split(text[start: start + 120], maxsplit=1)[0]).strip()


def _about_guests(text: str, m: re.Match) -> bool:
    """False when the words around the diet make it a menu or a vendor ("some veg and some non veg dishes", "one of the caterers should be halal"), not a group of guests."""
    before = _clause_before(text, m.start())
    quants = [q for rx in (_QUANT_WORD, _QUANT_NUM) for q in rx.finditer(before)]
    since = before[max((q.start() for q in quants), default=0):]  # only the words between the quantity and the diet: a "food" in an earlier clause says nothing about it
    after = m.end()
    while after < len(text) and text[after].isspace():
        after += 1
    return not (_MENU.search(since) or _MENU_AFTER.match(text, after))


def _size(before: str) -> tuple[int | None, str | None, int | None]:
    """(count, share, where it starts in ``before``) from the words before a diet word: the nearest number the user wrote, else the word they used ("a few", "half", "30%"). The nearest
    one, because "40 are jain and 25 are vegan" has two."""
    found: list[tuple[int, int | None, str | None]] = []
    for m in _PERCENT.finditer(before):
        found.append((m.start(), None, m.group(0).replace(" ", "")))
    for m in _QUANT_NUM.finditer(before):
        n = _DIGITS.search(m.group(0))
        if n and int(n.group(1)) >= 1 and not _PERCENT.match(m.group(0)):
            found.append((m.start(), int(n.group(1)), None))
    for m in _QUANT_WORD.finditer(before):
        word = m.group(0).lower()
        if word in _NUMBER_WORDS:
            found.append((m.start(), _NUMBER_WORDS[word], None))
        else:
            found.append((m.start(), None, "a few" if word == "few" and re.search(r"\ba\s+few\b", before, _F) else word))
    if not found:
        return None, None, None
    at, count, share = max(found, key=lambda f: f[0])
    return count, share, at


def _words(said: str) -> str:
    return _SAFE.sub("", " ".join(said.split())).strip()[:60]


def _group(diet: D | None, count: int | None, share: str | None, exception: bool, said: str) -> DietGroup | None:
    words = _words(said)
    return DietGroup(diet=diet, count=count, share=share, exception=exception, words=words) if words else None


def split(text: str) -> tuple[tuple[D, ...], list[DietGroup]]:
    """(the diets written for the whole event, the groups written about some guests or as an exception). A diet may be both: "vegetarian for everyone and a few are vegan" has vegetarian
    for the whole event and vegan for a few."""
    text = mgr._blank_dates(text)  # a date's numbers are not counts of guests
    counted = mgr.guest_counts(text).subgroups  # "40 people vegetarian" beside a total of 120 is a group, not the whole event
    wide: list[D] = []
    groups: list[tuple[int, DietGroup]] = []  # with where in the message each was written, so they are kept in the user's order
    for rx, diet in mgr._DIETS:
        mentions = [m for m in rx.finditer(text) if not negation.negated(text, m.start())]
        if not mentions:
            continue
        partial = [
            m for m in mentions
            if _about_guests(text, m) and (_QUANT_WORD.search(_clause_before(text, m.start())) or _QUANT_NUM.search(_clause_before(text, m.start())) or any(rx.search(said) for _, said in counted))
        ]
        if len(partial) < len(mentions):
            wide.append(diet)  # a mention with no such words, or one that only has an exception, is the whole event's
        if partial:
            m = partial[0]
            before = _clause_before(text, m.start())
            count, share, at = _size(before)
            if count is None and share is None:
                count = next((n for n, said in counted if rx.search(said)), None)
            said = f"{before[at:]} {m.group(0)}" if at is not None else _clause_around(text, m.start(), m.end())  # from its own quantity to the diet word, not the whole clause
            if g := _group(diet, count, share, False, said):
                groups.append((m.start(), g))
        for m in (m for m in mentions if m not in partial and _EXCEPTION.search(_clause_full(text, m.start()))):
            clause = _clause_full(text, m.start())
            tail = clause[_EXCEPTION.search(clause).end():]
            eaten = next((d for erx, d in _EXCEPTION_DIETS if erx.search(tail)), None)
            n = _DIGITS.search(tail)
            word = next((_NUMBER_WORDS[w.group(0).lower()] for w in _QUANT_WORD.finditer(tail) if w.group(0).lower() in _NUMBER_WORDS), None)
            if g := _group(eaten, int(n.group(1)) if n and int(n.group(1)) >= 1 else word, None, True, clause):
                groups.append((m.start(), g))
            break
    kept: list[DietGroup] = []
    seen: set[tuple] = set()
    for _, g in sorted(groups, key=lambda x: x[0]):  # one group per diet and role: a later count for the same diet in the same message does not add a second one
        if (g.diet, g.exception) not in seen:
            seen.add((g.diet, g.exception))
            kept.append(g)
    return tuple(wide), kept


def corrections(text: str, prior: tuple[DietGroup, ...]) -> list[DietGroup]:
    """A new size for a group the draft already holds, said without a group wording: "30 vegan, not 25", "make the vegan ones 30", "vegan should be 30". Only a diet that already has
    a group is read this way, so "100 guests vegetarian" and "vegan catering" stay whole-event diets. The number written as the value being replaced ("not 25") is ignored."""
    text = mgr._blank_dates(text)
    out: list[DietGroup] = []
    for g in prior:
        rx = next((r for r, d in mgr._DIETS if d == g.diet), None)
        if rx is None:
            continue
        for m in rx.finditer(text):
            if negation.negated(text, m.start()):
                continue
            before = _clause_before(text, m.start())
            near = _SIZE_BEFORE.search(before)
            if near and not _OLD_VALUE.search(before[: near.start()]):
                n, at = int(near.group(1)), near.start()
                said = f"{before[at:]} {m.group(0)}"
            elif (after := _SIZE_AFTER.match(_CLAUSE.split(text[m.end(): m.end() + 40], maxsplit=1)[0].lstrip())) and not _OLD_VALUE.search(text[max(0, m.end() - 12): m.end()]):
                n, said = int(after.group(1)), f"{m.group(0)} {after.group(1)}"
            else:
                continue
            if n >= 1 and n != g.count and (made := _group(g.diet, n, None, g.exception, said)):
                out.append(made)
            break
    return out


def label_words(groups: list[DietGroup]) -> list[str]:
    """The user's own words for groups whose diet this product does not list: they go to the caterer as a food note, so they are never dropped."""
    return [g.words for g in groups if g.diet is None]

