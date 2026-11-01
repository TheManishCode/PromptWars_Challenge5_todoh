# Driving the room on a real phone over USB (Android, Chrome)

Needs `adb`, USB debugging authorised, Chrome open on the phone. Nothing here sends anything anywhere: the phone reaches this computer through `adb reverse`.

    export EVENTMATE_WALL_DB=/tmp/phone-wall.sqlite3 EVENTMATE_ENABLE_LLM=0
    python - <<'PY' &            # a local server on 127.0.0.1:8791 with temporary data
    from pathlib import Path; import tempfile
    from eventmate.service import EventMate
    from eventmate.web.app import create_app
    from werkzeug.serving import make_server
    d = Path(tempfile.mkdtemp()); make_server("127.0.0.1", 8791, create_app(EventMate(log_dir=d/"l", plan_dir=d/"p", run_id="phone")), threaded=True).serve_forever()
    PY
    adb reverse tcp:8791 tcp:8791
    adb shell am start -a android.intent.action.VIEW -d http://localhost:8791/ com.android.chrome
    adb forward tcp:9333 localabstract:chrome_devtools_remote
    python tools/device/phone_wall_flow.py     # touch the wall, solve the puzzle by finger, sign, approve through the CLI, play a timed round

The script attaches only to the tab whose address starts with `http://localhost:8791`; it never reads or touches any other tab. Remove the forwards afterwards
(`adb reverse --remove-all; adb forward --remove-all`).
