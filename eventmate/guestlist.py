"""Guest-list import: a CSV the user uploads for one plan (ADR-055).

The list is personal data, so it is handled narrowly: parsed strictly with every problem reported by line, previewed before anything is saved,
saved only through a single-use token, kept in one private file per plan, and deleted with the plan. Models, logs and the assistant see counts and
diet totals only, never a name or a raw cell. Nothing here sends anything to a guest.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import threading
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import Field, ValidationError

from eventmate.retention import PLAN_ID, wipe
from eventmate.schemas import RSVP, DietaryRequirement, Guest, _Model

MAX_BYTES = 256 * 1024
MAX_ROWS = 500
MAX_NAME = 120
MAX_CELL = 400
PREVIEW_TTL_S = 15 * 60
MAX_PREVIEWS = 8
COLUMNS = ("name", "party_size", "rsvp", "dietary")
FORMULA_START = ("=", "+", "-", "@")
DIETS = tuple(d.value for d in DietaryRequirement)
CONTACT_COLUMNS = frozenset({"email", "e-mail", "phone", "mobile", "whatsapp", "address", "contact", "number"})  # named in the error so the user knows why


class GuestListError(ValueError):
    """The file as a whole cannot be used (too large, not UTF-8, wrong columns, too many rows). The message is safe to show."""


class GuestListTooLarge(GuestListError):
    """The file is over the size limit (reported as HTTP 413)."""


class StalePreview(Exception):
    """The preview token is unknown, used, expired, or older than a newer preview or change to this plan's list."""


class RowError(_Model):
    line: int = Field(ge=1)
    column: str = Field(max_length=20)
    message: str = Field(max_length=200)


class GuestListTotals(_Model):
    """Counts only. People are counted by party size; a declined party is not counted towards diets (the same rule the Guest Agent uses)."""

    rows: int = Field(ge=0)
    heads: int = Field(ge=0)  # everyone on the list, declined included
    yes: int = Field(ge=0)
    no: int = Field(ge=0)
    pending: int = Field(ge=0)
    dietary_counts: Dict[str, int] = {}
    required_diets: Tuple[str, ...] = ()


class ListCheck(_Model):
    """The list set beside the headcount the user stated. The plan keeps the stated number; this only says whether the two agree."""

    planning_headcount: int = Field(ge=1)
    expected: int = Field(ge=0)  # yes + pending: the people the list says may come
    flag: str  # "" when they agree


class GuestPreview(_Model):
    """What the preview endpoint returns, in the names the page reads: ``can_commit``, ``preview_token``, ``row_errors``, ``totals.rows`` and ``totals.heads``.
    The parsed rows are for the user's own screen; ``check`` sets the totals beside the stated headcount."""

    guests: Tuple[Guest, ...] = ()
    row_errors: Tuple[RowError, ...] = ()
    totals: GuestListTotals
    check: ListCheck
    can_commit: bool = False
    preview_token: Optional[str] = None  # absent while any row has a problem: nothing can be saved from a file with errors
    notice: str = "Nothing is saved until you commit. Only counts and diet totals are ever shown to the assistant; names stay on this machine."


class ParseResult(_Model):
    guests: Tuple[Guest, ...] = ()
    errors: Tuple[RowError, ...] = ()
    totals: GuestListTotals


_INVISIBLE = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})  # control, format (zero-width, bidi, tag characters), surrogate, private use, unassigned
_SKIPPED_AT_START = _INVISIBLE | {"Zs", "Zl", "Zp", "Mn", "Me"}  # what a spreadsheet may ignore before it decides a cell is a formula


def _clean(cell: str) -> str:
    """Control, zero-width and bidi characters out (a leading tab, NUL or U+200B must not hide a formula), then trimmed (F-005)."""
    return "".join(c for c in cell if unicodedata.category(c) not in _INVISIBLE).strip()


def _starts_like_formula(value: str) -> bool:
    """Does the cell begin with = + - or @ once anything invisible or blank in front of it is looked past (a combining mark, a space of any width)."""
    for c in value:
        if unicodedata.category(c) in _SKIPPED_AT_START:
            continue
        return c in FORMULA_START or unicodedata.normalize("NFKC", c) in FORMULA_START  # a full-width "＝" folds to "="
    return False


