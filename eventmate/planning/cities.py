"""One spelling per Indian city, so "Bangalore", "Bengaluru" and "BLR" are the same place and a spelling variant never loops the intake.

Two layers. ``canonical`` and the alias table say which names are the same city. ``read`` is what the intake uses to find the city in a message: it knows the cities the vendor data
covers as well as the aliases, reads them in any case, keeps a distinct place distinct ("Navi Mumbai" is not Mumbai), understands "not Pune, Mumbai", treats two cities as a
question for the user, and, for a name that looks like a misspelling of a city it knows, suggests that city and waits to be told: a typo is never turned into another place by itself.
"""

from __future__ import annotations

import difflib
import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass

from eventmate.planning.questions import Pick, Unresolved, clip

ALIASES: dict[str, str] = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru", "blr": "Bengaluru",
    "bombay": "Mumbai", "mumbai": "Mumbai", "bom": "Mumbai",
    "navi mumbai": "Navi Mumbai",  # a different city from Mumbai: never folded into it
    "poona": "Pune", "pune": "Pune",
    "madras": "Chennai", "chennai": "Chennai",
    "calcutta": "Kolkata", "kolkata": "Kolkata",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "baroda": "Vadodara", "vadodara": "Vadodara",
    "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    "cochin": "Kochi", "kochi": "Kochi",
    "mysore": "Mysuru", "mysuru": "Mysuru",
    "new delhi": "Delhi", "delhi": "Delhi", "dilli": "Delhi",
    "hyderabad": "Hyderabad", "hyd": "Hyderabad",
    "jaipur": "Jaipur", "ahmedabad": "Ahmedabad",
}
# Places the vendor data does not hold, spelled the way the state now writes them, and the localities of the large cities people name instead of the city ("Koramangala" is Bengaluru).
# They are here so a message that names one is read as that place and not asked about again; a city with no vendor records still fails at planning, and the intake says so.
_MORE_CITIES = {
    "mysuru": ("mysore",), "mandya": (), "udupi": ("udipi",), "hubballi": ("hubli", "hubli-dharwad", "hubballi-dharwad"), "dharwad": (), "mangaluru": ("mangalore",), "madikeri": ("mercara",),
    "belagavi": ("belgaum", "belgavi"), "kalaburagi": ("gulbarga",), "shivamogga": ("shimoga",), "tumakuru": ("tumkur",), "hassan": (), "davanagere": ("davangere",), "ballari": ("bellary",),
    "vijayapura": ("bijapur",), "chikkamagaluru": ("chikmagalur", "chikmagaluru"), "coorg": ("kodagu",), "ooty": ("udhagamandalam", "udagamandalam"), "coimbatore": ("kovai",), "madurai": (),
    "salem": (), "tiruchirappalli": ("trichy", "tiruchi"), "vellore": (), "tirunelveli": ("nellai",), "kozhikode": ("calicut",), "thrissur": ("trichur",), "palakkad": ("palghat",),
    "visakhapatnam": ("vizag", "vishakhapatnam"), "vijayawada": (), "tirupati": (), "guntur": (), "nagpur": (), "nashik": ("nasik",), "aurangabad": (), "kolhapur": (), "indore": (),
    "bhopal": (), "lucknow": (), "kanpur": (), "patna": (), "ranchi": (), "bhubaneswar": (), "guwahati": (), "chandigarh": (), "amritsar": (), "surat": (), "rajkot": (), "goa": ("panaji", "panjim"),
    "noida": (), "ghaziabad": (), "faridabad": (), "dehradun": (), "jodhpur": (), "udaipur": (), "varanasi": ("banaras", "benares"), "thane": (), "vasai": (),
}
_LOCALITIES = {
    "Bengaluru": ("koramangala", "indiranagar", "hsr layout", "hsr", "electronic city", "whitefield", "jayanagar", "marathahalli", "jp nagar", "btm layout", "btm", "malleshwaram", "yelahanka", "hebbal",
                  "bellandur", "sarjapur", "banashankari", "basavanagudi", "rajajinagar", "bannerghatta", "kr puram", "domlur", "mg road", "brigade road", "frazer town", "yeshwanthpur"),
    "Mumbai": ("andheri", "bandra", "powai", "juhu", "colaba", "worli", "borivali", "malad", "goregaon", "dadar", "chembur", "kurla", "ghatkopar", "versova"),
    "Pune": ("kothrud", "hinjewadi", "baner", "viman nagar", "koregaon park", "hadapsar", "wakad", "aundh", "kharadi", "pimpri", "chinchwad"),
    "Delhi": ("dwarka", "rohini", "saket", "connaught place", "karol bagh", "lajpat nagar", "vasant kunj", "janakpuri"),
    "Hyderabad": ("gachibowli", "hitech city", "hi-tech city", "madhapur", "banjara hills", "jubilee hills", "secunderabad", "kondapur"),
    "Chennai": ("t nagar", "adyar", "anna nagar", "velachery", "mylapore", "nungambakkam", "tambaram", "porur", "omr"),
    "Kolkata": ("salt lake", "park street", "new town", "howrah"),
}
for _c, _aliases in _MORE_CITIES.items():
    ALIASES.setdefault(_c, _c.capitalize())
    for _a in _aliases:
        ALIASES.setdefault(_a, _c.capitalize())
