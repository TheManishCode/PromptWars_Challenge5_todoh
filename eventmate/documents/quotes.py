"""Priced lines out of a quote's markdown. Every amount returned is written in the line it came from (checked again with the same test the reply parser uses), a quantity times a
rate that does not equal the total is flagged and never corrected, and nothing here is a price the planner may use until the user confirms it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from eventmate.followthrough.claims import amount_is_in_text

MAX_LINES = 200
MAX_AMOUNT = 100_000_000  # INR 10 crore: more than that on one line is a misread, not a quote
_NUMBER = re.compile(r"(?<![\d.,])(\d[\d,]*(?:\.\d+)?)(?:\s?(lakhs?|lacs?|crores?|cr|k)\b)?", re.I)
_MULT = {"k": 1_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}
_HEADS = {
    "item": re.compile(r"^(?:item|items|description|particulars?|service|services|details?|name|inclusions?)$", re.I),
    "qty": re.compile(r"^(?:qty|quantity|nos?\.?|pax|units?|count|guests?|plates?)$", re.I),
    "rate": re.compile(r"^(?:rate|price|unit (?:price|rate|cost)|cost|per (?:plate|head|unit)|rate per \w+|price per \w+)(?:\s*\(.*\))?$", re.I),
    "total": re.compile(r"^(?:total|amount|line total|sub ?total|cost|price)(?:\s*\(.*\))?$", re.I),
}
_SUMMARY = re.compile(r"^(?:(grand\s+)?total|sub\s*-?\s*total|net(?: total| amount)?|amount payable|balance|gst|igst|cgst|sgst|tax(?:es)?|service charge|advance|discount|(?:security\s+|caution\s+|refundable\s+)?deposit|token(?:\s+amount)?|booking\s+amount|round(?:ing)?[- ]?off|(?:amount\s+)?(?:paid|due))\b", re.I)
_LOOSE = re.compile(r"^\s*(?:[-*•]\s*|\d+[.)]\s*)?(?P<label>[A-Za-z][^:–—\-=|]{2,60}?)\s*(?::|–|—|-|=)\s*(?P<rest>.*\d.*)$")


@dataclass(frozen=True)
class QuoteLine:
    label: str
    quantity: int | None
    unit_inr: int | None
    total_inr: int | None
    basis: str  # "per_unit" (rate and quantity), "total" (one amount), "unclear"
    warnings: tuple[str, ...] = ()


@dataclass
class QuoteExtract:
    lines: list[QuoteLine] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)  # "grand_total", "subtotal", "tax", "advance", "discount"
    warnings: list[str] = field(default_factory=list)


def read_amount(cell: str) -> int | None:
    """One INR amount written in the cell ("₹2,00,000", "Rs. 450/-", "1.5 lakh"), or None when there is none or more than one."""
    found = []
    for m in _NUMBER.finditer(re.sub(r"(?i)\b(?:inr|rs\.?|rupees)|₹|/-", " ", cell)):
        try:
            value = round(float(m.group(1).replace(",", "")) * (_MULT[m.group(2).lower()] if m.group(2) else 1))
        except ValueError:
            continue
        found.append(value)
    if len(found) != 1 or not 0 < found[0] <= MAX_AMOUNT or not amount_is_in_text(found[0], re.sub(r"(?i)\b(?:inr|rs\.?|rupees)|₹|/-", " ", cell)):
        return None
    return found[0]


def read_quantity(cell: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,6})(?:\s*(?:nos?\.?|pax|plates?|units?|persons?|people|guests?|x))?\s*", cell, re.I)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _roles(header: list[str]) -> dict[int, str] | None:
    roles: dict[int, str] = {}
    for i, h in enumerate(header):
        for role in ("item", "qty", "rate", "total"):
            if _HEADS[role].match(h) and role not in roles.values():
                roles[i] = role
                break
    return roles if "item" in roles.values() and ("total" in roles.values() or "rate" in roles.values()) else None


def _summary_key(label: str) -> str:
    low = label.lower()
    if re.search(r"grand|amount payable|net", low) or low.strip() == "total":
        return "grand_total"
    return ("subtotal" if "sub" in low else "tax" if re.search(r"gst|tax", low) else "advance" if re.search(r"advance|deposit|token|booking", low) else "discount" if "discount" in low else "other")


def _line(label: str, qty: int | None, rate: int | None, total: int | None) -> QuoteLine:
    warn = []
    if qty and rate and total is not None and qty * rate != total:
        warn.append(f"{qty} x {rate:,} is {qty * rate:,}, but the total says {total:,}")
    if qty and rate and total is None:
        total = qty * rate  # arithmetic the reader can see, not a figure the vendor wrote
        warn.append("the total is worked out from quantity and rate; the vendor did not write it")
    basis = "per_unit" if rate and qty else "total" if total is not None else "unclear"
    if basis == "unclear":
        warn.append("it is not stated whether this price is per unit or for the whole line")
    return QuoteLine(label[:120], qty, rate, total, basis, tuple(warn))


def _from_tables(rows: list[str], out: QuoteExtract) -> None:
    roles: dict[int, str] | None = None
    for raw in rows:
        cells = _cells(raw)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        header = _roles(cells)
        if header:
            roles = header
            continue
        if roles is None:
            continue
        by = {roles[i]: c for i, c in enumerate(cells) if i in roles}
        label = by.get("item", "").strip()
        if not label:
            continue
        rate, total, qty = read_amount(by.get("rate", "")), read_amount(by.get("total", "")), read_quantity(by.get("qty", ""))
        if _SUMMARY.match(label):
            value = total if total is not None else rate
            if value is not None:
                out.summary.setdefault(_summary_key(label), value)
            continue
        if rate is None and total is None:
            continue
        if len(out.lines) < MAX_LINES:
            out.lines.append(_line(label, qty, rate, total))


def _from_prose(text: str, out: QuoteExtract) -> None:
    for raw in text.splitlines():
        if "|" in raw:
            continue
        m = _LOOSE.match(raw)
        if not m:
            continue
        label, rest = m.group("label").strip(), m.group("rest")
        amount = read_amount(rest)
        if amount is None:
            continue
        if _SUMMARY.match(label):
            out.summary.setdefault(_summary_key(label), amount)
        elif len(out.lines) < MAX_LINES:
            per = re.search(r"\b(?:per|a|each|/)\s?(?:plate|head|person|guest|pax)\b", rest, re.I)
            out.lines.append(QuoteLine(label[:120], None, amount if per else None, None if per else amount, "per_unit" if per else "total",
                                       ("per plate or head: the quantity is not on the quote",) if per else ()))


def extract(markdown: str) -> QuoteExtract:
    out = QuoteExtract()
    table_rows = [ln for ln in markdown.splitlines() if ln.count("|") >= 2]
    _from_tables(table_rows, out)
    if not out.lines:
        _from_prose(markdown, out)
    if not out.lines:
        out.warnings.append("no priced lines were found in that document")
        return out
    totals = [ln.total_inr for ln in out.lines if ln.total_inr is not None]
    grand = out.summary.get("grand_total")
    if grand is not None and len(totals) == len(out.lines):
        added, tax, off = sum(totals), out.summary.get("tax", 0), out.summary.get("discount", 0)
        if grand not in {added, added + tax, added - off, added + tax - off}:
            out.warnings.append(f"the lines add up to {added:,} but the document's total says {grand:,}")
    return out
