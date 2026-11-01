"""Guardrails that wrap the model: injection scan, PII masking, data fencing and an output fact check.

None of these is the main defence. The main defence is that the model has no authority (tools are permission
checked, mutations are proposals). These make attacks visible, mask personal data in logs, tool results and (via placeholders, eventmate/privacy.py)
in what is sent to a model, and stop an answer from stating facts that no tool or document supplied.
"""

from __future__ import annotations

import re
from typing import Iterable

from eventmate.llm import _MARKUP, _NUMBER, _NUMBER_WORDS, links_in

_INJECTION = {
    "override_instructions": re.compile(r"\b(?:ignore|disregard|forget|override|bypass)\b[\s\S]{0,40}?\b(?:instructions?|rules?|prompt|guidelines?|above|previous|prior|earlier|everything)\b", re.I),
    # Other languages and chat-template tokens: cheap signals, and incomplete (a translation or paraphrase can still pass; this is not the defence).
    "override_other_language": re.compile(r"ignora\w*\s[^.\n]{0,30}instrucciones|ignor\w*\s[^.\n]{0,30}instructions|ignorier\w*\s[^.\n]{0,30}anweisungen|अनदेखा|निर्देशों को भूल|निर्देश.{0,20}अनदेखा", re.I),
    "chat_template_token": re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>|\[/?INST\]|<<SYS>>", re.I),
    "role_change": re.compile(r"\b(?:you are now|from now on,? you|act as|pretend to be|new persona|developer mode|jailbreak|system override|new instructions\s*:)", re.I),
    "prompt_exfiltration": re.compile(r"\b(?:reveal|print|show|repeat|leak)\b[^.\n]{0,30}\b(?:system prompt|hidden instructions?|your instructions)\b", re.I),
    "fake_authority": re.compile(r"(?:^|\n)[^\S\n]*(?:#{1,6}[^\S\n]*|\*\*|\[)?(?:system|assistant|developer)\b(?:[^\S\n]*:|\s?\]|\*\*|[^\S\n]*\n)", re.I),  # [^\S\n] not \s: a run of blank lines must not be re-scanned from every line start
    "mass_action": re.compile(r"\b(?:refund|cancel|delete|send|pay|transfer)\b[^.\n]{0,25}\b(?:every|all)\b", re.I),
    "fence_forgery": re.compile(r"<<<|>>>"),
    "tool_forgery": re.compile(r"""["']tool["']\s*:\s*["']""", re.I),
}
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}\b")  # bounded and anchored at a token start: linear time on any input
_EMAIL_SPOKEN = re.compile(r"(?<![\w.+-])[\w.+-]{2,64}\s{0,3}[\[(]?\s{0,3}at\s{0,3}[\])]?\s{0,3}[\w-]{2,63}(?:\s{0,3}[\[(]?\s{0,3}dot\s{0,3}[\])]?\s{0,3}[a-z]{2,}){1,3}\b", re.I)  # "name at gmail dot com" 
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_AADHAAR = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
_PHONE = re.compile(r"(?<![\w.])(?:\+?91[ -]?)?[6-9](?:[ -]?\d){9}(?!\w)")  # separators allowed between any two digits: "98 76 54 32 10", "9-8-7-..."
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")
_FENCE = re.compile(r"<<<|>>>")


_LEET = str.maketrans("013457@$", "oieastas")


def scan(text: str) -> list[str]:
    """Names of injection-like patterns found. A hit marks data as hostile; it never gates anything by itself. Also scans a copy with look-alike
    digits and symbols folded to letters ("1gn0re"), which can only add flags; the text itself is not changed."""
    folded = text.translate(_LEET)
    return [name for name, rx in _INJECTION.items() if rx.search(text) or (folded != text and rx.search(folded))]


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def mask_pii(text: str) -> str:
    """Replace emails, phone numbers, PAN, Aadhaar and card numbers with labelled placeholders."""

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "[card]" if 13 <= len(digits) <= 19 and _luhn(digits) else m.group(0)

    text = _EMAIL_SPOKEN.sub("[email]", _EMAIL.sub("[email]", text))
    text = _PAN.sub("[pan]", text)
    text = _CARD.sub(card, text)
    text = _AADHAAR.sub("[aadhaar]", text)
    return _PHONE.sub("[phone]", text)


