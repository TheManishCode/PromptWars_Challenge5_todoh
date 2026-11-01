"""Run the to;doh-the event planner web UI:  python -m eventmate.web [--port 8765]

Loopback only by default. To listen elsewhere you must set EVENTMATE_WEB_TOKEN
(a bearer token required for the API) and pass --allow-remote plus --allowed-host
for every hostname you serve under; put it behind TLS (reverse proxy) and a
production WSGI server (waitress: pip install 'eventmate[serve]'). The built-in server is for local single-user use.
"""

import argparse
import os
import sys
from pathlib import Path

from eventmate.env import DEFAULT_PATH as ENV_FILE
from eventmate.web import hosting, serve
from eventmate.web.app import LOOPBACK, create_app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eventmate.web")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--allow-remote", action="store_true")
    ap.add_argument("--allowed-host", action="append", default=[])
    ap.add_argument("--server", choices=["auto", "dev", "waitress"], default="auto", help="auto: waitress if installed, else the built-in server (loopback only)")
    a = ap.parse_args(argv)
    try:
        run_mode = hosting.mode(os.environ)
    except ValueError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    token = os.environ.get("EVENTMATE_WEB_TOKEN") or None
    if run_mode == "shared":
        problems = hosting.shared_mode_problems(os.environ, a.allowed_host, ENV_FILE)
        if a.host in LOOPBACK or not a.allow_remote:
            problems.append("serve shared mode on the address the proxy reaches, with --allow-remote")
        if problems:
            print("refusing to start in shared mode:\n  - " + "\n  - ".join(problems), file=sys.stderr)
            return 2
        token = None  # visitors cannot present a shared bearer token; each holds a workspace credential instead
    elif a.host not in LOOPBACK and not (a.allow_remote and token):
        print("refusing to listen on a non-loopback address without --allow-remote and EVENTMATE_WEB_TOKEN", file=sys.stderr)
        return 2
    try:
        kind = serve.choose(a.server, remote=a.host not in LOOPBACK)
    except serve.ServerUnavailable as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    hosts = set(LOOPBACK) | set(a.allowed_host)
    if run_mode == "shared":
        from eventmate.workspaces import WorkspaceRegistry

        try:
            server_lock = hosting.acquire_server_lock(Path(os.environ["EVENTMATE_DATA_DIR"]))  # noqa: F841  (held until the process ends)
        except hosting.AlreadyRunning as exc:
            print(f"refusing to start: {exc}", file=sys.stderr)
            return 2
        registry = WorkspaceRegistry(Path(os.environ["EVENTMATE_DATA_DIR"]), os.environ["EVENTMATE_SECRET"],
                                     max_workspaces=int(os.environ.get("EVENTMATE_MAX_WORKSPACES", "1000")))
        app = create_app(registry=registry, allowed_hosts=hosts)
    else:
        app = create_app(api_token=token, allowed_hosts=hosts)
    print(f"to;doh-the event planner UI on http://{a.host}:{a.port}/  ({run_mode} mode; API token {'required' if token else 'not set' if run_mode == 'local' else 'not used: each visitor holds a workspace credential'}; server: {kind})")
    serve.run(app, a.host, a.port, a.server, remote=a.host not in LOOPBACK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
