"""Found by sending SIGTERM to a real server with requests in flight: under waitress (the production server) the process died at once and cut them off. Only the built-in
server had an orderly stop. This runs the real waitress path in a child process."""

import http.client
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

pytest.importorskip("waitress")

CHILD = textwrap.dedent('''
    import sys, time
    from eventmate.web import serve

    def app(environ, start_response):
        if environ["PATH_INFO"] == "/slow":
            time.sleep(1.5)
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "4")])
        return [b"done"]

    serve.run(app, "127.0.0.1", int(sys.argv[1]), "waitress", remote=False, drain_s=8)
''')


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start(tmp_path):
    port = free_port()
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    proc = subprocess.Popen([sys.executable, str(script), str(port)], env={**os.environ, "PYTHONPATH": os.getcwd()}, stderr=subprocess.PIPE)
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return proc, port
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("child server did not start")


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path)
    r = c.getresponse()
    return r.status, r.read()


def test_sigterm_lets_a_request_in_flight_finish_then_exits_cleanly_and_refuses_new_connections(tmp_path):
    proc, port = start(tmp_path)
    result = []
    t = threading.Thread(target=lambda: result.append(get(port, "/slow")))
    t.start()
    time.sleep(0.5)  # the request is being handled
    proc.send_signal(signal.SIGTERM)
    t.join(20)
    code = proc.wait(20)
    err = proc.stderr.read().decode()
    assert result == [(200, b"done")], result  # answered, not cut off
    assert code == 0 and "stopped" in err and "abandoned" not in err, (code, err)
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), 1)


def test_sigterm_on_an_idle_server_exits_promptly(tmp_path):
    proc, port = start(tmp_path)
    assert get(port, "/fast") == (200, b"done")
    started = time.time()
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(15) == 0 and time.time() - started < 12


def test_forwarded_headers_reach_the_app_so_a_proxys_visitors_are_not_all_one_address(tmp_path):
    """Found by a load test: waitress silently dropped X-Forwarded-For and -Proto, so behind a proxy every visitor shared the proxy's address and the per-address limits (and the
    HTTPS check) saw one caller. The app decides whom to believe (web/limits.py); the server must pass the headers on."""
    from eventmate.web.serve import make_waitress

    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [repr((environ.get("HTTP_X_FORWARDED_FOR"), environ.get("HTTP_X_FORWARDED_PROTO"))).encode()]

    server = make_waitress(app, "127.0.0.1", 0)
    threading.Thread(target=server.run, daemon=True).start()
    c = http.client.HTTPConnection("127.0.0.1", server.effective_port, timeout=10)
    c.request("GET", "/", headers={"X-Forwarded-For": "198.51.100.7", "X-Forwarded-Proto": "https"})
    assert c.getresponse().read() == b"('198.51.100.7', 'https')"
    server.close()


def test_stopping_an_idle_server_many_times_never_ends_in_a_traceback(tmp_path):
    """The first version closed every connection from a helper thread while the loop was in select(): an idle stop usually worked and sometimes ended in EBADF (seen as exit 1 from a
    container). Ten stops, each with a connection just closed, must all be clean."""
    for _ in range(10):
        proc, port = start(tmp_path)
        assert get(port, "/fast") == (200, b"done")
        proc.send_signal(signal.SIGTERM)
        code = proc.wait(15)
        err = proc.stderr.read().decode()
        assert code == 0 and "Traceback" not in err, (code, err[-400:])