for _parent, _places in _LOCALITIES.items():
    for _p in _places:
        ALIASES.setdefault(_p, _parent)
ALIASES["mysore"] = "Mysuru"
PREP_ONLY = frozenset({"hassan", "salem", "goa", "surat", "patna", "thane", "omr", "hsr", "btm"})  # also a person's name or a word: a city only after "in", "at" or "near"
_ALTERNATION = "|".join(sorted(map(re.escape, ALIASES), key=lambda a: (-len(a), a)))
# a city written on its own, with no "in" before it: the longer spellings, any case, and the airport-style codes only in capitals (a lowercase "bom" is not Mumbai)
BARE = re.compile(rf"(?<!\w)(?:{'|'.join(sorted((a for a in ALIASES if len(a) >= 4 and a not in PREP_ONLY), key=lambda a: (-len(a), a)))}|(?-i:BLR|HYD|BOM))(?!\w)", re.I)
AFTER_PREPOSITION = re.compile(rf"\b(?:in|at|near)\s+({_ALTERNATION})\b", re.I | re.A)


def canonical(name: str | None) -> str | None:
    """The one spelling for a known alias, else the name as given (stripped)."""
    if name is None:
        return None
    key = " ".join(name.split()).lower()
    return ALIASES.get(key, name.strip())


# ------------------------------------------------------------------------------------------- reading a city from a message

_MONTHS = "jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october november december".split()
# words that follow "in", "at" or "near" without being a place ("in the evening", "in total", "at 6pm"), and the small words around a city name
_NOT_A_PLACE = frozenset(
    _MONTHS + """the a an my our this that your their his her its total all case order advance addition general time mind person people style spirit touch charge day days week weeks weekend
    weekday evening morning afternoon night noon hotel home house hall garden banquet budget vegetarian veg non jain halal vegan indoor outdoor party wedding birthday engagement lakh lac
    crore rs inr rupees guests guest pax monday tuesday wednesday thursday friday saturday sunday tomorrow today tonight yesterday ghar office mein kuch sabhi yes no ok okay thanks thank please sure maybe none nothing in at near on for with and or by to from upto under over about around approx it is city""".split()
)
_NEGATING = re.compile(r"(?:\bnot|\bnever|\bexcept|\bnahi|\bnahin|\bother\s+than|\brather\s+than|\binstead\s+of|\bno)\s+(?:in\s+|at\s+|near\s+|the\s+)?$", re.I)
_AFTER_PREPOSITION_WORDS = re.compile(r"\b(?:in|at|near)\s+([^\W\d_][\w.'-]*(?:\s+[^\W\d_][\w.'-]*)?)", re.I | re.A)
_LABELLED = re.compile(r"\bcity\s*(?:is|:|=|-)\s*([^\W\d_][\w.'-]*(?:\s+[^\W\d_][\w.'-]*)?)", re.I | re.A)
_CHANGE_TO = re.compile(r"\b(?:change|make|set|shift|move|switch|update)\s+(?:the\s+|my\s+)?(?:city|town|location|place)\s+(?:to|as)\s+([^\W\d_][\w.'-]*(?:\s+[^\W\d_][\w.'-]*)?)", re.I | re.A)
_MEIN = re.compile(r"(?<![\w.'-])([^\W\d_][\w.'-]{3,})\s+(?:mein|mai)\b", re.I | re.A)
_DESCRIPTOR = frozenset("traditional south north east west indian style veg menu food party function hall palace grounds ground resort banquet club restaurant park lawn lawns road cross circle temple".split())
_PLACE_NAME = re.compile(r"[^\W\d_]+(?:[ .'-][^\W\d_]+){0,2}")
_ANSWER_PREFIX = re.compile(r"^(?:in|at|near|it'?s|its|it\s+is|the\s+city\s+is|city\s*(?:is|:))\s+", re.I)
NEAR_CUTOFF = 0.74
MAX_CITY_CHARS = 40


