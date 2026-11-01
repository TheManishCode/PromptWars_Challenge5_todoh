"""Handling of text a third party can write (Phase 4.2 and 4.3).

None of this is the security boundary: the model has no authority, tools are permission checked and a human approves every consequential action.
These functions make hidden text visible, keep untrusted values from breaking out of their fence or substituting into a template, and give outside
names one safe shape. A regex cannot recognise a paraphrased, translated or encoded injection; docs/MODEL_INPUTS.md says so.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping

LABEL_MAX = 120
HIDDEN_LIMIT = 8  # more invisible or tag characters than this in one text and it is refused, not cleaned
_FENCE = re.compile(r"<<<|>>>")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_TAG = re.compile(r"</?[A-Za-z!?][^<>]{0,60}>")
_MARKUP = re.compile(r"[<>{}`]")
_HSPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class UntrustedTextRejected(ValueError):
    """The text is mostly invisible characters or too long; the message is safe to show."""


def _invisible(ch: str) -> bool:
    cp = ord(ch)
    return unicodedata.category(ch) == "Cf" or 0xE0000 <= cp <= 0xE007F or 0xE0100 <= cp <= 0xE01EF or ch == "\u00ad"  # format, tag, variation-selector-supplement, soft hyphen


def normalize_untrusted(text: str, max_len: int = 4000) -> str:
    """NFKC, invisible and control characters removed, digits of any script as ASCII, whitespace collapsed. The result is what gets screened, masked
    and sent on; the original is not kept. Refuses text over ``max_len`` before any work, and text that hides more than ``HIDDEN_LIMIT`` characters."""
    if not isinstance(text, str):
        raise UntrustedTextRejected("text expected")
    if len(text) > max_len:
        raise UntrustedTextRejected(f"text is limited to {max_len} characters")
    text = unicodedata.normalize("NFKC", text)
    joiners = sum(1 for ch in text if ch in "\u200c\u200d")  # legitimate in emoji sequences and some scripts, so they are counted apart
    hidden = sum(1 for ch in text if _invisible(ch)) - joiners
    if hidden > HIDDEN_LIMIT or joiners > 4 * HIDDEN_LIMIT:
        raise UntrustedTextRejected("the text contains many hidden characters; type it again as plain text")
    out = []
    for ch in text:
        if _invisible(ch):
            continue
        cat = unicodedata.category(ch)
        if cat == "Nd":
            out.append(str(unicodedata.digit(ch)))
        elif ch in "\n\t" or cat not in ("Cc", "Zl", "Zp"):
            out.append(" " if cat == "Zs" else ch)
        elif cat in ("Zl", "Zp") or ch in "\r\x0b\x0c\x85":
            out.append("\n")
    cleaned = _BLANK_LINES.sub("\n\n", _HSPACE.sub(" ", "".join(out))).strip()
    if len(cleaned) > max_len:
        raise UntrustedTextRejected(f"text is limited to {max_len} characters")
    return cleaned


def strip_fence(text: str) -> str:
    """Remove the fence tokens so a value cannot close the fence it sits in."""
    return _FENCE.sub("", text)


def sanitize_label(text: object, limit: int = LABEL_MAX) -> str:
    """A name from outside (a map, a search result, a free-text field) in one safe shape: normalised, single line, no markup characters or fence tokens."""
    if not isinstance(text, str):
        return ""
    try:
        clean = normalize_untrusted(text[: limit * 8], max_len=limit * 8)
    except UntrustedTextRejected:
        clean = "".join(ch for ch in unicodedata.normalize("NFKC", text[: limit * 8]) if not _invisible(ch))
    clean = _MARKUP.sub(" ", _TAG.sub(" ", strip_fence(clean)))
    return _HSPACE.sub(" ", clean.replace("\n", " ")).strip()[:limit].strip()


_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[\]P^_X][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b.?", re.S)


def strip_terminal(text: object) -> str:
    """Third-party text made safe to print to a terminal (red-team finding: an inbound email could carry escape sequences that rewrite earlier lines,
    set the window title or, on some terminals, write the clipboard). Whole escape sequences go, then every control, format and bidi character;
    line breaks and tabs become spaces so the text stays on its own line."""
    clean = _ESCAPE.sub("", str(text))
    return "".join(" " if ch in "\n\r\t" else ch for ch in clean if ch in "\n\r\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp"))


def render_template(body: str, values: Mapping[str, str]) -> str:
    """Fill ``{name}`` placeholders in one pass. A value is never scanned again, so a value that contains ``{tools}`` stays literal text.
    Placeholders that are not in ``values`` are left as they are."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), body)