def fence(label: str, text: str) -> str:
    """Wrap untrusted text as inert data. The fence tokens are stripped from the text so it cannot close its own fence."""
    return f"<<<{label}\n{_FENCE.sub('', text)}\n{label}>>>"


def check_answer(answer: str, evidence: str, question: str = "", extra_ok: Iterable[str] = ()) -> str | None:
    """Return None if the answer states nothing the evidence did not supply, else the reason it was refused."""
    if not answer.strip():
        return "empty answer"
    if len(answer) > 3000:
        return "answer too long"
    if _MARKUP.search(answer):
        return "markup not allowed"
    source = evidence + " " + question + " " + " ".join(extra_ok)
    if links_in(answer) - links_in(source):
        return "link or address not in the evidence"
    flat = lambda s: {n.replace(",", "") for n in _NUMBER.findall(s)}  # noqa: E731  ("1,20,000" and "120000" are one fact)
    extra = sorted(flat(answer) - flat(source))
    if extra:
        return f"number not in the evidence: {extra[:5]}"
    words = sorted({w.lower() for w in _NUMBER_WORDS.findall(answer)} - {w.lower() for w in _NUMBER_WORDS.findall(source)})
    if words:
        return f"number word not in the evidence: {words[:5]}"
    return None


_CAPS = re.compile(r"(?<![.!?:\n]\s)(?<!^)\b[A-Z][A-Za-z]{2,}\b")
_COMMON = frozenset(
    "january february march april may june july august september october november december monday tuesday wednesday thursday friday saturday sunday "
    "inr eventmate indian india hindi english jain halal vegan vegetarian none yes sorry thanks".split()
)


def introduced_names(answer: str, evidence: str, question: str = "") -> list[str]:
    """Capitalised words in the middle of a sentence that no tool, document or question supplied: a made-up or outside name."""
    known = {w.lower() for w in re.findall(r"[A-Za-z]+", evidence + " " + question)} | _COMMON
    return sorted({w for w in _CAPS.findall(answer) if w.lower() not in known})


class PolicyError(ValueError):
    """The policy file is missing or malformed; the assistant refuses to start rather than run unguarded."""


def _need(condition: bool) -> None:
    """Explicit check that survives ``python -O`` (a bare assert would vanish and let a malformed policy load)."""
    if not condition:
        raise ValueError("policy check failed")


def load_policy(path=None) -> dict:
    import json
    from pathlib import Path

    path = Path(path) if path else Path(__file__).resolve().parent.parent / "data" / "assistant_policy.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        _need(doc["schema_version"] == 1)
        names, phrases = doc["blocked_names"], doc["blocked_phrases"]
        _need(all(isinstance(x, str) and 0 < len(x) <= 80 for x in names + phrases))
        retries, backoff, cap = int(doc["retries"]), float(doc["backoff_seconds"]), int(doc["max_est_tokens_per_user_per_hour"])
        _need(0 <= retries <= 5 and 0 <= backoff <= 5 and cap > 0)
        inputs, preset, min_score = doc["blocked_input_phrases"], doc["preset_reply"], float(doc["min_retrieval_score"])
        _need(all(isinstance(x, str) and 0 < len(x) <= 80 for x in inputs) and isinstance(preset, str) and 0 < len(preset) <= 300 and 0 <= min_score <= 20)
        _need("<" not in preset and ">" not in preset)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PolicyError(f"assistant policy invalid: {type(exc).__name__}") from exc
    return {"blocked_names": tuple(names), "blocked_phrases": tuple(phrases), "retries": retries, "backoff": backoff, "token_cap": cap,
            "blocked_input_phrases": tuple(inputs), "preset_reply": preset, "min_retrieval_score": min_score}


def input_blocked(question: str, policy: dict) -> bool:
    """Input moderation with a preset reply (the Dify "sensitive word" idea): checked before any tool or model runs."""
    low = question.lower()
    return any(p.lower() in low for p in policy["blocked_input_phrases"])


def policy_violation(answer: str, policy: dict) -> str | None:
    """Deny-list check (the competitor and phrase validators): names an organisation never wants said."""
    low = answer.lower()
    for name in policy["blocked_names"]:
        if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", low):
            return f"names a blocked company: {name}"
    for phrase in policy["blocked_phrases"]:
        if phrase.lower() in low:
            return f"uses a blocked phrase: {phrase}"
    return None