@dataclass(frozen=True)
class CityReading:
    city: str | None = None  # the city the message names, as the vendor data spells it when it has it
    unresolved: Unresolved | None = None  # two cities, or a name that looks like a misspelling: the user decides
    removed: tuple[str, ...] = ()  # cities the user said it is NOT in ("not Pune")
    heard: str | None = None  # for a bare answer that was no use: what was said, so the next question can quote it


@functools.lru_cache(maxsize=32)
def _tables(known: tuple[str, ...]):
    """(name -> city, one regex for every name anywhere, one for every name after in/at/near). The vendor data's own spelling wins for a city it has."""
    spelled = {canonical(k): k for k in known}
    names = {a: spelled.get(c, c) for a, c in ALIASES.items()}
    for k in known:
        names.setdefault(k.lower(), k)
    by_length = sorted(names, key=lambda a: (-len(a), a))
    anywhere = re.compile(rf"(?<!\w)(?:{'|'.join(map(re.escape, (a for a in by_length if len(a) >= 4 and a not in PREP_ONLY)))}|(?-i:BLR|HYD|BOM))(?!\w)", re.I | re.A)
    after_prep = re.compile(rf"\b(?:in|at|near)\s+({'|'.join(map(re.escape, by_length))})\b", re.I | re.A)
    return names, anywhere, after_prep


def _occurrences(text: str, known: tuple[str, ...]) -> list[tuple[str, bool]]:
    """(city, negated) for every known city written in the text, in the order written."""
    names, anywhere, after_prep = _tables(known)
    seen: dict[int, tuple[str, bool]] = {}
    for rx, group in ((anywhere, 0), (after_prep, 1)):
        for m in rx.finditer(text):
            start, city = m.start(group), names.get(" ".join(m.group(group).split()).lower())
            if city is not None and start not in seen:
                seen[start] = (city, bool(_NEGATING.search(text[max(0, start - 30): start])))
    return [seen[pos] for pos in sorted(seen)]


def _near(word: str, known: tuple[str, ...]) -> str | None:
    """The known city this word looks like a misspelling of, or None. Only ever a suggestion: the caller asks, it never applies it."""
    names, _, _ = _tables(known)
    w = " ".join(word.lower().split())
    if len(w) < 3 or w in names or w in _NOT_A_PLACE:
        return None
    best = difflib.get_close_matches(w, list(names), n=1, cutoff=NEAR_CUTOFF)
    return names[best[0]] if best else None


def _as_written(word: str) -> str:
    w = " ".join(word.split()).strip(".,;:!?'\"()")
    return w if any(c.isupper() for c in w) else " ".join(p.capitalize() for p in w.split())


def _suggestion(typed: str, suggest: str) -> Unresolved:
    kept = _as_written(typed)
    return Unresolved(
        key="city", field="city", said=clip(typed, 40),
        question=f'I do not know a city called "{clip(typed, 40)}". Did you mean {suggest}? Press it to use {suggest}, or keep "{kept}" as written (I have no vendor records for it).',
        picks=(Pick(label=suggest, field="city", value=suggest), Pick(label=f"{kept} (as written)", field="city", value=kept)),
    )


def _which_name(first: str, whole: str) -> Unresolved:
    return Unresolved(
        key="city", field="city", said=clip(whole, 40),
        question=f'Is the city "{clip(first, 40)}" or "{clip(whole, 40)}"? I have no vendor records for either.',
        picks=(Pick(label=_as_written(first), field="city", value=_as_written(first)), Pick(label=_as_written(whole), field="city", value=_as_written(whole))),
    )


def _conflict(cities: list[str]) -> Unresolved:
    return Unresolved(
        key="city", field="city", said=clip(" and ".join(cities), 80),
        question=f"I found more than one city ({', '.join(cities[:-1])} and {cities[-1]}). Which city is the event in?",
        picks=tuple(Pick(label=c, field="city", value=c) for c in cities[:6]),
    )


