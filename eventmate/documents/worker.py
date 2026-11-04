"""The converter process: ``python -m eventmate.documents.worker <file> <extension>`` prints one JSON object, ``{"ok": true, "engine", "markdown"}`` or ``{"ok": false, "error"}``.

It runs inside the sandbox (see ``sandbox.py``) and reads only the one file it is given. Built-in engines need nothing installed; the heavy ones are imported only when asked for."""

from __future__ import annotations

import csv
import io
import json
import sys
from html.parser import HTMLParser

MAX_ROWS = 2000
TEXT = {".txt", ".md"}
HTML = {".html", ".htm"}
HEAVY = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg"}  # Docling's formats
BUILTIN = TEXT | HTML | {".csv"}


class Unsupported(Exception):
    """Said to the person as written."""


def _cell(value: str) -> str:
    return " ".join(value.replace("|", "/").split())


def _table(rows: list[list[str]]) -> str:
    rows = [r for r in rows[:MAX_ROWS] if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    return "\n".join(["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width] + ["| " + " | ".join(r) + " |" for r in rows[1:]])


class _Html(HTMLParser):
    """Tables become markdown tables, everything else becomes lines of text; scripts and styles are dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.rows: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.skip = 0
        self.line: list[str] = []

    def _flush(self):
        text = " ".join("".join(self.line).split())
        if text:
            self.out.append(text)
        self.line = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        elif tag == "table":
            self._flush()
            self.rows = []
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in ("br", "p", "div", "li", "h1", "h2", "h3", "h4") and self.rows is None:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.rows is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.out.append(_table(self.rows))
            self.rows = None

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.rows is None:
            self.line.append(data)


def from_html(text: str) -> str:
    p = _Html()
    p.feed(text)
    p.close()
    p._flush()
    return "\n\n".join(x for x in p.out if x)


def from_csv(text: str) -> str:
    return _table(list(csv.reader(io.StringIO(text))))


def from_docling(path: str) -> str:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise Unsupported("reading this kind of file needs Docling (pip install docling, or pip install '.[documents]'); a text, CSV or HTML quote works without it") from None
    return DocumentConverter().convert(path).document.export_to_markdown()


def convert(path: str, ext: str) -> dict:
    if ext in HEAVY:
        return {"ok": True, "engine": "docling", "markdown": from_docling(path)}
    if ext not in BUILTIN:
        return {"ok": False, "error": f"a {ext or 'file without an extension'} file is not supported"}
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"\x00" in raw:
        return {"ok": False, "error": "that is not a text file"}
    text = raw.decode("utf-8", errors="replace")
    if ext == ".csv":
        return {"ok": True, "engine": "csv", "markdown": from_csv(text)}
    if ext in HTML:
        return {"ok": True, "engine": "html", "markdown": from_html(text)}
    return {"ok": True, "engine": "text", "markdown": text}


def main(argv: list[str]) -> int:
    try:
        out = convert(argv[1], argv[2])
    except Unsupported as exc:
        out = {"ok": False, "error": str(exc)}
    except Exception as exc:  # whatever a converter does with a hostile file, the parent gets one plain answer
        out = {"ok": False, "error": f"the file could not be read ({type(exc).__name__})"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
