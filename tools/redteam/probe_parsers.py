"""Throw hostile text at every parser of third-party text; report any exception type that is not a deliberate refusal."""
import random, sys, time, traceback
sys.path.insert(0, ".")
from datetime import datetime, timezone
from eventmate.messaging.conversations import analyze_reply
from eventmate.messaging.channels import clean_reply_text, _strip_html
from eventmate.integrations.deals import extract_mentions
from eventmate.integrations.web_access import extract_text
from eventmate.integrations.websearch import DuckDuckGoHtml
from eventmate.sourcing.contacts import find_contacts
from eventmate.followthrough.claims import extract_claims
from eventmate.guestlist import parse_guest_csv
from eventmate.agents.manager import parse_request
from eventmate.schemas import InboundMessage, VendorCategory

r = random.Random(7)
ATOMS = ["Rs", "₹", "INR", "1,00,000", "1e999", "999999999999999999999999", "-5", "0", "NaN", "\x00", "\u202e", "\ufeff", "<", ">", "<<<", ">>>", "&amp;", "&#x110000;", "&#0;", "&#xD800;", "<script>", "</", "<!--", "-->",
         "<![CDATA[", "<a href='", "mailto:", "javascript:", "@", "a@b.co", "per plate", "per day", "lakh", "crore", "k", "stop", "unsubscribe", "\n> ", "\nFrom: x\nSent: y\n", "\r", "\t", "💥", "𝕬", "ß", "İ", "ǅ", "\U0010ffff", "a" * 300, "9" * 60, "%", "{", "}", "[", "]", "\\", "'", '"']
def junk(n):
    return "".join(r.choice(ATOMS) if r.random() < .6 else chr(r.randrange(32, 0x2fff)) for _ in range(n))

def msg(body):
    return InboundMessage(message_id="m1", external_id="x1", plan_id="p", draft_id="d", vendor_id="v", vendor_name="V", category=VendorCategory.CATERER, channel="email", sender="a@b.co", received_at=datetime(2026, 11, 1, tzinfo=timezone.utc), body=body[:4000])

class Fetch:
    def __init__(self, html): self.html = html
    def fetch_html(self, url):
        from eventmate.integrations.web_access import RawPage
        return RawPage(url, "t", "x", self.html)

bad = {}
def run(name, fn):
    t = time.perf_counter()
    try:
        fn()
    except Exception as e:
        key = (name, type(e).__name__)
        bad.setdefault(key, (str(e)[:80], traceback.format_exc().splitlines()[-3:]))
    d = time.perf_counter() - t
    if d > 2: bad[(name, "SLOW")] = (f"{d:.1f}s", [])

for i in range(3000):
    s = junk(r.randrange(0, 400))
    run("analyze_reply", lambda: analyze_reply(s, ("service staff", "taxes")))
    run("clean_reply_text", lambda: clean_reply_text(s))
    run("_strip_html", lambda: _strip_html(s))
    run("extract_mentions", lambda: extract_mentions(s))
    run("extract_text", lambda: extract_text(s))
    run("ddg", lambda: DuckDuckGoHtml(lambda q: s).search("x"))
    run("find_contacts", lambda: find_contacts(Fetch(s), "https://example.com/"))
    run("extract_claims", lambda: extract_claims(msg(s)))
    run("parse_guest_csv", lambda: parse_guest_csv(s))
    run("parse_request", lambda: parse_request(s[:1500]))
for k, v in sorted(bad.items()): print(k, v)
print("done; distinct problem classes:", len(bad))
