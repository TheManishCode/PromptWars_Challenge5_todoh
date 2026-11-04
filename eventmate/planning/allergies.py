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
_CUE = re.compile(r"\b(?:allerg\w*|alerji|elerji|celiac|coeliac|intoleran\w*)\b", _F)
_NEG_START = re.compile(r"^\W*(?:no|not|without|never|don'?t|dont|nahi|nahin|nope|nobody|none|nothing|no\s?one|noone)\b", _F)
_NEG_CUE = re.compile(r"\b(?:nobody|none|no\s?one|noone|koi\s+nahi)\b.{0,80}?(?:allerg|alerji|intoleran)|(?:allerg|alerji|intoleran)\w*\s+(?:\w+\s+){0,2}(?:nobody|none)\b|\b(?:no|not|never|nahi|nahin|without)\s+(?:\w+\s+){0,2}(?:allerg|alerji|intoleran)|\b(?:allerg\w*|alerji)\s+(?:\w+\s+){0,2}(?:nahi|nahin|nope)\b", _F)
_SPLIT_SENTENCE = re.compile(r"[.;!?\n]|\b(?:but|however|although|except|yet|lekin|magar)\b", _F)
_SPLIT_ITEM = re.compile(r"[,/&]|\b(?:and|or|aur|plus|also)\b", _F)  # no leading \s*: on a long run of spaces that is quadratic

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
_FREE_OF = re.compile(r"\b(?:peanut|groundnut|nut|tree[- ]?nut|dairy|egg|shellfish|fish|soy|soya|sesame|mustard|celery|lupin|sulph?ite)[- ]free\b", _F)
_STOP = frozenset("""a an the to of and or also is are was has have had hai hain se ko ka ki ke bhi aur my his her their our your its i we he she they me us him them it
 some any many few very too so more most mild severe serious bad slight little especially mainly mostly really quite extremely badly highly seriously hugely
 allergic allergy allergies alerji intolerant intolerance for from with in on at by as be been being that this these those there here who whom which what when where
 guest guests people person persons kids kid child children son daughter mother father mom dad wife husband friend friends family anyone someone everyone one two three
 mere meri mera hamare humare unhe unko usse unse uska uski""".split())
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


def read(text: str) -> AllergyReading:
    found: list[A] = []
    unclear: list[str] = []
    for sentence in _SPLIT_SENTENCE.split(text):
        in_list = False  # the previous item was an allergy statement: an item with no cue of its own may continue its list
        for item in (i for i in _SPLIT_ITEM.split(sentence) if i and i.strip()):
            cue = _CUE.search(item)
            negated = bool(_NEG_START.search(item) or _NEG_CUE.search(item))
            if cue:
                if negated:
                    in_list = False
                    continue
                before, after = item[: cue.start()], item[cue.end():]
                named = _named(item)  # the cue itself can name one ("celiac")
                words = _leftover(after) or (_leftover(before)[-1:] if not named else [])
                if not named and not words:
                    words = [item.strip()]  # "he has an allergy": said, not named
                found += named
                if words:
                    unclear.append(_phrase(words))
                in_list = True
            elif _FREE_OF.search(item) and not negated:
                found += _named(" ".join(m.group(0).replace("-free", " ").replace(" free", " ") for m in _FREE_OF.finditer(item)))
            elif in_list and not negated:
                named = _named(item)
                found += named
                rest = _leftover(item)
                if rest and not named and len(rest) <= 2 and not _BENIGN.search(item) and not _CITY.search(item):
                    unclear.append(_phrase(rest))  # "allergic to peanuts, strawberries": a second thing we cannot name
            elif negated:
                in_list = False
    safe = tuple(dict.fromkeys(re.sub(r"[^A-Za-z' -]", "", p).strip() for p in unclear if re.sub(r"[^A-Za-z' -]", "", p).strip()))[:MAX_UNCLEAR]
    return AllergyReading(tuple(dict.fromkeys(found)), safe)
