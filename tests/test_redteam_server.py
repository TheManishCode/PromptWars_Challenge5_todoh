"""Red-team finding: the server never closed an idle or half-open connection and accepted any number of them."""

import socket
import threading
import time

import pytest

from eventmate.service import EventMate
from eventmate.web.app import create_app
from eventmate.web.server import make_bounded_server
from tests.conftest import TODAY


@pytest.fixture
def server(tmp_path):
    em = EventMate(log_dir=tmp_path / "logs", plan_dir=tmp_path / "plans", run_id="srv")
    srv = make_bounded_server(create_app(em, today_fn=lambda: TODAY), "127.0.0.1", 0, max_connections=4, idle_timeout_s=1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def connect(srv):
    return socket.create_connection(("127.0.0.1", srv.server_port), timeout=3)


def closed_by_server(sock, within_s):
    sock.settimeout(within_s)
    try:
        return sock.recv(10) == b""
    except TimeoutError:
        return False
    except OSError:
        return True


def test_a_half_open_connection_is_closed_after_the_idle_timeout(server):
    s = connect(server)
    s.sendall(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nX-a: ")  # never finishes its headers
    assert closed_by_server(s, 5)


def test_connections_over_the_cap_are_closed_at_once_and_the_server_recovers(server):
    held = [connect(server) for _ in range(4)]
    for s in held:
        s.sendall(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nX-a: ")
    time.sleep(0.2)
    extra = connect(server)
    assert closed_by_server(extra, 1.5)
    assert server.refused >= 1
    time.sleep(1.5)  # the idle ones time out and free their slots
    ok = connect(server)
    ok.sendall(b"GET /api/session HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
    assert b"200" in ok.recv(200)
