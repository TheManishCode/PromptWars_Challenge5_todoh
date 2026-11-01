"""Property-based tests (hypothesis): statements that must hold for ANY input, not the handful an author thought of. Each is a security or correctness rule stated once and
then attacked with generated text, bytes and addresses. Examples that failed in the past are kept by hypothesis in its local database; the seeds here are derandomised in CI
(HYPOTHESIS_PROFILE=ci) so a failure is reproducible."""

import ipaddress
import os
import unicodedata
from datetime import date

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings, strategies as st  # noqa: E402

from eventmate.agents.manager import RequestParseError, parse_request  # noqa: E402
from eventmate.assistant.guard import check_answer  # noqa: E402
from eventmate.guestlist import GuestListError, parse_guest_csv  # noqa: E402
from eventmate.untrusted import UntrustedTextRejected, normalize_untrusted  # noqa: E402
from eventmate.web import limits  # noqa: E402

settings.register_profile("ci", max_examples=200, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("dev", max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

TEXT = st.text(max_size=600)
HOSTILE = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=400)


@given(HOSTILE)
def test_parsing_free_text_either_gives_a_valid_request_or_a_named_refusal(text):
    try:
        req = parse_request(text, today=date(2026, 11, 1))
    except RequestParseError:
        return
    assert req.guest_count >= 1 and req.budget_total > 0 and req.event_date >= date(2026, 11, 1)


@given(st.text(max_size=300))
def test_normalising_untrusted_text_is_idempotent_and_leaves_no_hidden_characters(text):
    try:
        once = normalize_untrusted(text)
    except UntrustedTextRejected:
        return
    assert normalize_untrusted(once) == once
    assert not any(unicodedata.category(c) in ("Cc", "Cf") and c not in "\n\t" for c in once), repr(once)
    assert len(once) <= 4000


@given(st.text(max_size=20), st.integers(min_value=4001, max_value=9000))
def test_over_long_text_is_refused_before_any_work(ch, n):
    with pytest.raises(UntrustedTextRejected):
        normalize_untrusted((ch or "a") * n)


@given(st.binary(max_size=3000))
def test_a_guest_file_of_any_bytes_gives_a_result_or_a_named_refusal_never_a_crash(raw):
    try:
        result = parse_guest_csv(raw)
    except GuestListError:
        return
    assert hasattr(result, "errors")


@given(st.text(max_size=200), st.lists(st.sampled_from(["198.51.100.7", "203.0.113.9", "10.0.0.1", "::1", "not an ip", "", "999.1.1.1"]), max_size=6))
def test_forwarded_headers_from_an_untrusted_peer_never_change_the_address(header_noise, hops):
    """The spoofing rule: with no trusted proxy (or a peer that is not one) the caller is the connection's own address, whatever X-Forwarded-For says."""
    xff = ", ".join(hops) + header_noise
    assert limits.client_address("192.0.2.5", xff, []) == "192.0.2.5"
    trusted = [ipaddress.ip_network("10.9.9.0/24")]
    assert limits.client_address("192.0.2.5", xff, trusted) == "192.0.2.5"


@given(st.integers(min_value=0, max_value=2**64 - 1), st.integers(min_value=0, max_value=2**64 - 1))
def test_every_address_in_one_ipv6_slash_64_is_one_caller(prefix, suffix):
    a = ipaddress.IPv6Address((prefix << 64) | suffix)
    b = ipaddress.IPv6Address((prefix << 64) | ((suffix * 7919 + 13) % 2**64))
    assert limits.limiter_key(str(a)) == limits.limiter_key(str(b))


@given(st.integers(min_value=1, max_value=20), st.lists(st.floats(min_value=0, max_value=5, allow_nan=False), min_size=1, max_size=120))
def test_a_window_limiter_never_allows_more_than_its_limit_in_any_window(limit, gaps):
    now = [0.0]
    lim = limits.WindowLimiter(limit, 10.0, clock=lambda: now[0])
    allowed = []
    for g in gaps:
        now[0] += g
        if lim.allow("k"):
            allowed.append(now[0])
    for i, t in enumerate(allowed):
        assert sum(1 for u in allowed[i:] if u - t <= 10.0) <= limit


@given(st.from_regex(r"[a-z]{3,12}", fullmatch=True))
def test_an_answer_naming_a_site_the_evidence_never_mentioned_is_refused(name):
    evidence = "The venue is Heritage Hall in Pune. The fee is INR 65000."
    assert check_answer(f"Book it at https://{name}-tickets.example/pay now.", evidence) is not None
    assert check_answer(f"Write to {name}@{name}-mail.example to book.", evidence) is not None
