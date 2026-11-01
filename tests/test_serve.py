"""F-002: the production server choice and what waitress adds over the built-in server (idle timeout, connection cap, body cap)."""

import json
import socket
import threading
import time

import pytest

from eventmate.service import EventMate
from eventmate.web import serve
from eventmate.web.app import create_app

waitress = pytest.importorskip("waitress")


def test_a_non_loopback_bind_never_gets_the_builtin_server(monkeypatch):
    monkeypatch.setattr(serve, "waitress_installed", lambda: False)
    with pytest.raises(serve.ServerUnavailable, match="waitress"):
        serve.choose("auto", remote=True)
    with pytest.raises(serve.ServerUnavailable):
        serve.choose("dev", remote=True)
    with pytest.raises(serve.ServerUnavailable, match="not installed"):
        serve.choose("waitress", remote=False)
    assert serve.choose("auto", remote=False) == "dev" and serve.choose("dev", remote=False) == "dev"
    with pytest.raises(serve.ServerUnavailable, match="unknown"):
        serve.choose("gunicorn", remote=False)
    monkeypatch.setattr(serve, "waitress_installed", lambda: True)
    assert serve.choose("auto", remote=True) == "waitress" and serve.choose("auto", remote=False) == "waitress" and serve.choose("dev", remote=False) == "dev"


def test_main_refuses_a_remote_bind_without_waitress(monkeypatch, capsys):
    from eventmate.web import __main__ as m
    monkeypatch.setattr(serve, "waitress_installed", lambda: False)
    monkeypatch.setenv("EVENTMATE_WEB_TOKEN", "t" * 20)
    assert m.main(["--host", "0.0.0.0", "--allow-remote", "--allowed-host", "x.example"]) == 2
    assert "needs waitress" in capsys.readouterr().err


@pytest.fixture
def running(tmp_path):
    em = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="srv")
    app = create_app(em)
    server = serve.make_waitress(app, "127.0.0.1", 0, channel_timeout=1, cleanup_interval=1, connection_limit=50, threads=4)
    port = server.effective_port
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    yield port
    server.close()


def raw(port, data=b"", timeout=5):
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    if data:
        s.sendall(data)
    return s


def get(port):
    s = raw(port, b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    out = b""
    while chunk := s.recv(65536):
        out += chunk
    s.close()
    return out


def test_half_open_connections_are_closed_by_the_idle_timeout_and_the_app_stays_up(running):
    held = [raw(running, b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nX-a: ") for _ in range(30)]
    assert b"200 OK" in get(running)  # the app still answers while 30 connections dribble
    time.sleep(3.5)  # channel_timeout is 1 s in this fixture (30 s by default)
    closed = 0
    for s in held:
        s.settimeout(1)
        try:
            if s.recv(10) == b"":
                closed += 1
        except (TimeoutError, ConnectionResetError, OSError):
            closed += 1
    for s in held:
        s.close()
    assert closed == 30, f"only {closed} of 30 idle connections were closed"
    assert b"200 OK" in get(running)


def test_an_oversize_body_is_refused_before_it_is_buffered_by_the_app(running):
    tok = json.loads(get(running).split(b"\r\n\r\n", 1)[1])["csrf"]
    body = b"{" + b'"x":"' + b"A" * 70_000 + b'"}'
    s = raw(running, f"POST /api/assistant/ask HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nX-EventMate-CSRF: {tok}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    head = s.recv(4096)
    s.close()
    assert head.split(b"\r\n", 1)[0].split()[1] in (b"413", b"400")


def test_the_server_identifies_itself_with_no_version(running):
    assert b"Server: to;doh" in get(running) and b"waitress" not in get(running).split(b"\r\n\r\n", 1)[0].lower()
