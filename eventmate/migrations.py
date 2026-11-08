"""Schema versions and upgraders for everything the application stores (v6 prompt 1.2).

Every stored record carries ``schema_version`` (a JSONL line gets it as its first key when written; a plan file and a job row carry it too). A record
with none is version 1: that is how every file written before this existed reads. On read a record is **upgraded in memory** through one function per
step, ``(kind, n) -> n + 1``; the bytes on disk are never rewritten, and a plan's stored hash covers the stored schema, so a plan from an older version
still passes its hash check and is upgraded only after that. A record written by a *newer* application is refused with an error that names the file, the
line and both versions; it is never read half-understood.

``kind`` is the store's channel name (``ledger``, ``outreach``, ``inbound``, ``mandate``, ``contact``, ``suppression``, ``lead``, ``digest``, ...), or
``plan_version`` or ``job``. Nothing is registered below yet because every format is still at version 1; the tests register a fake version 2 to prove the
path works.
"""

from __future__ import annotations

import json
from typing import Any
from collections.abc import Callable

# Money and send state: a damaged file here stops sending (integrity.py), and nothing may be appended behind a cut-off line (jsonl.py)
AUTHORITATIVE = frozenset({"ledger", "outreach", "inbound", "mandate", "contact", "suppression", "unmatched", "reply_review", "brief_link"})
CURRENT: dict[str, int] = {}  # kind -> the version this application writes; a kind not listed is at 1
UPGRADERS: dict[tuple[str, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}  # (kind, from_version) -> the document as the next version


class StoreCorrupt(ValueError):
    """A stored record cannot be read. Says which file and which line, and why; never the content of the record."""

    def __init__(self, source: str, line: int | None, reason: str):
        where = f"{source} line {line}" if line else source
        super().__init__(f"{where}: {reason}")
        self.source, self.line, self.reason = source, line, reason


class SchemaTooNew(StoreCorrupt):
    """The record was written by a newer version of the application than this one."""


def current(kind: str) -> int:
    return CURRENT.get(kind, 1)


def upgrade(kind: str, doc: dict[str, Any], version: int | None = None, source: str = "", line: int | None = None) -> dict[str, Any]:
    """The document at the current version of ``kind``. ``version`` is taken from the document's own ``schema_version`` (removed from it) unless given."""
    if version is None:
        version = doc.pop("schema_version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise StoreCorrupt(source, line, "schema_version is not a positive whole number")
    target = current(kind)
    if version > target:
        raise SchemaTooNew(source, line, f"written by a newer version of the application (schema {version}; this one reads up to {target}); upgrade the application")
    while version < target:
        step = UPGRADERS.get((kind, version))
        if step is None:
            raise StoreCorrupt(source, line, f"no upgrade from schema {version} of {kind}")
        doc = step(doc)
        version += 1
    return doc


def parse_line(kind: str, raw: str | bytes, source: str, line: int) -> dict[str, Any]:
    """One JSONL line as a document at the current version, or StoreCorrupt."""
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError):
        raise StoreCorrupt(source, line, "not valid JSON (a torn or damaged line)") from None
    if not isinstance(doc, dict):
        raise StoreCorrupt(source, line, "not a JSON object")
    return upgrade(kind, doc, source=source, line=line)


def stamp(kind: str, json_text: str) -> str:
    """A record's JSON with ``schema_version`` as its first key."""
    body = json.loads(json_text)
    return json.dumps({"schema_version": current(kind), **body}, ensure_ascii=False, separators=(",", ":"))
