"""Phase 4.6: a provider or library error that echoes the key it was sent never reaches a log, trace or answer."""

import urllib.error
from pathlib import Path

import pytest

from eventmate.assistant.agent import Assistant
from eventmate.assistant.identity import LOCAL_ORGANIZER
from eventmate.env import redact_secrets
from eventmate.llm import LLMError, OpenAICompatClient, UsageMeter
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY

KEY = "nvapi-SECRETKEY1234567890abcdef"


class Echoing:
    """A provider whose every error repeats the key and a URL with a query secret, as some real ones do."""

    name = "echo"

    def complete(self, system, user, max_tokens):
        raise RuntimeError(f"401 Unauthorized for Bearer {KEY} at https://api.example/v1?api_key={KEY}&x=1")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)


def leaks(root: Path):
    return [str(p) for p in root.rglob("*") if p.is_file() and KEY.encode() in p.read_bytes()]


def test_redact_removes_environment_secrets_and_common_key_shapes():
    out = redact_secrets(f"fail {KEY} Bearer abc.def-ghi https://h/p?token=zzz&a=1 sk-abcdefghijklmnop1234 AIza{'x' * 30}")
    assert KEY not in out and "abc.def" not in out and "zzz" not in out and "sk-abcdef" not in out and "AIzax" not in out and "a=1" in out
    assert redact_secrets("a plain sentence, 12345") == "a plain sentence, 12345"
    assert redact_secrets(f"{KEY}", environ={}) == "[secret]"  # key-shaped even when the environment does not hold it


def test_a_provider_error_that_echoes_the_key_never_reaches_a_log_or_an_explanation(tmp_path):
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="leak", llm=Echoing())
    pid = em.plan(EXAMPLE, today=TODAY).plan.plan_id
    expl = em.explain(pid) if hasattr(em, "explain") else None
    em.ideas.generate(em.store.latest(pid))
    a = Assistant(em, {"large": Echoing(), "small": Echoing()}, policy={**__import__("eventmate.assistant.guard", fromlist=["x"]).load_policy(), "retries": 0, "backoff": 0})
    r = a.ask("compare the caterer options for me", pid, LOCAL_ORGANIZER)
    assert KEY not in r.answer and KEY not in (r.degraded_reason or "")
    assert leaks(tmp_path) == []
    assert expl is None or KEY not in str(expl)


def test_the_http_client_never_puts_a_provider_body_or_key_in_its_message():
    def echo_body(*a):
        raise urllib.error.HTTPError("https://x.test", 401, "bad", {}, __import__("io").BytesIO(f'{{"error":"invalid key {KEY}"}}'.encode()))

    c = OpenAICompatClient("n", "https://x.test/v1", KEY, "m", transport=echo_body, meter=UsageMeter(5))
    with pytest.raises(LLMError) as exc:
        c.complete("s", "u", 50)
    assert KEY not in str(exc.value) and KEY not in repr(c)
