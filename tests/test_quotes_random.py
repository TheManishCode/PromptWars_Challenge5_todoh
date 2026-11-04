"""Seeded random quote documents: tables and loose lines mixed with totals, tax, advance, discount and deposit rows.

An amount a quote returns must be one the row wrote; a summary row (tax, total, advance, deposit...) is never a priced item; a quantity times a rate that does not equal the total is
flagged. The first run found 'Security deposit: Rs 10,000' offered as a priced service line.
"""

import random

import pytest

from eventmate.documents.quotes import extract

ITEMS = ["Venue rental", "Catering - veg buffet", "Photography (6 hrs)", "Stage decor", "Sound system", "Welcome drinks", "Service staff"]
LOOSE = [("Venue rental", 45000), ("Catering veg buffet", 65000), ("Photography", 30000), ("Stage decor", 18000)]
SUMMARY_WORDS = ("gst", "grand", "subtotal", "advance", "discount", "deposit", "balance", "taxes", "service charge")


def inr(r, n):
    return r.choice([f"{n:,}", f"Rs {n:,}", f"₹{n:,}", f"{n}", f"INR {n:,}", f"Rs. {n}", f"{n:,}/-"])


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_table_rows_keep_their_own_amounts_and_summary_rows_are_not_items(seed):
    r = random.Random(seed)
    for _ in range(150):
        truth, rows = {}, []
        for item in r.sample(ITEMS, r.randint(2, 5)):
            q, rate = r.choice([1, 1, 50, 100, 120]), r.choice([500, 650, 1200, 4500, 18000, 25000])
            wrong = r.random() < .15
            shown = q * rate + (r.choice([1000, -500]) if wrong else 0)
            rows.append((item, q, rate, shown))
            truth[item] = (rate, shown, q * rate, wrong)
        cols = r.choice([("Item", "Qty", "Rate", "Total"), ("Description", "Quantity", "Unit Price", "Amount"), ("Particulars", "Nos", "Rate", "Amount")])
        md = ["| " + " | ".join(cols) + " |", "|---|---|---|---|", *[f"| {i} | {q} | {inr(r, rate)} | {inr(r, shown)} |" for i, q, rate, shown in rows]]
        sub = sum(x[3] for x in rows)
        md += r.sample([f"| Subtotal | | | {inr(r, sub)} |", f"| GST 18% | | | {inr(r, round(sub * .18))} |", f"| Grand Total | | | {inr(r, sub + round(sub * .18))} |", f"| Advance | | | {inr(r, 20000)} |",
                        f"| Security deposit | | | {inr(r, 10000)} |", f"| Discount | | | {inr(r, 5000)} |"], r.randint(1, 4))
        for line in extract("\n".join(md)).lines:
            base = next((k for k in truth if k in line.label or line.label in k), None)
            assert base is not None and not any(w in line.label.lower() for w in SUMMARY_WORDS), line.label
            rate, shown, product, wrong = truth[base]
            assert {a for a in (line.unit_inr, line.total_inr) if a is not None} <= {rate, shown, product}
            if wrong and line.unit_inr and line.total_inr and line.quantity and line.quantity * line.unit_inr != line.total_inr:
                assert line.warnings, (line, "a quantity times a rate that is not the total must be flagged")


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_loose_lines_give_only_the_items_and_never_a_deposit_or_a_tax(seed):
    r = random.Random(seed)
    summary = [("GST @ 18%", 8100), ("Advance", 20000), ("Discount", 5000), ("Grand Total", 90000), ("Balance payable", 70000), ("Taxes", 9000), ("Service charge 5%", 4500), ("Security deposit", 10000),
               ("Refundable deposit", 5000), ("Token amount", 5000), ("Booking amount", 10000)]
    for _ in range(200):
        truth, lines = {}, []
        for item, amount in r.sample(LOOSE, r.randint(1, 3)):
            lines.append(f"{r.choice(['', '- ', '* ', '1. '])}{item}{r.choice([':', ' - ', ' – ', ' = '])}{r.choice([f'Rs {amount:,}', f'₹{amount:,}', f'INR {amount:,}', f'Rs. {amount}', f'{amount:,}/-'])}")
            truth[item] = amount
        lines += [f"{label}: Rs {amount:,}" for label, amount in r.sample(summary, r.randint(1, 5))]
        r.shuffle(lines)
        for line in extract("\n".join(lines)).lines:
            base = next((k for k in truth if k.lower() in line.label.lower()), None)
            assert base is not None, (line.label, lines)
            assert {a for a in (line.total_inr, line.unit_inr) if a is not None} <= {truth[base]}
