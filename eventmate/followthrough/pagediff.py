"""What changed between two readings of a page, ignoring what changes by itself.

Ported from openmuse's ``page-diff.ts`` (MIT, Copyright (c) 2026 OpenMuse contributors: ``pageLines``, ``withoutRelativeTimes``, ``diffPage``), re-written for Python. A comparison against upstream found no copied lines; the one stretch they share is the relative-time pattern below ("5 minutes ago"), which is the same expression, so the upstream notice is kept in docs/LICENSES.md. A line that only differs in a
relative time ("posted 2 days ago" to "posted 3 days ago") is not news; a line whose only change is a number (a price, a count) is news and is listed as updated; a
line that was there and is gone is removed. Each previous line can account for at most one current line, so one leftover cannot explain two changes.

The page text is untrusted data: it is only ever compared and counted here, never run, never sent to a model.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from collections.abc import Callable

LINE_CHARS = 300
MAX_LINES = 2000
_SPACE = re.compile(r"\s+")
# "5 minutes ago", "an hour ago", "one week ago"; bounded alternatives, no nested repetition
_RELATIVE = re.compile(r"\b(?:\d{1,6}|an?|one)\s+(?:sec(?:ond)?|min(?:ute)?|hour|hr|day|week|month|year)s?\s+ago\b", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PLURAL = re.compile(r"([^\W\d_])s\b")


@dataclass(frozen=True)
class PageDiff:
    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def empty(self) -> bool:
        return not (self.added or self.updated or self.removed)


def page_lines(text: str, limit: int = MAX_LINES) -> list[str]:
    """The distinct, whitespace-collapsed, length-capped lines of a page, in order."""
    seen, out = set(), []
    for raw in text.splitlines():
        line = _SPACE.sub(" ", raw).strip()[:LINE_CHARS]
        if line and line not in seen:
            seen.add(line)
            out.append(line)
            if len(out) >= limit:
                break
    return out


def without_relative_times(text: str) -> str:
    return _RELATIVE.sub("<time>", text)


def _numberless(line: str) -> str:
    return _PLURAL.sub(r"\1", _NUMBER.sub("#", without_relative_times(line))).lower()


def _pair(previous: list[str], current: list[str], key: Callable[[str], str]) -> tuple[list[str], list[str], list[str]]:
    """Match each current line to one unused previous line with the same key. Returns (paired, unpaired current, previous lines left over, in page order)."""
    open_: dict[str, deque[int]] = defaultdict(deque)
    for i, line in enumerate(previous):
        open_[key(line)].append(i)
    taken, paired, unpaired = set(), [], []
    for line in current:
        bucket = open_.get(key(line))
        if bucket:
            taken.add(bucket.popleft())
            paired.append(line)
        else:
            unpaired.append(line)
    return paired, unpaired, [line for i, line in enumerate(previous) if i not in taken]


def diff_page(previous: list[str], current: list[str]) -> PageDiff:
    same, unpaired, left = _pair(previous, current, lambda s: s)
    _retimed, unpaired, left = _pair(left, unpaired, without_relative_times)  # only a relative time changed: not news
    renumbered, unpaired, left = _pair(left, unpaired, _numberless)  # a number changed: news, listed as updated
    return PageDiff(added=tuple(unpaired), updated=tuple(renumbered), removed=tuple(left))


def describe(diff: PageDiff, shown: tuple[int, int, int] = (8, 4, 4)) -> str:
    """A short "New / Updated / Removed" summary, or "" when no line changed."""
    out: list[str] = []
    for title, lines, limit in (("New", diff.added, shown[0]), ("Updated", diff.updated, shown[1]), ("Removed", diff.removed, shown[2])):
        if lines:
            out += [f"{title}:"] + [f"- {x[:160]}" for x in lines[:limit]] + ([f"+{len(lines) - limit} more"] if len(lines) > limit else [])
    return "\n".join(out)


def count(diff: PageDiff) -> str:
    total = len(diff.added) + len(diff.updated) + len(diff.removed)
    if not total:
        return ""
    parts = [f"{n} {w}" for n, w in ((len(diff.added), "new"), (len(diff.updated), "updated"), (len(diff.removed), "removed")) if n]
    return f"{total} line{'' if total == 1 else 's'} changed ({', '.join(parts)})"
