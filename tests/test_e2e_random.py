"""A seeded random end-to-end pass: brief -> plan -> change requests -> squeezed budget -> trade-offs, with invariants that a stated safety fact is never lost.

It found the allergy reader dropping all but the first item of a list, a negation that cancelled a later diet ("no onion no garlic vegetarian" lost vegetarian) and a bare
"under 150000" budget that was refused. Every case is a seed, so a failure replays: run ``run(seed, n, tmp)`` and read the returned bug list.
"""

import random
import re
from datetime import date, timedelta

import pytest

from eventmate.planning import allergies, diets
from eventmate.planning.delta import formalize_delta
from eventmate.schemas import DietaryRequirement as DR
from eventmate.schemas import VendorCategory as VC
from eventmate.service import EventMate

TODAY = date(2026, 10, 30)
TYPES = ["engagement party", "wedding", "birthday party", "conference", "corporate offsite", "shaadi", "birthday", "reception"]
ALLERGY = ["", "", "", " My mother is allergic to peanuts.", " Allergic to peanuts, cashews and eggs.", " nut allergy in the family", " allergic to gluten", " mungfali se allergy hai", " one guest is allergic to kiwi",
           " allergic to peanuts and strawberries", " kaju se allergy hai", " no allergies", " nobody is allergic", " lactose intolerant guests", " shellfish allergy, vegetarian"]
DIET = ["", "", " no onion no garlic vegetarian catering.", " no pork, halaal", " Vegetarian catering.", " veg only", " Jain food.", " satvik khana", " no onion no garlic", " kosher",
        " Dietary restrictions: low iodine.", " non veg allowed", " eggless cake", " pure veg and fasting menu"]
CHANGES = ["now {g} guests", "make the budget {b}", "move it to {ds}", "the caterer pulled out", "ab {g} log honge", "budget {b} kar do", "caterer ne mana kar diya", "hello", "also book a florist the week before",
           "cancel everything", "{g} guests and budget {b}"]
NEG = re.compile(r"\b(?:no|not|none|nobody|never|nahi|without)\b", re.I)
SAFETY = ("allergens", "allergy_unclear", "diet_notes", "diet_unclear", "dietary")


