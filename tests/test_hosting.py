"""Phase 5.2: shared mode refuses to start unless every condition holds, and HTTPS is believed only from a trusted proxy."""

import ipaddress

import pytest

from eventmate.service import EventMate
from eventmate.web import hosting
from eventmate.web.__main__ import main
from eventmate.web.app import create_app
from tests.conftest import TODAY

GOOD = {
    "EVENTMATE_MODE": "shared", "EVENTMATE_SECRET": "s" * 40, "EVENTMATE_TRUSTED_PROXY": "10.0.0.1", "EVENTMATE_PUBLIC_URL": "https://todoh.example",
    "EVENTMATE_OWNER_TOKEN": "owner-token-0123456789",
}


def problems(env=None, hosts=("todoh.example",), env_file=None, workspaces=True):
    return hosting.shared_mode_problems({**GOOD, **(env or {})}, hosts, env_file, workspaces_implemented=workspaces)


def test_the_mode_is_local_by_default_and_an_unknown_value_is_an_error():
    assert hosting.mode({}) == "local" and hosting.mode({"EVENTMODE": "x"}) == "local" and hosting.mode({"EVENTMATE_MODE": " Shared "}) == "shared"
    with pytest.raises(ValueError, match="EVENTMATE_MODE"):
        hosting.mode({"EVENTMATE_MODE": "prod"})


def test_a_complete_shared_setup_has_no_problems():
    assert problems() == []


@pytest.mark.parametrize(
    "change,needle",
    [
        ({"EVENTMATE_SECRET": "short"}, "EVENTMATE_SECRET"),
        ({"EVENTMATE_SECRET": ""}, "EVENTMATE_SECRET"),
        ({"EVENTMATE_TRUSTED_PROXY": ""}, "TRUSTED_PROXY"),
        ({"EVENTMATE_TRUSTED_PROXY": "not-an-address"}, "TRUSTED_PROXY"),
        ({"EVENTMATE_PUBLIC_URL": "http://todoh.example"}, "https://"),
        ({"EVENTMATE_PUBLIC_URL": ""}, "https://"),
        ({"EVENTMATE_OWNER_TOKEN": "short"}, "OWNER_TOKEN"),
    ],
)
def test_each_missing_setting_is_named(change, needle):
    found = problems(change)
    assert len(found) == 1 and needle in found[0]


def test_loopback_names_alone_are_not_a_hosted_setup():
    assert any("allowed-host" in p for p in problems(hosts=("localhost", "127.0.0.1")))
    assert any("allowed-host" in p for p in problems(hosts=()))


def test_a_dotenv_file_is_refused_in_shared_mode(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("X=1")
    assert any(".env" in p for p in problems(env_file=env_file)) and problems(env_file=tmp_path / "absent") == []


def test_shared_mode_refuses_until_workspaces_exist():
    assert hosting.WORKSPACES_IMPLEMENTED is False
    assert any("separate workspace" in p for p in problems(workspaces=False))


def test_the_server_command_refuses_to_start_in_shared_mode_with_the_reasons(monkeypatch, capsys):
    for k, v in {**GOOD, "EVENTMATE_SECRET": "short"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EVENTMATE_WEB_TOKEN", "t" * 24)
    assert main(["--host", "0.0.0.0", "--allow-remote", "--allowed-host", "todoh.example"]) == 2
    err = capsys.readouterr().err
    assert "refusing to start in shared mode" in err and "EVENTMATE_SECRET" in err and "separate workspace" in err


def test_an_unknown_mode_refuses_to_start(monkeypatch, capsys):
    monkeypatch.setenv("EVENTMATE_MODE", "prod")
    assert main(["--port", "0"]) == 2 and "EVENTMATE_MODE" in capsys.readouterr().err


PROXY = [ipaddress.ip_network("10.0.0.0/24")]


@pytest.mark.parametrize(
    "scheme,remote,proto,expected",
    [("https", "203.0.113.9", None, True), ("http", "10.0.0.2", "https", True), ("http", "203.0.113.9", "https", False), ("http", "10.0.0.2", "http", False), ("http", "10.0.0.2", None, False)],
)
def test_https_is_believed_only_from_a_trusted_proxy(scheme, remote, proto, expected):
    assert hosting.is_https(scheme, remote, proto, PROXY) is expected


def test_hsts_is_sent_only_over_https(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTMATE_TRUSTED_PROXY", "10.0.0.1")
    client = create_app(EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="h"), today_fn=lambda: TODAY).test_client()
    plain = client.get("/healthz", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    spoofed = client.get("/healthz", headers={"X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    via_proxy = client.get("/healthz", headers={"X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "10.0.0.1"})
    assert "Strict-Transport-Security" not in plain.headers and "Strict-Transport-Security" not in spoofed.headers
    assert via_proxy.headers["Strict-Transport-Security"].startswith("max-age=")
