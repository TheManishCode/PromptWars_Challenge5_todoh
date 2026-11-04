"""Reading a stated allergy from the user's own words, and saying so when an allergy statement cannot be read.

Two rules, because a missed allergy is the harmful error:
* a list stays a list ("allergic to peanuts, cashews and eggs" is three allergens), and
* a statement that mentions an allergy but names nothing this reader knows ("allergic to strawberries", "mungfali" spelled a way it has not seen) is never read as
  "no allergy": its words are kept in ``unclear`` so the plan says the allergy was not understood.
The vocabulary follows the major-allergen lists used by food labelling (the EU's 14, as in Open Food Facts' allergens taxonomy) plus common Hindi and Hinglish
names. Nothing here is inferred from anything but the user's words; a model neither sets nor clears these (the intake enforces that).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Literal

from eventmate.planning.cities import BARE as _CITY
from eventmate.planning.questions import Pick, Unresolved, clip
from eventmate.schemas import Allergen as A

_F = re.I | re.A
_CUE = re.compile(r"\b(?:allerg(?:y|ies|ic|ics|en|ens|ist|ists)|alerji|elerji|celiac|coeliac|intoleran\w*|(?:can'?t|cannot|can\s+not|doesn'?t|does\s+not|won'?t|unable\s+to)\s+(?:eat|have|take|touch|digest)|avoid(?:s|ing)?)\b", _F)  # "can't eat X" and "avoid X" are read as an allergy: the harmful error is to miss one
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


# What follows these words is another clause ("2 guests have an egg allergy so the kitchen must be careful"), not more of what they are allergic to.
_CLAUSE_AFTER = re.compile(r"\b(?:so|because|since|therefore|hence|which\s+means|that\s+is\s+why)\b", _F)
# An item that opens with its own subject is a new statement, not one more item of the list ("allergic to eggs, everyone else eats anything").
_NEW_SUBJECT = re.compile(r"^\W*(?:everyone|everybody|anyone|anybody|others?|rest|the\s+rest|baaki|baki|sab|all|we|they|he|she|i|you|my|our|their|no\s?one|nobody)\b", _F)


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
                if clause := _CLAUSE_AFTER.search(after):
                    after = after[: clause.start()]
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
            elif in_list and not negated and _NEW_SUBJECT.match(item) and not _named(item):
                in_list = False  # "everyone else eats anything": a new statement, so the list is over and nothing in it is an allergy
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


# ------------------------------------------------------------------------------------------------ words that look like an allergy but are not read as one
#
# Everything above reads an allergy only from the exact words it knows. That is deliberate: an allergy is never invented from a word that merely resembles one. But a person who
# types "alergic to peanuts" or "allergic to penuts" has said something about an allergy, and dropping it silently is the harmful error. ``suspects`` finds those places so the
# intake can ask ("did you mean peanut?") instead of guessing either way.

# a spelling of "allergy", "anaphylaxis" or an auto-injector that is not one of the exact cue words: alergic, allergik, alerjy, anaphalaxis, epipen
_CUE_TYPO = re.compile(
    r"\b(?:a[l1]+[ae]?r+[gj]\w*|el+[ae]?r+[gj]\w*|anap\w*|anaf\w*|epi[- ]?pens?|epinephrine|(?:severe|serious|bad|life[- ]threatening|anaphylactic)\s+reactions?|reacts?\s+(?:badly\s+)?to)\b", _F
)
_CUE_WORDS = ("allergy", "allergic", "allergies", "allergen", "allergens")
CUE_CUTOFF = 0.84  # on its own a word must be this close to "allergy"/"allergic": real misspellings score 0.86-0.93; "alley" scores 0.83, "alcohol" and "algorithm" under 0.7
CUE_CUTOFF_WITH_FOOD = 0.72  # when the same sentence names an allergen ("one guest is alketgic to peanuts") a worse spelling is still read as the cue
_CUE_FIRST_LETTERS = "ae"  # the cue words begin with "a" (or "e" in "elerji"); "gallery" scores 0.86 and is never an allergy, so a word that starts otherwise is not a cue
_NOT_A_CUE = frozenset("allergist allergists alley alleys alleged allegory allergenic".split())
_EXCLUSION = re.compile(r"\b(?:no|without|zero)\s+(?:any\s+)?(?P<food>peanuts?|ground\s?nuts?|tree\s?nuts?|nuts?|shellfish|sesame)\b", _F)
_FUZZY_WORDS = ("peanut", "peanuts", "groundnut", "almond", "almonds", "cashew", "cashews", "walnut", "walnuts", "pistachio", "hazelnut", "shellfish", "prawns", "shrimp", "lobster", "oysters",
                "sesame", "gluten", "wheat", "celery", "mustard", "lupin", "sulphite", "sulphites", "eggs", "milk", "dairy", "lactose", "soya", "seafood", "tofu", "walnut")
FUZZY_CUTOFF = 0.8
Kind = Literal["typo_cue", "typo_word", "unnamed", "exclusion"]


@dataclass(frozen=True)
class Suspect:
    kind: Kind
    said: str  # the user's words
    allergens: tuple[A, ...] = ()  # what they may have meant; empty when nothing can be offered


def _near_food(word: str) -> tuple[A, ...]:
    """The allergens a misspelled food word looks like ("penuts" -> peanut), or (). Offered to the user, never applied."""
    w = word.lower()
    if len(w) < 5 or _named(w):
        return ()
    best = difflib.get_close_matches(w, _FUZZY_WORDS, n=1, cutoff=FUZZY_CUTOFF)
    return tuple(dict.fromkeys(_named(best[0]))) if best else ()


def _fuzzy_cue(sentence: str, food_named: bool) -> str | None:
    """A word that is a few letters away from "allergy"/"allergic" ("alletgic", "alkergy", "allegry"), when it is not the cue itself or an ordinary word."""
    cutoff = CUE_CUTOFF_WITH_FOOD if food_named else CUE_CUTOFF
    for w in _words(sentence):
        lw = w.lower()
        if len(lw) >= 5 and lw[0] in _CUE_FIRST_LETTERS and lw not in _NOT_A_CUE and not _CUE.search(lw) and max(difflib.SequenceMatcher(None, lw, c).ratio() for c in _CUE_WORDS) >= cutoff:
            return w
    return None


def suspects(text: str) -> list[Suspect]:
    """Places where the user seems to have written about an allergy but nothing was read: a misspelled cue, a misspelled food after a correct cue, a serious reaction with no food named,
    or "no peanuts" with nothing to say whether it is an allergy."""
    out: list[Suspect] = []
    reading = read(text)
    for phrase in reading.unclear:
        for word in _words(phrase):
            if near := _near_food(word):
                out.append(Suspect("typo_word", word, near))
                break
    for sentence in _SPLIT_SENTENCE.split(_NA.sub("none", text)):
        if _CUE.search(sentence):
            continue  # an exact cue: the reader above handled it, and the loop over its unclear words handled any typo in the food
        food = bool(_named(sentence)) or any(_near_food(w) for w in _words(sentence))
        if cue := (_CUE_TYPO.search(sentence) or (m := _fuzzy_cue(sentence, food)) and re.search(re.escape(m), sentence)):
            named = tuple(dict.fromkeys(_named(sentence)))
            near = () if named else next((n for n in (_near_food(w) for w in _words(sentence)) if n), ())
            if named or near:
                out.append(Suspect("typo_cue", cue.group(0), named or near))
            else:
                out.append(Suspect("unnamed", cue.group(0)))
        elif m := _EXCLUSION.search(sentence):
            out.append(Suspect("exclusion", m.group(0), tuple(dict.fromkeys(_named(m.group("food"))))))
    return out


def _names(allergens: tuple[A, ...]) -> str:
    return " and ".join(a.value.replace("_", " ") for a in allergens)


def question_for(s: Suspect) -> Unresolved:
    """The question the intake asks for one suspect: what was written, what it may mean, and a way to say it is something else."""
    word = re.sub(r"[^A-Za-z' -]", "", s.said).strip()[:40] or "allergy"
    key = f"allergy:{word}"
    yes = (Pick(label=f"Yes, {_names(s.allergens)}", field="allergens", value=",".join(a.value for a in s.allergens)),) if s.allergens else ()
    if s.kind == "typo_word":
        return Unresolved(key=key, field="allergens", said=clip(s.said, 40), question=f'I do not know the word "{clip(s.said, 40)}" as a food. Did you mean {_names(s.allergens)}? An allergy is never guessed, so please press it or say it is something else.',
                          picks=(*yes, Pick(label=f"Something else (keep my words: {clip(s.said, 30)})", field="allergens", keep_words=True)))
    if s.kind == "typo_cue":
        return Unresolved(key=key, field="allergens", said=clip(s.said, 40), question=f'I read "{clip(s.said, 40)}" as an allergy, but I only record an allergy when it is spelled out. Is anyone allergic to {_names(s.allergens)}?',
                          picks=(*yes, Pick(label="No, it is not an allergy", field="allergens")))
    if s.kind == "exclusion":
        return Unresolved(key=key, field="allergens", said=clip(s.said, 40), question=f'You wrote "{clip(s.said, 40)}". Is anyone allergic to {_names(s.allergens)}, or is it only a preference?',
                          picks=(*yes, Pick(label="Only a preference", field="allergens")))
    return Unresolved(key=key, field="allergens", said=clip(s.said, 40), question=f'You mentioned "{clip(s.said, 40)}", which is a serious reaction, but not to what. What is the allergy? Write the food, for example "peanuts".')


def unnamed_question(phrase: str) -> Unresolved:
    """An allergy was written but this reader cannot name it ("allergic to kiwi"). It is asked about, so the draft is not ready until the user names it or keeps their words: the plan then
    says the allergy was not understood. It is never left as a row beside a draft that looks finished."""
    word = re.sub(r"[^A-Za-z' -]", "", phrase).strip()[:40] or "allergy"
    return Unresolved(
        key=f"allergy_unclear:{word}", field="allergens", said=clip(word, 40),
        question=f'You mentioned an allergy to "{clip(word, 40)}", which I could not name. What is the allergy? Write the food, for example "peanuts", or keep your words as written and the plan will say the allergy was not understood.',
        picks=(Pick(label=f"Keep as written ({clip(word, 30)})", field="allergens", keep_words=True),),
    )


def unnamed_questions(text: str) -> list[Unresolved]:
    """The questions for allergies in ``text`` that were written but not named, leaving out words already offered back as "did you mean peanut?"."""
    consumed = {s.said.lower() for s in suspects(text) if s.kind == "typo_word"}
    return [unnamed_question(u) for u in read(text).unclear if not consumed & {w.lower() for w in u.split()}]


# ------------------------------------------------------------------------------------------------ asking to remove a stated allergy
# An allergy once stated is never replaced by a later message (see ``read`` and the intake's merge). A request to drop it is therefore a question, never an edit: the user presses
# "remove" or "keep". Without this the request was ignored with no message, and a quiet removal would be the worse error.
_REMOVAL = re.compile(
    r"\b(?:nobody|no\s?one|none\s+of\s+(?:us|them|the\s+guests))\s+(?:is\s+|are\s+|has\s+|have\s+)?(?:actually\s+)?(?:allergic|any\s+allerg\w+)\b"
    r"|\b(?:remove|delete|drop|forget|ignore|cancel|clear|undo|take\s+off)\b[^.;!?\n]{0,40}\ballerg\w*"
    r"|\ballerg\w*[^.;!?\n]{0,30}\b(?:no\s+longer|not\s+(?:any\s?more|needed|applicable)|removed|cancell?ed)\b"
    r"|\bno\s+(?:more\s+)?allerg(?:y|ies)\b|\bno\s+longer\s+(?:allergic|applies)\b|\bnot\s+allergic\s+any\s?more\b|\bkoi\s+allergy\s+nahi\b",
    _F,
)


def removal_requested(text: str) -> bool:
    return bool(_REMOVAL.search(text))


def removal_question(stated: tuple[A, ...]) -> Unresolved:
    names = _names(stated)
    return Unresolved(
        key="allergy_remove", field="allergens", said=clip(names, 40),
        question=f"Earlier you said someone is allergic to {names}. An allergy is only removed when you say so here. Remove it?",
        picks=(Pick(label=f"Yes, remove the {names} allergy", field="allergens", value=",".join("-" + a.value for a in stated)), Pick(label="No, keep it", field="allergens")),
    )
