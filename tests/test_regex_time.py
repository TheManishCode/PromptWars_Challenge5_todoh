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


def test_the_two_link_patterns_are_bounded_and_do_the_same_work_on_ordinary_text():
    """llm._LINK and ideas._LINK were the same quadratic pattern; the sweep above only catches it when the machine is busy enough to push it past a second, so this
    checks the work directly: ten times the input must cost far less than a hundred times the time."""
    from eventmate import llm
    from eventmate.planning import ideas

    for rx in (llm._LINK, ideas._LINK):
        small, large = "a-" * 2_000, "a-" * 20_000
        t0 = time.perf_counter()
        rx.search(small)
        t_small = max(time.perf_counter() - t0, 1e-4)
        t0 = time.perf_counter()
        rx.search(large)
        assert time.perf_counter() - t0 < t_small * 40, rx.pattern  # linear would be 10x; quadratic is 100x
        assert rx.search("write to asha@example.com today") and rx.search("see https://x.example") and not rx.search("no links here")
