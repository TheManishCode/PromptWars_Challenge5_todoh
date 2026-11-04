"""Loads secrets from a local ``.env`` into the process environment (keys come from the environment only).

Only allow-listed names are read, values already set in the real environment win, and nothing here ever returns, logs or
prints a value: callers get names and warnings. ``.env`` is git-ignored; keep it out of version control and shared drives.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from collections.abc import MutableMapping

ALLOWED = frozenset({
    "OPENWEATHER_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "EVENTMATE_ENABLE_LLM", "EVENTMATE_BROWSER", "EVENTMATE_BROWSER_NO_SANDBOX", "EVENTMATE_REQUIRE_NETLESS", "EVENTMATE_OVERPASS_URL",
    "EVENTMATE_NVIDIA_MODEL", "EVENTMATE_NVIDIA_MODEL_LIGHT", "EVENTMATE_OPENROUTER_MODEL_HEAVY", "EVENTMATE_OPENROUTER_MODEL_LIGHT", "EVENTMATE_LLM_MAX_TOKENS", "EVENTMATE_LLM_MAX_CALLS",
    "LANGFUSE_HOST", "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "FOURSQUARE_API_KEY", "BRAVE_SEARCH_API_KEY", "GOOGLE_PLACES_API_KEY",
    "EVENTMATE_SMTP_HOST", "EVENTMATE_SMTP_PORT", "EVENTMATE_SMTP_USER", "EVENTMATE_SMTP_PASSWORD", "EVENTMATE_MAIL_FROM", "EVENTMATE_IMAP_HOST", "EVENTMATE_IMAP_PORT",
    "EVENTMATE_COMMUNITY_PROFILES", "EVENTMATE_WAHA_URL", "EVENTMATE_WAHA_API_KEY", "EVENTMATE_WAHA_SESSION", "EVENTMATE_POLL_SECONDS",
    "EVENTMATE_WS_LLM_CALLS_PER_HOUR", "EVENTMATE_WS_LLM_TOKENS_PER_HOUR", "EVENTMATE_WS_LLM_CONCURRENCY",
    "EVENTMATE_MAX_CONNECTIONS", "EVENTMATE_IDLE_TIMEOUT_S", "EVENTMATE_TRUSTED_PROXY", "EVENTMATE_OWNER_TOKEN", "EVENTMATE_MODE", "EVENTMATE_PUBLIC_URL", "EVENTMATE_DATA_DIR", "EVENTMATE_MAX_WORKSPACES", "EVENTMATE_WS_MAX_BYTES", "EVENTMATE_WS_MAX_PLANS_PER_DAY", "EVENTMATE_WS_LOOKUPS_PER_HOUR", "EVENTMATE_WS_EMPTY_IDLE_HOURS", "EVENTMATE_WS_SWEEP_SECONDS",
})
MAX_BYTES = 64_000
_SECRET_NAME = re.compile(r"(?:KEY|PASSWORD|SECRET|TOKEN)$")
_SECRET_SHAPE = re.compile(r"(?i)(?:bearer\s+\S+|nvapi-[\w-]{8,}|sk-[\w-]{16,}|AIza[\w-]{20,}|(?<=[?&])(?:key|token|secret|password|api_key|apikey)=[^&\s]+)")
DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".env"
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


@dataclass(frozen=True)
class EnvLoad:
    loaded: tuple[str, ...] = ()  # names only
    warnings: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return f"EnvLoad(loaded={self.loaded!r}, warnings={len(self.warnings)})"


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v.split(" #", 1)[0].strip()


def load_env(path: Path | None = None, environ: MutableMapping[str, str] | None = None) -> EnvLoad:
    environ = os.environ if environ is None else environ
    if path is None:
        path = Path(environ.get("EVENTMATE_ENV_FILE") or DEFAULT_PATH)
    path = Path(path)
    try:
        st = os.lstat(path)
    except OSError:
        return EnvLoad()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return EnvLoad(warnings=("the env file is a symlink or not a regular file and was ignored",))
    if st.st_size > MAX_BYTES:
        return EnvLoad(warnings=(f"the env file is larger than {MAX_BYTES} bytes and was ignored",))
    warnings = []
    if st.st_mode & 0o077:
        warnings.append("the env file's permissions let other users read it; run chmod 600 on it")
    try:
        text = path.read_text(encoding="utf-8-sig")  # tolerate the BOM some Windows editors add
    except (OSError, UnicodeDecodeError):
        return EnvLoad(warnings=tuple(warnings) + ("the env file could not be read",))
    loaded = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _LINE.match(line)
        if not m or m.group(1) not in ALLOWED or m.group(1) in environ:
            continue
        value = _unquote(m.group(2))
        if value:
            environ[m.group(1)] = value
            loaded.append(m.group(1))
    return EnvLoad(loaded=tuple(loaded), warnings=tuple(warnings))


def redact_secrets(text: str, environ: MutableMapping[str, str] | None = None) -> str:
    """Remove secrets from text that is about to be logged or shown: the value of every secret-named environment variable and common key shapes.
    Error text from a provider or a library can echo the key it was sent; this is the last line of defence, not a licence to log raw errors."""
    env = os.environ if environ is None else environ
    for name, value in env.items():
        if _SECRET_NAME.search(name) and len(value) >= 8 and value in text:
            text = text.replace(value, "[secret]")
    return _SECRET_SHAPE.sub("[secret]", text)
