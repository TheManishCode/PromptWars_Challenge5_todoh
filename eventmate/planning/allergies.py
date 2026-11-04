"""Reading a stated allergy from the user's own words, and saying so when an allergy statement cannot be read.

Two rules, because a missed allergy is the harmful error:
* a list stays a list ("allergic to peanuts, cashews and eggs" is three allergens), and
* a statement that mentions an allergy but names nothing this reader knows ("allergic to strawberries", "mungfali" spelled a way it has not seen) is never read as
  "no allergy": its words are kept in ``unclear`` so the plan says the allergy was not understood.
The vocabulary follows the major-allergen lists used by food labelling (the EU's 14, as in Open Food Facts' allergens taxonomy) plus common Hindi and Hinglish
names. Nothing here is inferred from anything but the user's words; a model neither sets nor clears these (the intake enforces that).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eventmate.planning.cities import BARE as _CITY
from eventmate.schemas import Allergen as A

_F = re.I | re.A
_CUE = re.compile(r"\b(?:allerg\w*|alerji|elerji|celiac|coeliac|intoleran\w*|(?:can'?t|cannot|can\s+not|doesn'?t|does\s+not|won'?t|unable\s+to)\s+(?:eat|have|take|touch|digest)|avoid(?:s|ing)?)\b", _F)  # "can't eat X" and "avoid X" are read as an allergy: the harmful error is to miss one
_NEG_START = re.compile(r"^\W*(?:no|not|without|never|don'?t|dont|nahi|nahin|nope|nobody|none|nothing|no\s?one|noone)\b", _F)
# A negation counts only when nothing but filler words stand between it and the cue: "no known allergies", "guests are not allergic", "none of the guests have any allergies".
# Anything else between them ("no onion no garlic lactose intolerant") is another statement, and the allergy in it is still read.
_FILL = r"(?:(?:any|known|food|at|all|really|so|very|that|of|the|a|an|is|are|was|were|am|be|been|to|anything|such|kind|sort|type|guests?|people|persons?|here|has|have|had|with|in|our|us|them|family|friends?|hai|hain|koi|ko|ke|mein|mere|meri|unhe|dikkat|problem|issue|as|far|we|know|i|he|she|they|you|my|his|her|their|him|need|needed|worry|about|special)\s+)"
_NEG_CUE = re.compile(
    rf"\b(?:nobody|none|no\s?one|noone|koi\s+nahi|(?:no|not|never|nahi|nahin|without)|(?:isn'?t|aren'?t|wasn'?t|weren'?t|doesn'?t|don'?t|do\s+not|does\s+not|did\s+not|didn'?t)\s+(?:have\s+|has\s+)?)\s+{_FILL}{{0,6}}(?:allerg|alerji|intoleran|avoid)"
    rf"|\b(?:allerg\w*|alerji)\s+{_FILL}{{0,3}}(?:nahi|nahin|nope)\b|\b(?:allerg\w*|alerji)\s+{_FILL}{{0,3}}(?:nobody|none)\b",
    _F,
)
_SPLIT_SENTENCE = re.compile(r"[.!?]|\n\s*\n|\b(?:but|however|although|except|yet|lekin|magar)\b", _F)  # a single line break or a semicolon does not end an allergy list; a blank line does
_SPLIT_ITEM = re.compile(r"[,/&;|+\n]|\b(?:and|or|aur|plus|also|too)\b", _F)  # no leading \s*: on a long run of spaces that is quadratic
_BULLET = re.compile(r"^[\s\-*\u2022\d.)(]+")
_MARKER = re.compile(r"^\W*(?:also|and|plus|too|as\s+well|additionally|aur)\b", _F)  # a short sentence that starts like this continues the list before it
_NA = re.compile(r"\bn\s{0,2}/\s{0,2}a\b", _F)
_NO_ANSWER = frozenset("none nil no nope nothing nahi nahin na".split())  # "Allergies: none" is an answer, not an allergy we could not read
MAX_CONTINUATION_WORDS = 4  # an item or a sentence longer than this is a new thought, not one more item of the list

# (words, the allergens they name). "nut" alone does not say which kind, so it names both.
_VOCAB: list[tuple[re.Pattern[str], tuple[A, ...]]] = [
    (re.compile(r"\b(?:peanuts?|groundnuts?|monkey\s?nuts?|(?:mung|moong)(?:ph|f)alis?|singdana)\b", _F), (A.PEANUT,)),
    (re.compile(r"\b(?:tree[- ]?nuts?|almonds?|badam|cashews?|kaju|walnuts?|akhrot|pistachios?|pista|hazelnuts?|pecans?|macadamias?|brazil\s?nuts?)\b", _F), (A.TREE_NUT,)),
    (re.compile(r"\bnuts?\b", _F), (A.PEANUT, A.TREE_NUT)),
    (re.compile(r"\b(?:dairy|milk|lactose|doodh|dudh|paneer|dahi|curd|cheese|butter|ghee)\b", _F), (A.DAIRY,)),
    (re.compile(r"\b(?:eggs?|anda|ande)\b", _F), (A.EGG,)),
    (re.compile(r"\b(?:shellfish|prawns?|shrimps?|crabs?|lobsters?|jhinga|oysters?|mussels?|clams?|squid|octopus|scallops?|molluscs?|mollusks?|crustaceans?)\b", _F), (A.SHELLFISH,)),
    (re.compile(r"\bseafood\b", _F), (A.SHELLFISH, A.FISH)),
    (re.compile(r"\b(?:fish|machh?li|macchi)\b", _F), (A.FISH,)),
    (re.compile(r"\b(?:soy|soya|soybeans?|tofu)\b", _F), (A.SOY,)),
    (re.compile(r"\b(?:sesame|til|gingelly)\b", _F), (A.SESAME,)),
    (re.compile(r"\b(?:gluten|wheat|gehu+n?|maida|barley|rye|celiac|coeliac)\b", _F), (A.GLUTEN,)),
    (re.compile(r"\bcelery\b", _F), (A.CELERY,)),
    (re.compile(r"\b(?:mustard|sarson|rai)\b", _F), (A.MUSTARD,)),
    (re.compile(r"\b(?:sulph?ites?|sulfites?)\b", _F), (A.SULPHITES,)),
    (re.compile(r"\blupins?\b", _F), (A.LUPIN,)),
]
# "<x>-free" outside an allergy sentence: read only for the foods that are not already a diet here ("gluten free" is the gluten-free diet, handled there)
_FREE_OF = re.compile(r"\b(?:lactose|peanut|groundnut|nut|tree[- ]?nut|dairy|egg|shellfish|fish|soy|soya|sesame|mustard|celery|lupin|sulph?ite)[- ]free\b", _F)
_STOP = frozenset("""a an the to of and or also is are was has have had hai hain se ko ka ki ke bhi aur my his her their our your its i we he she they me us him them it
 some any many few very too so more most mild severe serious bad slight little especially mainly mostly really quite extremely badly highly seriously hugely
 allergic allergy allergies alerji intolerant intolerance for from with in on at by as be been being that this these those there here who whom which what when where
 guest guests people person persons kids kid child children son daughter mother father mom dad wife husband friend friends family anyone someone everyone one two three
 mere meri mera hamare humare unhe unko usse unse uska uski anything nothing something
 spicy spice spices food foods meat chicken mutton lamb beef pork onion onions garlic alcohol sugar salt oil sweets sweet non veg vegetarian""".split())
_BENIGN = re.compile(
    r"\d|\b(?:veg\w*|non|vegan|jain|halal|gluten|indoor|outdoor|cater\w*|food|meals?|photo\w*|reminders?|invit\w*|budget|rs|inr|rupees?|lakh|lac|crore|k|party|wedding|birthday|"
    r"engagement|conference|corporate|event|venue|please|thanks|thank|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*|in|at|on|for|under|around|about)\b",
    _F,
)
MAX_UNCLEAR, MAX_PHRASE = 5, 60


@dataclass(frozen=True)
class AllergyReading:
    allergens: tuple[A, ...] = ()
    unclear: tuple[str, ...] = ()  # the user's own words for allergies this reader could not name


def _named(text: str) -> list[A]:
    return [a for rx, kinds in _VOCAB if rx.search(text) for a in kinds]


def _leftover(text: str) -> list[str]:
    """Content words in ``text`` that are neither a known allergen nor a filler word."""
    cleaned = text
    for rx, _ in _VOCAB:
        cleaned = rx.sub(" ", cleaned)
    return [w for w in re.findall(r"[^\W\d_][\w'-]*", cleaned, re.A) if w.lower() not in _STOP]


def _phrase(words: list[str]) -> str:
    return " ".join(words)[:MAX_PHRASE].strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text, re.A)


def read(text: str) -> AllergyReading:
    found: list[A] = []
    unclear: list[str] = []
    carry = False  # the previous sentence ended inside an allergy list
    for sentence in _SPLIT_SENTENCE.split(_NA.sub("none", text)):
        items = [i for i in (_BULLET.sub("", x) for x in _SPLIT_ITEM.split(sentence)) if i.strip()]
        if not items:
            continue
        in_list = carry and (bool(_MARKER.match(sentence)) or len(_words(sentence)) <= MAX_CONTINUATION_WORDS)  # "Allergic to peanuts. Cashews too." / "Also eggs."
        pending = ""  # a cue that named nothing yet ("Allergies:"): the items that follow may name it
        for item in items:
            cue = _CUE.search(item)
            negated = bool(_NEG_CUE.search(item)) if cue else bool(_NEG_START.search(item))  # a leading "no" negates a continuation ("not cashews"), not a statement with its own cue
            if cue:
                before, after = item[: cue.start()], item[cue.end():]
                answer = [w.lower() for w in _words(after)]
                if negated or (answer and all(w in _NO_ANSWER for w in answer)):
                    in_list, pending = False, ""
                    continue
                named = _named(item)  # the cue itself can name one ("celiac")
                words = _leftover(after) or (_leftover(before)[-1:] if not named else [])
                found += named
                if words:
                    unclear.append(_phrase(words))
                    pending = ""
                elif not named and re.match(r"allerg|alerji|elerji|intoleran", cue.group(0), _F):
                    pending = item.strip()  # "he has an allergy" / "Allergies:": said, not yet named; the items that follow may name it ("avoid onion" is a diet, read elsewhere)
                else:
                    pending = ""
                in_list = True
            elif _FREE_OF.search(item) and not negated:
                found += _named(" ".join(m.group(0).replace("-free", " ").replace(" free", " ") for m in _FREE_OF.finditer(item)))
            elif in_list and not negated:
                answer = [w.lower() for w in _words(item)]
                if answer and all(w in _NO_ANSWER for w in answer):
                    in_list, pending = False, ""  # "Allergies:\n- none"
                    continue
                if len(answer) > MAX_CONTINUATION_WORDS:
                    found += _named(" ".join(item.split()[:MAX_CONTINUATION_WORDS]))  # a run-on ("peanuts and gluten 120 people in Pune"): the first words may still be the last item
                    in_list = False  # the rest is a new thought, not one more item
                    continue
                named = _named(item)
                found += named
                rest = _leftover(item)
                if named:
                    pending = ""
                elif rest and len(rest) <= 2 and not _BENIGN.search(item) and not _CITY.search(item):
                    unclear.append(_phrase(rest))  # "allergic to peanuts, strawberries": a second thing we cannot name
                    pending = ""
                else:
                    in_list = False  # a benign fact ("veg catering", "Pune") or filler: the list is over, so what follows is not one more item of it
            elif negated:
                in_list, pending = False, ""
        if pending:
            unclear.append(_phrase([pending]))  # nothing named it: never read as "no allergy"
        carry = in_list
    safe = tuple(dict.fromkeys(re.sub(r"[^A-Za-z' -]", "", p).strip() for p in unclear if re.sub(r"[^A-Za-z' -]", "", p).strip()))[:MAX_UNCLEAR]
    return AllergyReading(tuple(dict.fromkeys(found)), safe)
