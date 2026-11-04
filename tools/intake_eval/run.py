"""Score the intake reader on the labelled cases: ``python tools/intake_eval/run.py [--root CHECKOUT] [--splits dev heldout] [--json OUT] [--md OUT]``

It plays each case the way the page does (type a message, press a button, send back the saved draft and the open question) and compares the end state with the label. ``--root``
points at any checkout of the repo, so the same cases can be run on an older commit; an older reader has no buttons, so a case that needs a press then counts as not recovered.

Reported apart, because they answer different questions:
* overall: exact-fact accuracy on cases that should simply be read, wrong values set silently, questions asked when none were needed, recovery within two steps;
* safety: for every case that mentions an allergy or a diet, was it captured, asked about, or silently missed; was an allergy invented; was one cleared without confirmation;
* the Build rule: a draft that says it is ready while a question is open (must be 0).
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

TODAY = date(2026, 10, 30)
KNOWN = ("Pune", "Mumbai", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Delhi", "Jaipur", "Ahmedabad")
FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "intake_labelled"
FACTS = ("guest_count", "city", "event_date", "budget_total")


class Player:
    """One conversation against one checkout of the reader."""

    def __init__(self, root: Path, reload: bool = True):
        if reload:  # a different checkout is imported fresh; a test that scores the checkout it runs in keeps what it already has
            sys.path.insert(0, str(root))
            for name in [n for n in sys.modules if n == "eventmate" or n.startswith("eventmate.")]:
                del sys.modules[name]
        self.intake = importlib.import_module("eventmate.planning.intake")
        logs_mod = importlib.import_module("eventmate.logs")
        self.logs = logs_mod.RunLogs.create(log_dir=tempfile.mkdtemp(), run_id="eval")
        self.modern = "expecting" in inspect.signature(self.intake.propose).parameters
        self.has_buttons = hasattr(self.intake, "IntakeAnswer")

    def say(self, draft, asking, text):
        kw = {"expecting": asking} if self.modern and asking else {}
        return self.intake.propose([text], draft, TODAY, KNOWN, self.logs, None, **kw)

    def press(self, turn, label):
        if not self.has_buttons:
            return None
        for u in turn.draft.unresolved:
            for i, p in enumerate(u.picks):
                if label.lower() in p.label.lower():
                    return self.intake.propose(["x"], turn.draft, TODAY, KNOWN, self.logs, None, expecting=getattr(turn, "asking", None), answer=self.intake.IntakeAnswer(key=u.key, pick=i))
        return None

    def play(self, steps):
        turn, no_button = None, False
        for st in steps:
            if "say" in st:
                turn = self.say(turn.draft if turn else None, getattr(turn, "asking", None) if turn else None, st["say"])
            else:
                nxt = self.press(turn, st["press"]) if turn else None
                if nxt is None:
                    no_button = True
                    break
                turn = nxt
        return turn, no_button


def open_fields(turn) -> set[str]:
    d = turn.draft
    return {u.field for u in getattr(d, "unresolved", ())} | {c.field for c in turn.choices} | set(turn.missing)


def safety_asked(turn) -> bool:
    d = turn.draft
    return bool({u.field for u in getattr(d, "unresolved", ())} & {"allergens", "dietary"}) or bool(d.allergy_unclear or d.diet_unclear or d.diet_notes)


def facts(turn) -> dict:
    d = turn.draft
    return {"guest_count": d.guest_count, "city": d.city, "event_date": d.event_date.isoformat() if d.event_date else None, "budget_total": d.budget_total}


def reached(turn, truth) -> bool:
    return turn is not None and turn.ready and all(facts(turn)[k] == v for k, v in truth.items())


def score_case(player: Player, c: dict) -> dict:
    turn, no_button = player.play(c["steps"])
    out = {"id": c["id"], "group": c["group"], "expect": c["expect"], "note": c.get("note", "")}
    f, truth, asked_on = facts(turn), c["truth"], open_fields(turn)
    unresolved = len(getattr(turn.draft, "unresolved", ()))
    out["questions_open"] = unresolved + len(turn.choices) if not hasattr(turn.draft, "unresolved") else unresolved
    out["ready_with_open_question"] = bool(turn.ready and (unresolved or turn.choices))
    exact = all(f[k] == v for k, v in truth.items()) and turn.ready
    wrong = [k for k, v in f.items() if v is not None and k in truth and v != truth[k]]
    if c["expect"] == "exact":
        out["outcome"] = "pass" if exact else ("wrong_value" if wrong else "dropped_or_asked")
        out["false_ask"] = bool(not exact and not wrong)
    elif c["expect"] == "city_ok":
        written = (c["note"].split(" for ")[0] if " for " in c["note"] else c["note"]).strip().lower()
        city = (f["city"] or "").lower()
        out["outcome"] = "pass" if (not city or city == written) else "wrong_value"
        out["asked"] = not city
    elif c["expect"] in ("ask", "ask_or_exact"):
        if c["expect"] == "ask_or_exact" and exact:
            out["outcome"], out["read_directly"] = "pass", True
        else:
            fields, d = c["ask_fields"], turn.draft
            asked = bool(set(fields) & asked_on) or (bool(set(fields) & {"allergens", "dietary"}) and safety_asked(turn))
            guessed = False  # a value was set for something the user wrote in a way that has no single safe reading
            for k in fields:
                if k in FACTS and f[k] is not None and (c["expect"] == "ask" or f[k] != truth.get(k)):
                    guessed = True
                if k == "number" and (f["guest_count"] != 100 or f["budget_total"] != 200000):
                    guessed = True  # "make it 150": the guest count or budget moved without being asked
                if k == "allergens" and d.allergens and not asked:
                    guessed = True
                if k == "dietary" and d.dietary and not asked:
                    guessed = True
            out["outcome"] = "wrong_value" if guessed else ("pass" if asked else "dropped")
            out["asked"] = asked
        if c["resolve"] and out["outcome"] == "pass" and not out.get("read_directly"):
            t2, nb = turn, False
            for st in c["resolve"][:2]:
                t2 = player.press(t2, st["press"]) if "press" in st else player.say(t2.draft, getattr(t2, "asking", None), st["say"])
                if t2 is None:
                    nb = True
                    break
            out["recovered"] = bool(t2 is not None and not nb and reached(t2, c["truth"]))
        elif c["resolve"] and not out.get("read_directly"):
            out["recovered"] = False
    out["no_button"] = no_button
    s = c.get("safety")
    if s:
        d = turn.draft
        got = sorted(a.value for a in d.allergens)
        want = s.get("allergens")
        so = {"mention": s.get("mention", False)}
        if s.get("removal"):
            so["removal"] = "cleared" if not got else ("asked" if ("allergens" in asked_on) else "kept_silent")
        elif want is None:
            so["kind"] = "unnamed"
            so["captured"], so["asked"] = False, safety_asked(turn)
            so["invented"] = bool(got)
        elif want:
            so["kind"] = "allergy"
            so["captured"] = got == sorted(want) and not ("allergens" in asked_on)
            so["asked"] = safety_asked(turn)
            so["silent_miss"] = not so["captured"] and not so["asked"] and got != sorted(want)
            so["invented"] = bool(set(got) - set(want))
            so["guessed_without_asking"] = c["expect"] == "ask" and got == sorted(want) and not so["asked"]
        elif s.get("mention") is False:
            so["kind"] = "lookalike"
            so["invented"] = bool(got)
            so["false_alarm"] = bool({"allergens", "dietary"} & asked_on) or bool(d.allergy_unclear)
        elif s.get("dietary"):
            so["kind"] = "diet"
            dd = sorted(x.value for x in d.dietary)
            so["captured"] = dd == sorted(s["dietary"])
            so["asked"] = bool({u.field for u in getattr(d, "unresolved", ())} & {"dietary"}) or bool(d.diet_unclear)
            so["silent_miss"] = not so["captured"] and not so["asked"]
        elif s.get("diet_note"):
            so["kind"] = "diet_note"
            so["captured"] = bool(d.diet_notes)
            so["asked"] = bool(d.diet_unclear) or "dietary" in asked_on
            so["silent_miss"] = not so["captured"] and not so["asked"]
        else:
            so["kind"] = "none"
            so["invented"] = bool(got)
        out["safety"] = so
    return out


def summarize(results: list[dict]) -> dict:
    by_group = defaultdict(Counter)
    for r in results:
        by_group[r["group"]][r["outcome"]] += 1
    n = len(results)
    exact_cases = [r for r in results if r["expect"] == "exact"]
    ask_cases = [r for r in results if r["expect"] == "ask"]
    rec = [r for r in results if "recovered" in r]
    safety = [r for r in results if "safety" in r]
    allergy = [r["safety"] for r in safety if r["safety"].get("kind") == "allergy"]
    unnamed = [r["safety"] for r in safety if r["safety"].get("kind") == "unnamed"]
    diets = [r["safety"] for r in safety if r["safety"].get("kind") in ("diet", "diet_note")]
    lookalike = [r["safety"] for r in safety if r["safety"].get("kind") == "lookalike"]
    none_cases = [r["safety"] for r in safety if r["safety"].get("kind") == "none"]
    removal = Counter(r["safety"]["removal"] for r in safety if "removal" in r["safety"])
    return {
        "cases": n,
        "pass": sum(r["outcome"] == "pass" for r in results),
        "wrong_value_set_silently": sum(r["outcome"] == "wrong_value" for r in results),
        "dropped_silently": sum(r["outcome"] == "dropped" for r in results),
        "exact_cases": len(exact_cases), "exact_pass": sum(r["outcome"] == "pass" for r in exact_cases),
        "false_asks_on_cases_that_should_just_be_read": sum(bool(r.get("false_ask")) for r in exact_cases),
        "ask_cases": len(ask_cases), "ask_cases_that_asked": sum(r["outcome"] == "pass" for r in ask_cases),
        "recovery_cases": len(rec), "recovered_within_two_steps": sum(bool(r["recovered"]) for r in rec),
        "no_button_available": sum(r["no_button"] for r in results),
        "ready_with_open_question": sum(r["ready_with_open_question"] for r in results),
        "safety": {
            "allergy_mentions": len(allergy), "allergy_captured_exactly": sum(s["captured"] for s in allergy), "allergy_asked_instead": sum(s["asked"] and not s["captured"] for s in allergy),
            "allergy_silently_missed": sum(s["silent_miss"] for s in allergy), "allergy_invented": sum(s["invented"] for s in allergy),
            "unnamed_or_exclusion_mentions": len(unnamed), "unnamed_asked": sum(s["asked"] for s in unnamed), "unnamed_silently_missed": sum(not s["asked"] for s in unnamed),
            "diet_mentions": len(diets), "diet_captured": sum(s["captured"] for s in diets), "diet_asked_instead": sum(s["asked"] and not s["captured"] for s in diets), "diet_silently_missed": sum(s["silent_miss"] for s in diets),
            "none_or_negated_cases": len(none_cases), "invented_from_a_negation": sum(s["invented"] for s in none_cases),
            "lookalike_cases": len(lookalike), "allergy_invented_from_lookalike": sum(s["invented"] for s in lookalike), "false_alarm_on_lookalike": sum(s["false_alarm"] for s in lookalike),
            "removal_requests": sum(removal.values()), "removal_outcomes": dict(removal),
        },
        "groups": {g: dict(c) for g, c in sorted(by_group.items())},
    }


def markdown(name: str, s: dict, results: list[dict]) -> str:
    sf = s["safety"]
    pct = lambda a, b: f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")  # noqa: E731
    lines = [f"### {name}: {s['cases']} cases", "",
             "| measure | result |", "|---|---|",
             f"| cases that should simply be read: read exactly | {pct(s['exact_pass'], s['exact_cases'])} |",
             f"| ...of those, a question asked that was not needed | {s['false_asks_on_cases_that_should_just_be_read']} |",
             f"| cases that should be asked about: asked, no value guessed | {pct(s['ask_cases_that_asked'], s['ask_cases'])} |",
             f"| a wrong value set silently (any case) | {s['wrong_value_set_silently']} |",
             f"| a fact dropped silently | {s['dropped_silently']} |",
             f"| recovered to the right answer within two steps | {pct(s['recovered_within_two_steps'], s['recovery_cases'])} |",
             f"| Build offered while a question is open | {s['ready_with_open_question']} |",
             "", "**Safety (scored apart)**", "", "| measure | result |", "|---|---|",
             f"| allergy mentioned, captured exactly | {pct(sf['allergy_captured_exactly'], sf['allergy_mentions'])} |",
             f"| allergy mentioned, asked about instead | {pct(sf['allergy_asked_instead'], sf['allergy_mentions'])} |",
             f"| **allergy mentioned, silently missed** | **{pct(sf['allergy_silently_missed'], sf['allergy_mentions'])}** |",
             f"| allergy invented that the user did not state | {sf['allergy_invented']} |",
             f"| serious reaction or 'no peanuts' with no stated allergy: asked | {pct(sf['unnamed_asked'], sf['unnamed_or_exclusion_mentions'])} |",
             f"| diet mentioned: captured / asked / silently missed | {sf['diet_captured']} / {sf['diet_asked_instead']} / {sf['diet_silently_missed']} of {sf['diet_mentions']} |",
             f"| 'no allergies', 'not allergic to X': allergy invented | {sf['invented_from_a_negation']} of {sf['none_or_negated_cases']} |",
             f"| look-alike words (peanut butter, pancake): allergy invented / false alarm | {sf['allergy_invented_from_lookalike']} / {sf['false_alarm_on_lookalike']} of {sf['lookalike_cases']} |",
             f"| asked to remove a stated allergy | {sf['removal_outcomes']} |", "", "**By group** (pass / wrong_value / dropped / dropped_or_asked)", "", "| group | outcomes |", "|---|---|"]
    lines += [f"| {g} | {', '.join(f'{k} {v}' for k, v in c.items())} |" for g, c in s["groups"].items()]
    bad = [r for r in results if r["outcome"] not in ("pass",)]
    lines += ["", f"**Not passing ({len(bad)})**", ""] + [f"- `{r['id']}` {r['outcome']} {r['note']}" for r in bad[:80]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--splits", nargs="+", default=["dev", "heldout"])
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args()
    player = Player(Path(a.root))
    report, md = {}, [f"# Intake reader on the labelled cases\n\nReader checkout: `{a.root}` (has buttons: {player.has_buttons}, remembers the open question: {player.modern}). Rule reader only; no model.\n"]
    for split in a.splits:
        cases = json.loads((FIXTURES / f"{split}.json").read_text())
        results = [score_case(player, c) for c in cases]
        report[split] = {"summary": summarize(results), "results": results}
        md.append(markdown(split, report[split]["summary"], results))
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    text = "\n".join(md)
    if a.md:
        Path(a.md).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
