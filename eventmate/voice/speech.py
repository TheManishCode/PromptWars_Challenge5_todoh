"""Turning a checked answer into something to say aloud, without adding to it.

Only an answer that has already passed the assistant's output check is ever spoken. Speaking it is a deterministic rewrite: markup, tables and links
are dropped, Indian amounts are written the way people say them ("1 lakh 39 thousand rupees"), and a long answer is shortened to a few sentences with an
offer of the rest on screen. Nothing is generated: ``verify_spoken`` recomputes every number and checks every capitalised name against the answer.
"""

from __future__ import annotations

import re
from typing import List, NamedTuple, Set, Tuple

MAX_SENTENCES = 3
MAX_WORDS = 55  # about twenty seconds at a normal speaking pace
MORE = "Want details? The full answer is on screen."
_ABBREVIATIONS = frozenset({"rs", "mr", "mrs", "ms", "dr", "st", "vs", "no", "approx", "e.g", "i.e", "etc", "inr", "min", "max"})

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")  # links and images: the text stays, the address goes
_URL = re.compile(r"(?:https?://|www\.)\S+")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_HEADING_OR_BULLET = re.compile(r"(?m)^[^\S\n]*(?:#{1,6}\s+|[-*+]\s+|\d{1,2}[.)]\s+|>\s+)")
_TABLE_RULE = re.compile(r"(?m)^\s*\|?[\s:|-]{3,}\|?\s*$")
_TABLE_ROW = re.compile(r"(?m)^\s*\|(.+)\|\s*$")
_MONEY = re.compile(r"(?:INR|Rs\.?|₹)\s?(\d[\d,]*(?:\.\d+)?)")
_BIG = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{2,3})+(?![\d,])")
_PERCENT = re.compile(r"(\d)\s?%")
_SPACE = re.compile(r"[ \t]+")


def strip_markup(text: str) -> str:
    """Drop markdown, tables, links and bare addresses; keep the words."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _TABLE_RULE.sub("", text)
    text = _TABLE_ROW.sub(lambda m: ", ".join(c.strip() for c in m.group(1).split("|") if c.strip()), text)
    text = _HEADING_OR_BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)
    return _SPACE.sub(" ", text).strip()


def _words_for(value: int) -> str:
    """Indian grouping in words: 139000 -> '1 lakh 39 thousand'; below one thousand the digits are left alone."""
    parts: List[str] = []
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        n, value = divmod(value, size)
        if n:
            parts.append(f"{n} {name}")
    if value:
        parts.append(str(value))
    return " ".join(parts)


def _amount(match: "re.Match[str]") -> str:
    raw = match.group(1).replace(",", "")
    if "." in raw:
        return f"{raw} rupees"  # a fractional amount is said as written
    value = int(raw)
    return f"{_words_for(value) if value >= 1000 else value} rupees"


def speak_numbers(text: str) -> str:
    text = _MONEY.sub(_amount, text)
    text = _BIG.sub(lambda m: _words_for(int(m.group(0).replace(",", ""))) if int(m.group(0).replace(",", "")) >= 100_000 else m.group(0), text)  # only a lakh or more: smaller plain numbers are left to the speech engine
    return _PERCENT.sub(r"\1 percent", text)


def split_sentences(text: str) -> List[str]:
    """Split on sentence ends and line breaks, not on decimals ("1.5"), "Rs." or "e.g."."""
    out: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        start = 0
        for m in re.finditer(r"[.!?]+(?=\s+\S)", line):
            before = line[start:m.start()].split()
            last = before[-1].lower().rstrip(".") if before else ""
            if last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
                continue
            out.append(line[start:m.end()].strip())
            start = m.end()
        tail = line[start:].strip()
        if tail:
            out.append(tail)
    return [s for s in out if re.search(r"\w", s)]


class Spoken(NamedTuple):
    sentences: Tuple[Tuple[str, str], ...]  # (what to say, what to show)
    text: str  # the sentences to say, joined
    truncated: bool


def _speak(sentence: str) -> str:
    return _SPACE.sub(" ", speak_numbers(strip_markup(sentence))).strip()


def spoken_answer(answer: str, max_sentences: int = MAX_SENTENCES, max_words: int = MAX_WORDS, readback: str = "") -> Spoken:
    """The checked answer as short speech. ``readback`` (the summary of a change the user may confirm) is always said, so a mis-heard number is caught."""
    said = [(_speak(s), s) for s in split_sentences(answer)]
    said = [(spoken, shown) for spoken, shown in said if spoken]
    pairs: List[Tuple[str, str]] = []
    words = 0
    for spoken, shown in said:
        n = len(spoken.split())
        if pairs and (len(pairs) >= max_sentences or words + n > max_words):
            break
        if not pairs and n > max_words:
            spoken = " ".join(spoken.split()[:max_words]).rstrip(",;:") + "."
            n = max_words
        pairs.append((spoken, shown))
        words += n
    truncated = len(pairs) < len(said) or (bool(said) and pairs[0][0] != said[0][0])
    if truncated:
        pairs.append((MORE, MORE))
    if readback:
        line = f"Please check on screen: {_speak(readback)}"
        line = line if line.endswith((".", "?", "!")) else line + "."
        pairs.append((line, line))
    return Spoken(tuple(pairs), " ".join(p[0] for p in pairs), truncated)


# ------------------------------------------------------------------ verification


_NUMBER_RUN = re.compile(r"\d+(?:\.\d+)?(?: (?:crore|lakh|thousand))?(?: \d+(?:\.\d+)?(?: (?:lakh|thousand))?)*")
_UNIT = {"crore": 10_000_000, "lakh": 100_000, "thousand": 1000}


def spoken_values(text: str) -> Set[float]:
    """Every number said in ``text``; "1 lakh 39 thousand" is one number, 139000."""
    values: Set[float] = set()
    for run in _NUMBER_RUN.findall(re.sub(r"(?<=\d),(?=\d)", "", text)):  # "61,000" is one number, as it is in the answer
        total, pending = 0.0, None
        for tok in run.split():
            if tok in _UNIT:
                total += (pending or 0) * _UNIT[tok]
                pending = None
            else:
                if pending is not None:
                    total += pending
                pending = float(tok)
        values.add(total + (pending or 0))
    return values


def answer_values(text: str) -> Set[float]:
    """Every number written in the answer (commas removed)."""
    return {float(m.replace(",", "")) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _names(text: str) -> Set[str]:
    out: Set[str] = set()
    for sentence in split_sentences(text) or [text]:
        for w in sentence.split()[1:]:  # the first word of a sentence is capitalised anyway
            w = w.strip(".,;:!?()\"'")
            if w[:1].isupper():
                out.add(w)
    return out


def verify_spoken(answer: str, spoken: str, readback: str = "") -> bool:
    """True when ``spoken`` adds no number and no capitalised name that is not in the answer (or in the read-back of a change the user will confirm)."""
    source = answer + " " + readback
    if not spoken_values(spoken) <= answer_values(source):
        return False
    return _names(spoken) <= (_names(source) | {w.strip(".,;:!?()\"'") for w in source.split()})
