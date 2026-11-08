"""Which plan a brief became, so what was filed under the brief (requests, replies, readings) follows it into the plan.

Requests and replies are filed under ``brief_<staged id>`` while an event is only a brief. When the brief is built into a plan nothing moves: the link is recorded here, and the stores
that file by plan id (``OutreachStore``, ``ShortlistStore``, ``QuoteConfirmationStore``) resolve the plan's id to the brief's home through ``home``. Reads and writes for either id then
land in the same place, so a reply that arrives after the build is still the plan's reply (audit A4). One event has one key (``event_key``): the plan's id.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from eventmate.jsonl import InterProcessLock, JsonlLog
from eventmate.planning.brief_scope import BRIEF_PREFIX, is_brief, staged_id_of
from eventmate.schemas import _Model


class BriefLink(_Model):
    staged_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class _File(JsonlLog[BriefLink]):
    channel = "brief_link"
    model = BriefLink


class BriefLinks:
    def __init__(self, root: Path):
        self.root = Path(root) / "brief_links"
        self._lock = InterProcessLock(self.root / ".lock")
        self._seen: tuple[int, int] | None = None
        self._by_plan: dict[str, str] = {}
        self._by_brief: dict[str, str] = {}

    def _path(self) -> Path:
        return self.root / "brief_link-all.jsonl"

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            self._by_plan, self._by_brief, self._seen = {}, {}, None
            return
        st = path.stat()
        if self._seen != (st.st_mtime_ns, st.st_size):
            rows = _File(self.root, run_id="all").read()
            self._by_plan = {r.plan_id: r.staged_id for r in rows}
            self._by_brief = {r.staged_id: r.plan_id for r in rows}
            self._seen = (st.st_mtime_ns, st.st_size)

    def link(self, staged_id: str, plan_id: str) -> None:
        with self._lock():
            self._load()
            if self._by_brief.get(staged_id) == plan_id:
                return
            _File(self.root, run_id="all")._append(BriefLink(staged_id=staged_id, plan_id=plan_id), f"{staged_id}:{plan_id}")
            self._seen = None

    def plan_of(self, brief_id: str) -> str | None:
        """The plan a ``brief_<id>`` became, or None."""
        if not is_brief(brief_id):
            return None
        self._load()
        return self._by_brief.get(staged_id_of(brief_id))

    def brief_of(self, plan_id: str) -> str | None:
        """The ``brief_<id>`` a plan was built from, or None."""
        self._load()
        sid = self._by_plan.get(plan_id)
        return BRIEF_PREFIX + sid if sid else None

    def home(self, plan_id: str) -> str:
        """Where this event's requests and replies are filed: the brief it came from, if it came from one; otherwise the id itself."""
        return self.brief_of(plan_id) or plan_id

    def event_key(self, plan_id: str) -> str:
        """One key for one event, whichever id it is asked by: a brief that became a plan is that plan."""
        return self.plan_of(plan_id) or plan_id


def identity(plan_id: str) -> str:
    return plan_id


__all__ = ["BriefLinks", "BriefLink", "identity"]
