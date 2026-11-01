"""Red-team finding: several patterns took quadratic time on long hostile input (a 20,000 character run took 5 to 9 seconds). Every compiled
module-level pattern must now stay fast on adversarial input, so a new pattern with the same flaw fails here."""

import importlib
import pkgutil
import re
import time

import pytest

import eventmate
from eventmate.assistant import guard

N = 20_000
LIMIT_S = 1.0
ATTACKS = {
    "a": "a" * N, "space": " " * N, "newline": "\n" * N, "a-": "a-" * (N // 2), "a.": "a." * (N // 2), "1 ": "1 " * (N // 2), "a ": "a " * (N // 2),
    "<a": "<a " * (N // 3), "a@": "a@" * (N // 2), "a.@": "a." * (N // 2) + "@", "words": "word, " * (N // 6), "Caps": "Ab " * (N // 3), ". ": ". " * (N // 2),
    "1,": "1," * (N // 2), "inr": "INR 1 " * (N // 6), "x:": "x: " * (N // 3), "=": "=" * N, "a/": "a/" * (N // 2), "http": "http://a." * (N // 9), '"': '"' * N,
    "'": "'" * N, "(": "(" * N, "\\": "\\" * N, "tab": "\t" * N, "date": "12/12/12 " * (N // 9), "at": "a at b dot " * (N // 11), "ignore": "ignore the " * (N // 11),
}
FULLMATCH_ONLY = {"eventmate.llm._PROMPT_ID", "eventmate.integrity.JSONL_NAME"}  # used only with fullmatch on a short id or a file name, never searched in free text


def module_patterns():
    seen = set()
    for info in pkgutil.walk_packages(eventmate.__path__, "eventmate."):
        if info.name.endswith(".__main__") or ".web.static" in info.name:
            continue
        mod = importlib.import_module(info.name)
        for name, val in vars(mod).items():
            pats = [val] if isinstance(val, re.Pattern) else list(val.values()) if isinstance(val, dict) and val and all(isinstance(v, re.Pattern) for v in val.values()) else []
            for rx in pats:
                if (rx.pattern, rx.flags) not in seen and f"{info.name}.{name}" not in FULLMATCH_ONLY:
                    seen.add((rx.pattern, rx.flags))
                    yield info.name, name, rx


def test_no_module_pattern_takes_quadratic_time_on_hostile_input():
    slow = []
    count = 0
    for module, name, rx in module_patterns():
        count += 1
        for label, text in ATTACKS.items():
            started = time.perf_counter()
            rx.search(text.encode() if isinstance(rx.pattern, bytes) else text)
            if time.perf_counter() - started > LIMIT_S:
                slow.append(f"{module}.{name} on {label!r}")
                break
    assert count > 100, "the sweep found too few patterns to mean anything"
    assert slow == [], slow


@pytest.mark.parametrize("label", ["newline", "a-", "a.@", "at", "ignore", "space"])
def test_the_injection_scan_and_masks_stay_fast_on_a_megabyte_of_hostile_text(label):
    text = (ATTACKS[label] * 50)[:1_000_000]
    started = time.perf_counter()
    guard.scan(text)
    guard.mask_pii(text)
    assert time.perf_counter() - started < 5.0
