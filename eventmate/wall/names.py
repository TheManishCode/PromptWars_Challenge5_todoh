"""Validation of a wall name (docs/WALL.md, 8.4). Server side only. The blocklist and the look-alike check reduce abuse; they are not complete."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

NAME_MIN, NAME_MAX = 2, 24
_ALLOWED_PUNCT = frozenset(" -'’.")
_STRIP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})  # control, format (zero-width, bidi), surrogate, private, unassigned, line/paragraph separators
_URLISH = re.compile(r"(?i)(?:https?|ftp|www)\b|\b[\w-]{1,63}\s{0,3}(?:\.|dot)\s{0,3}(?:com|net|org|in|io|co|me|xyz|app|dev|ly|gg|tv|info|biz|ru|cn)\b|\b(?:at|@)\s{0,3}[\w-]{1,63}\s{0,3}(?:\.|dot)")  # bounded: the sweep in test_regex_time flagged the unbounded form
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "wall_blocklist.txt"
_FAMILY = {"HIRAGANA": "cjk", "KATAKANA": "cjk", "CJK": "cjk", "HANGUL": "hangul"}
_RUN = re.compile(r"(.)\1{5,}")  # six of the same character in a row is a flood, not a name


class InvalidName(ValueError):
    """The name cannot go on the wall. The message is short and safe to show to the visitor."""


def _blocklist(path: Path = BLOCKLIST_PATH) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """(whole words, fragments). A file that cannot be read blocks nothing: moderation, not this list, is the control."""
    try:
        lines = [x.strip().casefold() for x in path.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return (), ()
    lines = [x for x in lines if x and not x.startswith("#")]
    return tuple(x for x in lines if not x.startswith("~")), tuple(x[1:] for x in lines if x.startswith("~") and len(x) > 1)


def _stretchy(entry: str) -> str:
    """A regex for an entry that tolerates repeated letters ("fuuuck"), without collapsing the name itself: "kkk" still needs three k's, "nigg" two g's."""
    return "".join(re.escape(c) + "+" for c in entry)


_WORDS, _FRAGMENTS = _blocklist()


def normalize(raw: str) -> str:
    """NFKC, no control, zero-width or bidi characters, one space between words, trimmed."""
    text = unicodedata.normalize("NFKC", raw)
    text = "".join(c for c in text if unicodedata.category(c) not in _STRIP_CATEGORIES or c == " ")
    return " ".join(text.split())


def _script(ch: str) -> Optional[str]:
    if not ch.isalpha():
        return None
    word = unicodedata.name(ch, "").split(" ", 1)[0]
    return _FAMILY.get(word, word or None)


def _folded(text: str) -> str:
    base = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    if any(c.isalpha() for c in base):  # look-alike digits are only read as letters inside a word that has letters: "1515" is a number, "1s1s" is not
        base = base.translate(_LEET)
    return re.sub(r"[^a-z0-9]", "", base)


def validate(raw: object, blocklist: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None) -> str:
    """The stored form of a name, or ``InvalidName``."""
    if not isinstance(raw, str):
        raise InvalidName("a name is text")
    if len(raw) > NAME_MAX * 8:  # before any work on it
        raise InvalidName(f"names are {NAME_MIN} to {NAME_MAX} characters")
    removed = sum(1 for c in raw if unicodedata.category(c) in _STRIP_CATEGORIES and c not in " \t\n\r")
    if removed > 4:
        raise InvalidName("that name has hidden characters")
    name = normalize(raw)
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise InvalidName(f"names are {NAME_MIN} to {NAME_MAX} characters")
    for c in name:
        cat = unicodedata.category(c)
        if not (cat[0] in "LM" or cat == "Nd" or c in _ALLOWED_PUNCT):
            raise InvalidName("names can use letters, numbers, spaces, hyphens, apostrophes and full stops")
    if sum(1 for c in name if c.isalnum()) < 2 or not any(c.isalpha() for c in name):
        raise InvalidName("a name needs at least two letters or numbers, and one letter")
    if any(0x0250 <= ord(c) <= 0x02FF for c in name):
        raise InvalidName("that name uses letters that look like others")  # IPA and modifier letters: look-alikes for plain Latin, rare in real names
    if _URLISH.search(name):
        raise InvalidName("a name cannot be an address or a link")
    if _RUN.search(name.casefold()):
        raise InvalidName("that name repeats one character too many times")
    for token in name.split(" "):
        if len({s for s in map(_script, token) if s}) > 1:
            raise InvalidName("each word must use one alphabet")  # Latin 'a' and Cyrillic 'а' in one word is a look-alike trick
    words, fragments = (_WORDS, _FRAGMENTS) if blocklist is None else blocklist
    folded_words = [_folded(t) for t in name.split(" ")]
    whole = _folded(name)
    if any(re.fullmatch(_stretchy(w), t) for w in words for t in folded_words) or any(re.search(_stretchy(f), whole) for f in fragments):
        raise InvalidName("that name is not allowed")
    return name
