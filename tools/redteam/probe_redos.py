"""Time regex-bearing text paths on adversarial input of the size an attacker can deliver. Prints anything slower than 0.25 s."""
import sys, time
sys.path.insert(0, ".")
from eventmate.assistant import guard
from eventmate.privacy import Placeholders
from eventmate.integrations.deals import extract_mentions
from eventmate.integrations.web_access import extract_text
from eventmate.sourcing.contacts import _TEXT_EMAIL
from eventmate.messaging.channels import clean_reply_text
from eventmate.voice.speech import strip_markup
from eventmate.guestlist import parse_guest_csv
from eventmate.agents.manager import parse_request
import contextlib

N = int(__import__("os").environ.get("REDOS_SIZE", "60000"))  # 200000 reproduces the F-001 numbers but takes minutes while F-001 is open
PAY = {
 "a*": "a" * N, "digits": "1" * N, "spaces": " " * N, "dots": "." * N, "at": "a@" * (N // 2),
 "email_long_local": "a" * 5000 + "@" + "b." * 5000, "email_dots": "a." * 50000 + "@x",
 "labels": ("a" * 63 + ".") * 3000 + "!", "digit_sp": "1 " * 100000, "digit_dash": "1-" * 100000,
 "rs": "Rs." * 60000, "inr": "INR 1," * 40000, "price_k": ("₹" + "1" * 20 + " ") * 8000,
 "words": "price " * 40000, "lines": "From: x\nSent: y\n" * 20000, "quote": "> " * 100000,
 "md": "[" * 50000 + "](" * 50000, "stars": "*" * N, "under": "_" * N, "tick": "`" * N,
 "html": "<a href=" * 30000, "html2": "<" * N, "mailto": "mailto:" * 30000,
}
def t(name, fn, arg):
    s = time.perf_counter()
    with contextlib.suppress(Exception):
        fn(arg)
    d = time.perf_counter() - s
    return d

targets = {
 "guard.scan": guard.scan, "guard.mask_pii": guard.mask_pii, "Placeholders.mask": lambda s: Placeholders().mask(s),
 "extract_mentions": extract_mentions, "extract_text": extract_text, "TEXT_EMAIL": lambda s: list(_TEXT_EMAIL.finditer(s)),
 "clean_reply_text": clean_reply_text, "strip_markup": strip_markup, "parse_guest_csv": lambda s: parse_guest_csv(s[:262144]),
 "parse_request": lambda s: parse_request(s[:1000]) if True else None,
}
rows = []
for tn, fn in targets.items():
    for pn, p in PAY.items():
        d = t(pn, fn, p)
        rows.append((d, tn, pn))
rows.sort(reverse=True)
for d, tn, pn in rows[:25]:
    print(f"{d:8.3f}s  {tn:20s} {pn}")