def run(seed: int, n: int, root) -> tuple[dict, list]:
    r = random.Random(seed)
    em = EventMate(log_dir=root / "l", plan_dir=root / "p", run_id="fz")
    cities = list(em.repo.cities())
    avail = {c: {d for d in set.intersection(*[{d for p in em.repo.providers(k) if p.city == c for d in p.available_dates} for k in (VC.VENUE, VC.CATERER, VC.PHOTOGRAPHER)])
                 if TODAY + timedelta(days=5) < d < TODAY + timedelta(days=360)} for c in cities}
    bugs, stats = [], {"briefs": n, "planned": 0, "changed": 0, "tradeoff": 0}

    def bug(kind, text, **kw):
        bugs.append({"kind": kind, "text": text, **kw})

    def money(x):
        f = r.choice(["lakh", "plain", "comma", "k", "hazaar"])
        if f == "lakh" and x % 100000 == 0:
            return f"{x // 100000} lakh"
        if f == "k":
            return f"{x // 1000}k"
        if f == "hazaar":
            return f"{x // 1000} hazaar"
        return f"INR {x:,}" if f == "comma" else f"Rs {x}"  # a bare number is never a budget (test_parse_gaps): ambiguity is an error

    for _ in range(n):
        c, g, b = r.choice(cities), r.choice([20, 30, 40, 50, 80, 100]), r.choice([200, 300, 500, 800]) * 1000
        d = r.choice(sorted(avail[c])) if r.random() < .85 else TODAY + timedelta(days=r.randint(7, 200))
        ds, t = f"{d.day} {d.strftime('%B')}", r.choice(TYPES)
        text = f"{c} mein {g} log ka {t} {ds} ko, budget {money(b)}." if r.random() < .25 else f"Plan a {g}-person {t} in {c} on {ds} under {money(b)}."
        text += r.choice(["", " Photographer please.", " indoor venue", " outdoor", " reminders for invitations and vendor confirmation"]) + r.choice(DIET) + r.choice(ALLERGY)
        low = text.lower()
        try:
            a, dd = allergies.read(text), diets.read(text)
            if re.search(r"\ballerg(?:y|ies|ic)\b", text, re.I) and not NEG.search(text) and not (a.allergens or a.unclear):
                bug("allergy_silently_dropped", text)
            if "intoleran" in low and not (a.allergens or a.unclear):
                bug("intolerance_dropped", text)
            for word, label in (("satvik", "sattvic"), ("kosher", "kosher"), ("eggless", "eggless")):
                if word in low and label not in dd.notes:
                    bug("diet_dropped", text, word=word)
            if "restrictions: low iodine" in low and "low iodine" not in dd.unclear:
                bug("diet_unclear_dropped", text)
            parsed = em.manager.parse(text, TODAY)
            for pat, want in (("vegetarian catering", DR.VEGETARIAN), ("veg only", DR.VEGETARIAN), ("pure veg", DR.VEGETARIAN), ("jain food", DR.JAIN), ("halaal", DR.HALAL), ("non veg allowed", DR.NON_VEGETARIAN)):
                if pat in low and want not in parsed.dietary:
                    bug("hard_diet_dropped", text, want=want.value)
            plan = em.plan(text, today=TODAY).plan
            if plan is None:
                continue
            stats["planned"] += 1
            req = plan.request
            if (req.guest_count, req.budget_total, req.event_date) != (g, b, d):
                bug("misread", text, got=(req.guest_count, req.budget_total, str(req.event_date)), want=(g, b, str(d)))
            if not plan.validation.passed or plan.total_cost > req.budget_total:
                bug("plan_not_valid_or_over_budget", text)
            un = {u.name for u in plan.validation.unverified}
            if (req.allergens and "allergens_unconfirmed" not in un) or (req.allergy_unclear and "allergy_unclear" not in un) or (req.diet_notes and "diet_unconfirmed" not in un) or (req.diet_unclear and "diet_unclear" not in un):
                bug("provisional_flag_missing", text, un=sorted(un))
            if plan.validation.fully_verified and (req.allergens or req.allergy_unclear or req.diet_notes or req.diet_unclear):
                bug("fully_verified_despite_flags", text)
            for _ in range(r.randint(1, 3)):
                when = d + timedelta(days=r.randint(1, 20))
                ch = r.choice(CHANGES).format(g=r.choice([25, 60, 90, 140]), b=money(r.choice([90, 150, 250]) * 1000), ds=f"{when.day} {when.strftime('%B')}")
                delta = getattr(formalize_delta(ch, plan, TODAY), "delta", None)
                stats["changed"] += 1
                if delta is not None and not delta.empty():
                    new = getattr(em.apply_change(plan.plan_id, delta, today=TODAY), "plan", None)
                    if new is not None:
                        for f in SAFETY:
                            if set(getattr(new.request, f)) != set(getattr(req, f)):
                                bug("safety_field_lost_on_change", text, field=f, change=ch)
                        if not new.validation.passed:
                            bug("changed_plan_not_passed", text, change=ch)
                        plan, req = new, new.request
            squeezed = re.sub(r"(under|budget)\s+[^.,]*", lambda m: m.group(1) + " INR 20,000", text, count=1)
            rep = em.tradeoffs(squeezed, today=TODAY)
            if rep.status == "options":
                stats["tradeoff"] += 1
                o = rep.options[r.randrange(len(rep.options))]
                applied = em.apply_tradeoff(o, today=TODAY)
                if applied.status != "planned":
                    bug("verified_option_not_plannable", squeezed, kind_=o.kind.value)
                elif applied.plan:
                    for f in SAFETY:
                        if set(getattr(applied.plan.request, f)) != set(getattr(rep.request, f)):
                            bug("safety_field_lost_in_tradeoff", squeezed, field=f)
        except Exception as exc:  # noqa: BLE001 - any exception on a random brief is a finding
            bug("exception", text, err=f"{type(exc).__name__}: {str(exc)[:160]}")
    return stats, bugs


@pytest.mark.parametrize("seed", [101, 102, 103])
def test_random_briefs_changes_and_tradeoffs_never_lose_a_stated_safety_fact(seed, tmp_path):
    stats, bugs = run(seed, 40, tmp_path)
    assert stats["planned"] >= 10 and stats["changed"] >= 10, stats  # the pass must reach the plan, the change and the trade-off stages to mean anything
    assert bugs == [], bugs[:3]