def _place_like_words(text: str) -> list[str]:
    """Words the user wrote after "in", "at" or "near" (or after "city is") that could be a place, whole name first: "Navi Mumbay", then "Navi". Ordinary words are left out."""
    out: list[str] = []
    for m in (*_LABELLED.finditer(text), *_CHANGE_TO.finditer(text), *_AFTER_PREPOSITION_WORDS.finditer(text), *_MEIN.finditer(text)):
        words = [w.strip(".,;:!?'\"()") for w in m.group(1).split()]
        if not words[0] or words[0].lower() in _NOT_A_PLACE:
            continue
        if len(words) == 2 and words[1] and words[1].lower() not in _NOT_A_PLACE and words[1].lower() not in _DESCRIPTOR and words[0][:1].isupper() == words[1][:1].isupper():
            out.append(f"{words[0]} {words[1]}")
        out.append(words[0])
    return out


def _is_a_city_name(word: str) -> bool:
    """Something a person would type alone to answer "Which city?": a short name made of letters, not a filler word."""
    parts = word.split()
    return bool(word) and len(word) <= MAX_CITY_CHARS and bool(_PLACE_NAME.fullmatch(word)) and all(len(p) >= 2 and p.lower() not in _NOT_A_PLACE for p in parts) and bool(re.search(r"[aeiouy]", word, re.I))


def read(text: str, known: Iterable[str] = (), *, answer: bool = False) -> CityReading:
    """The city in ``text``. ``answer`` means the whole message is the reply to "Which city?": a bare name is then accepted as written."""
    known_t = tuple(dict.fromkeys(k for k in known if k))
    found = _occurrences(text, known_t)
    removed = tuple(dict.fromkeys(c for c, neg in found if neg))
    gone = {canonical(r) for r in removed}
    live = [c for c in dict.fromkeys(c for c, neg in found if not neg) if canonical(c) not in gone]
    if len(live) > 1:
        return CityReading(unresolved=_conflict(live), removed=removed)
    if live:
        return CityReading(city=live[0], removed=removed)
    words = _place_like_words(text)
    for typed in words:
        suggest = _near(typed, known_t)
        if suggest:
            return CityReading(unresolved=_suggestion(typed, suggest), removed=removed)
    for typed in words:
        if typed[:1].isupper() and all(p[:1].isupper() for p in typed.split()):
            if len(typed.split()) == 2 and (first := typed.split()[0]) and any(typed.split()[1].lower() not in _NOT_A_PLACE for _ in (0,)):
                return CityReading(unresolved=_which_name(first, typed), removed=removed)  # two capitals could be one name or a place and a word after it ("Mandya Traditional"): the user says which
            return CityReading(city=_as_written(typed), removed=removed)  # a capitalised name is a place; a lowercase word after "in" is not, so it is asked for by name
    change = _CHANGE_TO.search(text)
    if change and change.group(1).split()[0].lower() not in _NOT_A_PLACE and len(change.group(1).split()[0]) >= 3:
        return CityReading(city=_as_written(change.group(1).split()[0]), removed=removed)  # "change city to coimbatore": said to be the city, in any case
    mein = _MEIN.search(text)
    if mein and mein.group(1).lower() not in _NOT_A_PLACE:
        return CityReading(city=_as_written(mein.group(1)), removed=removed)  # "tumkur mein": the place written before "in", in Hindi
    label = _LABELLED.search(text)
    if label and label.group(1).split()[0].lower() not in _NOT_A_PLACE:
        return CityReading(city=_as_written(label.group(1).split()[0]), removed=removed)  # "city: nagpur": the user said it is the city
    if answer and not removed:
        word = _ANSWER_PREFIX.sub("", text.strip().strip(".,;:!?\"'()").strip())
        names, _, _ = _tables(known_t)
        if word.lower() in names:
            return CityReading(city=names[word.lower()])  # as an answer, even a lowercase code ("hyd") is the city
        if _is_a_city_name(word):
            suggest = _near(word, known_t)
            return CityReading(unresolved=_suggestion(word, suggest)) if suggest else CityReading(city=_as_written(word))
        return CityReading(heard=clip(text.strip(), 40) or None)
    return CityReading(removed=removed)