def totals_of(guests: Tuple[Guest, ...]) -> GuestListTotals:
    heads = Counter()
    diets: Counter = Counter()
    for g in guests:
        heads[g.rsvp.value] += g.party_size
        if g.rsvp != RSVP.NO:
            diets.update(d.value for d in g.dietary)
    return GuestListTotals(
        rows=len(guests), heads=sum(heads.values()), yes=heads["yes"], no=heads["no"], pending=heads["pending"],
        dietary_counts=dict(sorted(diets.items())), required_diets=tuple(sorted(diets)),
    )


def check_against(totals: GuestListTotals, planning_headcount: int) -> ListCheck:
    expected = totals.yes + totals.pending
    if expected > planning_headcount:
        flag = f"the list expects {expected} people but you planned for {planning_headcount}: raise the guest count or trim the list"
    elif totals.rows and expected < planning_headcount:
        flag = f"the list accounts for {expected} of the {planning_headcount} people you planned for; the plan keeps {planning_headcount}"
    else:
        flag = ""
    return ListCheck(planning_headcount=planning_headcount, expected=expected, flag=flag)


def parse_guest_csv(raw) -> ParseResult:
    """Parse CSV bytes or text. Raises GuestListError for a problem with the file; row problems come back in ``errors`` with their line numbers."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise GuestListError("the guest list must be text")
    if len(raw) > MAX_BYTES:
        raise GuestListTooLarge(f"the file is larger than {MAX_BYTES // 1024} KB")
    try:
        text = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise GuestListError("the file is not UTF-8 text; save it as CSV (UTF-8) and try again") from None
    if "\x00" in text:
        raise GuestListError("the file contains binary data, not a CSV")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), skipinitialspace=True))
    except csv.Error:
        raise GuestListError("the file is not valid CSV (a cell may be far too long, or a quote is never closed)") from None
    rows = [(n, r) for n, r in enumerate(rows, 1)]
    while rows and not any(c.strip() for c in rows[0][1]):
        rows.pop(0)
    if not rows:
        raise GuestListError("the file is empty")
    header = rows[0][1]
    names = [_clean(h).lower() for h in header]
    unknown = [h for h in names if h not in COLUMNS]
    if unknown or len(set(names)) != len(names):
        contact = sorted({h for h in unknown if h in CONTACT_COLUMNS})
        raise GuestListError(
            "the first line must name the columns, using only " + ", ".join(COLUMNS) + " (each once)"
            + (f"; {', '.join(contact)} is not accepted because to;doh-the event planner does not store guest contact details" if contact else "")
        )
    body = [(n, r) for n, r in rows[1:] if any(c.strip() for c in r)]
    if len(body) > MAX_ROWS:
        raise GuestListError(f"the list has {len(body)} rows; the limit is {MAX_ROWS}")

    guests: List[Guest] = []
    errors: List[RowError] = []
    for n, cells in body:
        problems = _row(n, cells, names, len(guests) + 1)
        if isinstance(problems, Guest):
            guests.append(problems)
        else:
            errors.extend(problems)
    return ParseResult(guests=tuple(guests), errors=tuple(errors), totals=totals_of(tuple(guests)))


def _row(line: int, cells: List[str], names: List[str], index: int):
    errors: List[RowError] = []

    def bad(column: str, message: str) -> None:
        errors.append(RowError(line=line, column=column, message=message))

    if len(cells) > len(names):
        bad("row", f"this row has {len(cells)} cells but the header has {len(names)}")
        return errors
    row = {name: _clean(cells[i]) if i < len(cells) else "" for i, name in enumerate(names)}
    for column, value in row.items():
        if len(value) > MAX_CELL:
            bad(column, f"longer than {MAX_CELL} characters")
        elif _starts_like_formula(value):
            bad(column, "starts with = + - or @, which a spreadsheet would run as a formula; remove that character")
    if errors:
        return errors
    name = row.get("name", "")
    if len(name) > MAX_NAME:
        bad("name", f"longer than {MAX_NAME} characters")
    size_text = row.get("party_size", "")
    size = 1
    if size_text:
        if not size_text.isdigit() or not 1 <= int(size_text) <= 20:
            bad("party_size", "must be a whole number from 1 to 20")
        else:
            size = int(size_text)
    rsvp_text = row.get("rsvp", "").lower() or "pending"
    if rsvp_text not in ("yes", "no", "pending"):
        bad("rsvp", "must be yes, no or pending")
    diet: List[DietaryRequirement] = []
    for part in (p.strip().lower() for p in row.get("dietary", "").split(";")):
        if not part:
            continue
        if part not in DIETS:
            bad("dietary", f"a value is not a known diet; use {', '.join(DIETS)} separated by semicolons")  # the cell is not echoed: a misplaced column could hold a name
        elif DietaryRequirement(part) not in diet:
            diet.append(DietaryRequirement(part))
    if errors:
        return errors
    try:
        return Guest(id=f"g{index:03d}", name=name or f"Guest {index}", rsvp=RSVP(rsvp_text), party_size=size, dietary=tuple(diet))
    except ValidationError:  # unreachable unless the model's own rules change: report it rather than crash
        bad("row", "this row could not be read")
        return errors


# ------------------------------------------------------------------ storage


class GuestListStore:
    """One private file per plan (``guests/guests-<plan_id>.json``, 0600), previews held in memory only. Names appear nowhere else."""

    def __init__(self, root: Path, clock=time.time):
        self.dir = Path(root) / "guests"
        self._clock = clock
        self._lock = threading.Lock()
        self._previews: Dict[str, Tuple[str, int, float, Tuple[Guest, ...]]] = {}  # token -> (plan, generation, expires, guests)
        self._generation: Dict[str, int] = {}

    def _path(self, plan_id: str) -> Path:
        if not PLAN_ID.fullmatch(plan_id):
            raise ValueError("invalid plan id")
        return self.dir / f"guests-{plan_id}.json"

    def preview(self, plan_id: str, result: ParseResult) -> str:
        """Hold a clean parse and return a single-use token. A file with any row error cannot be committed, so it gets no token."""
        if result.errors or not result.guests:
            raise GuestListError("fix the rows listed and preview again; nothing was saved")
        now = self._clock()
        with self._lock:
            self._previews = {t: v for t, v in self._previews.items() if v[2] > now}
            for t in [t for t, v in self._previews.items() if v[0] == plan_id]:
                del self._previews[t]  # a newer preview replaces an older one for the same plan
            if len(self._previews) >= MAX_PREVIEWS:
                oldest = min(self._previews, key=lambda t: self._previews[t][2])
                del self._previews[oldest]
            token = secrets.token_urlsafe(24)
            self._previews[token] = (plan_id, self._generation.get(plan_id, 0), now + PREVIEW_TTL_S, result.guests)
            return token

    def commit(self, plan_id: str, token: str) -> Tuple[Guest, ...]:
        now = self._clock()
        with self._lock:
            held = self._previews.pop(token, None) if isinstance(token, str) else None
            if held is None or held[0] != plan_id or held[2] <= now or held[1] != self._generation.get(plan_id, 0):
                raise StalePreview("that preview is no longer valid; preview the file again")
            guests = held[3]
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            doc = {"schema_version": 1, "plan_id": plan_id, "committed_at": datetime.now(timezone.utc).isoformat(), "guests": [json.loads(g.model_dump_json()) for g in guests]}
            tmp = self.dir / f".tmp-{secrets.token_hex(8)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self._path(plan_id))
            finally:
                if tmp.exists():
                    tmp.unlink()
            self._generation[plan_id] = self._generation.get(plan_id, 0) + 1
            return guests

    def load(self, plan_id: str) -> Optional[Tuple[Guest, ...]]:
        path = self._path(plan_id)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return tuple(Guest.model_validate(g) for g in doc["guests"])

    def delete(self, plan_id: str) -> bool:
        """Remove the list and drop any preview of it. The file is overwritten before it is unlinked."""
        path = self._path(plan_id)
        with self._lock:
            self._generation[plan_id] = self._generation.get(plan_id, 0) + 1
            for t in [t for t, v in self._previews.items() if v[0] == plan_id]:
                del self._previews[t]
            return wipe(path)

