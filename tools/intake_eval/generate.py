"""Generate the labelled intake cases: ``python tools/intake_eval/generate.py``  ->  tests/fixtures/intake_labelled/{dev,heldout}.json

A case is a short scripted conversation (what the user types, and which button they press) plus a label that says what a good reader does with it. The labels come from how the
case is built, never from what the reader answers.

Two splits, one generator:
* ``dev``     the cases written while the reader was being built: hand-picked typos and phrasings. Reader behaviour was tuned against these, so they prove regressions, not skill.
* ``heldout`` the same families with a different seed and typos made by random character edits (a dropped, swapped, doubled or neighbouring letter) of words the reader was never
              shown misspelt. Its first run found misses in the reader, which were then fixed, so it is SPENT: it now counts as a second dev set.
* ``heldout2`` the same generator with a fresh seed, written after those fixes and run once before anything was changed in response. Only this split says how the reader does on typos
              it has not been adjusted for. If it is used to fix the reader it is spent too, and the next split needs a new seed.
* ``stress``   heldout2's families with harder typing: two random edits per word, and the first letter wrong one time in three. It is meant to fail somewhere. Its first run (28 of
              103 allergy mentions silently missed) led to fixes, so it is SPENT.
* ``stress2``  the stress generator with a fresh seed, run once after those fixes and before looking at its failures.

Labels
  expect = "exact"         the final facts equal ``truth``; nothing is open; Build may be offered
           "ask"           no wrong value is set for the fields in ``ask_fields``, one of them is an open question, and Build is not offered; ``resolve`` (what the user then does)
                           must lead to ``truth`` within two steps
           "ask_or_exact"  either of the above is fine (a reader that is certain may read it; one that is not must ask)
           "city_ok"       the city is empty (asked) or exactly the name as written; never another city
  safety = {"allergens": [...]|None, "dietary": [...], "mention": true}   for cases about an allergy or a diet; scored apart from everything else
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "intake_labelled"
BASE = {"guest_count": 100, "city": "Pune", "event_date": "2026-12-12", "budget_total": 200000}
BRIEF = "wedding for 100 guests in Pune on 12 December 2026, budget 2 lakh"
KNOWN = ["Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad"]
NEIGHBOURS = {"a": "sqz", "e": "wrd", "i": "uok", "o": "ipl", "u": "yij", "s": "adw", "t": "ryg", "n": "bm", "l": "k", "r": "et", "g": "fh", "h": "gj", "m": "n", "d": "sf", "c": "xv", "p": "ol", "k": "jl", "b": "vn"}

ALLERGENS = {  # truth value -> words a person might write for it
    "peanut": ["peanuts", "groundnuts"], "tree_nut": ["cashews", "almonds", "walnuts"], "dairy": ["dairy", "milk"], "egg": ["eggs"], "shellfish": ["shellfish", "prawns"], "fish": ["fish"],
    "soy": ["soy"], "sesame": ["sesame"], "gluten": ["gluten", "wheat"], "celery": ["celery"], "mustard": ["mustard"],
}
HINGLISH_FOOD = {"peanut": "mungfali", "dairy": "doodh", "egg": "anda", "shellfish": "jhinga", "fish": "machli", "gluten": "gehu", "sesame": "til", "tree_nut": "kaju"}


def perturb(word: str, rng: random.Random) -> str:
    """One random character edit that keeps the first letter: drop, swap two neighbours, double, or hit the next key. Always differs from ``word``."""
    for _ in range(50):
        w, i = list(word), rng.randrange(1, max(2, len(word) - 1))
        op = rng.choice(["drop", "swap", "double", "key"])
        if op == "drop" and len(w) > 4:
            del w[i]
        elif op == "swap" and i + 1 < len(w):
            w[i], w[i + 1] = w[i + 1], w[i]
        elif op == "double":
            w.insert(i, w[i])
        elif op == "key" and w[i].lower() in NEIGHBOURS:
            w[i] = rng.choice(NEIGHBOURS[w[i].lower()])
        out = "".join(w)
        if out != word and out.lower() != word.lower():
            return out
    return word + word[-1]


def perturb_hard(word: str, rng: random.Random) -> str:
    """Two random edits, and one time in three the first letter changes too: how people type on a phone, not how a spell-checker test is written."""
    for _ in range(50):
        out = perturb(perturb(word, rng), rng)
        if rng.random() < 1 / 3 and len(out) > 3 and out[0].lower() in NEIGHBOURS:
            out = rng.choice(NEIGHBOURS[out[0].lower()]) + out[1:]
        if out.lower() != word.lower():  # two edits can undo each other, and then the "typo" is the right word
            return out
    return word + word[-1]


def case(split, group, steps, expect, truth=None, ask_fields=(), resolve=(), safety=None, note=""):
    return {"split": split, "group": group, "steps": [{"say": s} if isinstance(s, str) else s for s in steps], "expect": expect, "truth": truth if truth is not None else dict(BASE),
            "ask_fields": list(ask_fields), "resolve": [{"press": r} if isinstance(r, str) else r for r in resolve], "safety": safety, "note": note}


def brief(guests="100 guests", city="Pune", date="12 December 2026", budget="budget 2 lakh", extra=""):
    return f"wedding for {guests} in {city} on {date}, {budget}{extra}"


def generate(split: str, seed: int):
    rng = random.Random(seed)
    held = split.startswith("heldout") or split.startswith("stress")
    P = (lambda w: perturb_hard(w, rng)) if split.startswith("stress") else (lambda w: perturb(w, rng))
    cases = []
    add = cases.append

    # ---- A. clean briefs: must be read exactly with no question (this is the false-alarm check)
    for guests in ("30 guests", "50 people", "120 pax", "80 guests", "200 guests"):
        for budget, value in (("budget 2 lakh", 200000), ("budget 2L", 200000), ("budget 200000", 200000), ("budget ₹2,00,000", 200000), ("budget 1.5 lakh", 150000), ("budget 50k", 50000)):
            n = int(guests.split()[0])
            add(case(split, "clean", [brief(guests=guests, budget=budget)], "exact", {**BASE, "guest_count": n, "budget_total": value}))
    for city, alias in (("Mumbai", "mumbai"), ("Bengaluru", "Bangalore"), ("Bengaluru", "BLR"), ("Hyderabad", "hyd"), ("Jaipur", "jaipur"), ("Ahmedabad", "ahmedabad"), ("Kolkata", "Calcutta"), ("Delhi", "new delhi")):
        add(case(split, "clean_city", [brief(city=alias)], "exact", {**BASE, "city": city}))
    for date, iso in (("12/12/2026", "2026-12-12"), ("2026-12-12", "2026-12-12"), ("12th December 2026", "2026-12-12"), ("Dec 12 2026", "2026-12-12"), ("19 Dec 2026", "2026-12-19")):
        add(case(split, "clean_date", [brief(date=date)], "exact", {**BASE, "event_date": iso}))
    for word, label in (("wedding", None), ("birthday party", None), ("engagement", None), ("corporate offsite", None)):
        add(case(split, "clean_type", [f"{word} for 100 guests in Pune on 12 December 2026, budget 2 lakh"], "exact"))

    # ---- B. budgets that are not safe to read
    unsafe_budgets = ["budget $5000", "budget 5000 dollars", "budget €4000", "budget 15000 AED", "budget 2 million", "budget 2000 per person", "budget 2000 per head", "budget 1500 a plate",
                      "budget 50", "budget 2", "budget 1e6", "budget 2-3 lakh", "budget between 2 and 3 lakh"]
    unit_typos = [f"budget 2 {u}" for u in ("lakhh", "laks", "lackh", "lakkh")] if not held else [f"budget 2 {P('lakh')}" for _ in range(12)]
    for b in unsafe_budgets:
        add(case(split, "budget_unsafe", [brief(budget=b)], "ask", ask_fields=["budget_total"], truth={**BASE, "budget_total": 200000},
                 resolve=[{"press": "2,00,000"}] if "per" in b or "plate" in b or "-" in b or "between" in b else [{"say": "2 lakh"}]))
    for b in unit_typos:
        add(case(split, "budget_unit_typo", [brief(budget=b)], "ask", ask_fields=["budget_total"], truth={**BASE, "budget_total": 200000}, resolve=[{"press": "2,00,000"}], note=b))

    # ---- C. guest counts that are not safe to read
    for g in ("100-150 guests", "100 to 150 guests", "between 100 and 150 guests", "-5 guests", "0 guests", "100.5 guests", "1000000 guests"):
        is_range = g in ("100-150 guests", "100 to 150 guests", "between 100 and 150 guests")
        add(case(split, "guests_unsafe", [brief(guests=g)], "ask", ask_fields=["guest_count"], truth={**BASE, "guest_count": 150},
                 resolve=[{"press": "150"}] if is_range else [{"say": "150 guests"}]))

    # ---- D. cities: typos are suggested, never silently changed
    typo_cities = [("Banglore", "Bengaluru"), ("punee", "Pune"), ("Puna", "Pune"), ("hydrabad", "Hyderabad"), ("chenai", "Chennai"), ("kolkatta", "Kolkata"), ("delih", "Delhi"), ("mumbay", "Mumbai")]
    if held:
        typo_cities = [(P(c), c) for c in KNOWN for _ in range(2)]
    for typed, real in typo_cities:
        add(case(split, "city_typo", [brief(city=typed)], "city_ok", {**BASE, "city": real}, ask_fields=["city"], note=f"{typed} for {real}"))
        add(case(split, "city_typo_answer", ["wedding for 100 guests on 12 December 2026, budget 2 lakh", typed], "city_ok", {**BASE, "city": real}, ask_fields=["city"], note=f"{typed} for {real}"))
    for name in ("Nagpur", "Surat", "Thane", "Nashik", "Indore", "Goa", "Navi Mumbai"):
        for typed in (name, name.lower()):
            add(case(split, "city_unknown", [brief(city=typed)], "city_ok", {**BASE, "city": name}, ask_fields=["city"], note=typed))
    add(case(split, "city_two", [brief(city="Pune or Mumbai")], "ask", ask_fields=["city"], truth={**BASE, "city": "Mumbai"}, resolve=[{"press": "Mumbai"}]))
    add(case(split, "city_two", ["wedding for 100 guests from Mumbai to Pune on 12 December 2026, budget 2 lakh"], "ask", ask_fields=["city"], truth={**BASE, "city": "Pune"}, resolve=[{"press": "Pune"}]))

    # ---- E. allergies and diets (safety, scored apart)
    phrasings = ["one guest is allergic to {f}", "{f} allergy", "allergic to {f}", "{f} se allergy hai"]
    for allergen, foods in ALLERGENS.items():
        for f in foods[:1 if held else 2]:
            for ph in phrasings[: 4 if not held else 3]:
                add(case(split, "allergy_correct", [brief(extra=", " + ph.format(f=f))], "exact", safety={"allergens": [allergen], "mention": True}, truth=dict(BASE)))
    for allergen, f in HINGLISH_FOOD.items():
        add(case(split, "allergy_hinglish", [brief(extra=f", {f} se allergy hai")], "exact", safety={"allergens": [allergen], "mention": True}))
        add(case(split, "allergy_hinglish", [brief(extra=f", {f} ki allergy")], "ask_or_exact", safety={"allergens": [allergen], "mention": True}, ask_fields=["allergens"], resolve=[{"press": allergen.replace("_", " ")}]))
    cue_typos = ["alergic", "allergik", "alergy", "alerjic", "alergik"] if not held else [P(w) for w in ("allergic", "allergy", "allergic", "allergy", "allergic", "allergy", "allergic", "allergy")]
    for cue in cue_typos:
        for allergen, f in (("peanut", "peanuts"), ("egg", "eggs"), ("shellfish", "prawns"), ("gluten", "gluten")):
            ph = f"one guest is {cue} to {f}" if cue.endswith("c") or cue.endswith("k") else f"{f} {cue}"
            add(case(split, "allergy_cue_typo", [brief(extra=", " + ph)], "ask", safety={"allergens": [allergen], "mention": True}, ask_fields=["allergens"], resolve=[{"press": allergen.replace("_", " ")}], note=ph))
    food_typos = [("peanuts", "penuts", "peanut"), ("shellfish", "shelfish", "shellfish"), ("gluten", "glutten", "gluten"), ("sesame", "sesme", "sesame")] if not held else \
        [(f, P(f), a) for a, fs in (("peanut", ["peanuts"]), ("shellfish", ["shellfish", "prawns"]), ("gluten", ["gluten"]), ("sesame", ["sesame"]), ("egg", ["eggs"]), ("tree_nut", ["cashews", "almonds"]), ("dairy", ["dairy"]), ("fish", ["fish"])) for f in fs for _ in range(2)]
    for real, typed, allergen in food_typos:
        add(case(split, "allergy_food_typo", [brief(extra=f", allergic to {typed}")], "ask", safety={"allergens": [allergen], "mention": True}, ask_fields=["allergens"], resolve=[{"press": allergen.replace("_", " ")}], note=typed))
    for ph in ("anaphylaxis", "carries an epipen", "no peanuts please", "without nuts", "no shellfish", "severe reaction to something"):
        add(case(split, "allergy_unnamed_or_exclusion", [brief(extra=", " + ph)], "ask", safety={"allergens": None, "mention": True}, ask_fields=["allergens"], resolve=[{"say": "peanuts"}] if "no " not in ph and "without" not in ph else [{"press": "preference"}]))
    for ph in ("no allergies", "nobody is allergic", "no one has any allergies", "koi allergy nahi hai", "allergies: none", "not allergic to peanuts"):
        add(case(split, "allergy_none", [brief(extra=", " + ph)], "exact", safety={"allergens": [], "mention": True}))
    add(case(split, "allergy_negation_mixed", [brief(extra=", not allergic to peanuts but allergic to eggs")], "exact", safety={"allergens": ["egg"], "mention": True}))
    add(case(split, "allergy_negation_mixed", [brief(extra=", allergic to peanuts, not eggs")], "exact", safety={"allergens": ["peanut"], "mention": True}))
    for ph in ("peanut butter sandwich counter", "a pancake station", "penguin themed cake", "Alice in Wonderland theme", "algorithm quiz night", "egg hunt for the kids"):
        add(case(split, "allergy_lookalike_no_mention", [brief(extra=", " + ph)], "exact", safety={"allergens": [], "mention": False}, note=ph))
    for ph, diet in (("vegetarian", "vegetarian"), ("veg", "vegetarian"), ("vegan", "vegan"), ("jain food", "jain"), ("halal", "halal"), ("non veg only", "non_vegetarian"), ("veg khana", "vegetarian")):
        add(case(split, "diet_correct", [brief(extra=", " + ph)], "exact", safety={"allergens": [], "dietary": [diet], "mention": True}))
    diet_typos = ["vegiterian", "vegitarian", "vegeterian", "vegtarian"] if not held else [P("vegetarian") for _ in range(10)]
    for w in diet_typos:
        add(case(split, "diet_typo", [brief(extra=", " + w)], "ask", safety={"allergens": [], "dietary": ["vegetarian"], "mention": True}, ask_fields=["dietary"], resolve=[{"press": "vegetarian"}], note=w))
    for ph in ("no onion no garlic", "bina pyaaz lehsun", "no beef", "sattvic", "kosher"):
        add(case(split, "diet_note", [brief(extra=", " + ph)], "ask_or_exact", safety={"allergens": [], "dietary": [], "diet_note": True, "mention": True}, note=ph))

    # ---- F. corrections and removals (multi-turn)
    corr = [("sorry, 150 guests", {"guest_count": 150}), ("actually 80 guests", {"guest_count": 80}), ("budget is 3 lakh", {"budget_total": 300000}), ("move it to 19 December 2026", {"event_date": "2026-12-19"}),
            ("actually make it Mumbai", {"city": "Mumbai"}), ("not Pune, Mumbai", {"city": "Mumbai"}), ("not in Pune but in Mumbai", {"city": "Mumbai"}), ("Mumbai instead of Pune", {"city": "Mumbai"}), ("Mumbai", {"city": "Mumbai"})]
    for msg, change in corr:
        add(case(split, "correction", [BRIEF, msg], "exact", {**BASE, **change}))
    for msg, field in (("make it 150", "guest_count"), ("now 80", "guest_count"), ("150", "guest_count")):
        add(case(split, "correction_bare_number", [BRIEF, msg], "ask", {**BASE, "guest_count": int(msg.split()[-1])}, ask_fields=["number"], resolve=[{"press": "guests"}]))
    add(case(split, "correction_nochange", [BRIEF, "sounds good"], "exact"))
    for msg in ("actually nobody is allergic", "remove the peanut allergy", "the peanut allergy no longer applies", "forget the allergy"):
        add(case(split, "allergy_removal", [brief(extra=", allergic to peanuts"), msg], "ask_or_exact", safety={"allergens": ["peanut"], "mention": True, "removal": True}, ask_fields=["allergens"], note=msg))
    # bare answers to the open question
    add(case(split, "bare_answers", ["wedding in Pune", "100", "12 Dec 2026", "200000"], "exact"))
    add(case(split, "bare_answers", ["wedding in Pune", "about 100", "12/12/2026", "2 lakh"], "exact"))
    add(case(split, "bare_answers", ["wedding for 100 guests on 12 December 2026, budget 2 lakh", "pune"], "exact", note="city answered in lower case"))

    # ---- G. Hinglish and number words
    for text, truth in (("Pune mein shaadi hai, 100 log, 12 December ko, budget 2 lakh", dict(BASE)), ("Pune mein shaadi, 100 mehmaan, 12 December 2026, budget 2 lakh", dict(BASE)),
                        ("shaadi 100 log Pune 12/12/2026 2 lakh", dict(BASE))):
        add(case(split, "hinglish", [text], "ask_or_exact", truth, ask_fields=["guest_count", "budget_total", "city"], resolve=[{"say": "100 guests"}]))
    for text in ("do lakh", "teen lakh", "paanch lakh"):
        add(case(split, "hinglish_number_words", [brief(budget=f"budget {text}")], "ask_or_exact", {**BASE, "budget_total": {"do lakh": 200000, "teen lakh": 300000, "paanch lakh": 500000}[text]}, ask_fields=["budget_total"], resolve=[{"say": "2 lakh"}] if text == "do lakh" else []))
    for text, n in (("a hundred guests", 100), ("hundred and twenty guests", 120), ("fifty pax", 50), ("two hundred people", 200)):
        add(case(split, "number_words", [brief(guests=text)], "exact", {**BASE, "guest_count": n}))
    add(case(split, "number_words", [brief(budget="budget two lakh")], "exact"))

    # ---- H. dates that cannot be read
    month_typos = ["Decmber", "Dcember", "Decembr"] if not held else [P("December") for _ in range(6)]
    for m in month_typos:
        add(case(split, "date_month_typo", [f"wedding for 100 guests in Pune on 12 {m} 2026, budget 2 lakh"], "ask_or_exact", ask_fields=["event_date"], resolve=[{"press": "12 December"}], note=m))
    for t in ("tomorrow", "next Saturday", "this weekend", "in December 2026", "12/12", "5/6/2027"):
        add(case(split, "date_unreadable", [f"wedding for 100 guests in Pune {t}, budget 2 lakh"], "ask", {**BASE}, ask_fields=["event_date"], resolve=[{"say": "12 December 2026"}]))

    for i, c in enumerate(cases):
        c["id"] = f"{split}-{i:03d}-{c['group']}"
    return cases


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for split, seed in (("dev", 1), ("heldout", 20261105), ("heldout2", 20261107), ("stress", 20261109), ("stress2", 20261113)):
        cases = generate(split, seed)
        (OUT / f"{split}.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n")
        groups = {}
        for c in cases:
            groups[c["group"]] = groups.get(c["group"], 0) + 1
        print(split, len(cases), "cases,", len(groups), "groups")


if __name__ == "__main__":
    main()
