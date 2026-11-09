"""Start, resume and read missions. State lives in one owner-only SQLite file, so a mission survives a restart and waits as long as the user does."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from eventmate.orchestration.graph import build_graph
from eventmate.sqlsafe import secure
from eventmate.orchestration.models import ApprovalReview, ContactReview, MissionView

MISSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
REVIEWS = {"contact_review": ContactReview, "draft_approval": ApprovalReview}


class MissionRunner:
    def __init__(self, em, db_path: Path):
        db_path = Path(db_path)
        if db_path.is_symlink():
            raise ValueError("the missions database path is a symlink")
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self._conn = secure(sqlite3.connect(db_path, check_same_thread=False))
        self.graph = build_graph(em, SqliteSaver(self._conn))

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _config(mission_id: str) -> dict[str, Any]:
        if not isinstance(mission_id, str) or not MISSION_ID.fullmatch(mission_id):
            raise ValueError("mission id must be 1-64 letters, digits, '_' or '-'")
        return {"configurable": {"thread_id": mission_id}}

    def start(self, brief: str, sender_name: str, categories: Sequence[str], today: date | None = None, source: str | None = None, limit: int = 10,
              mission_id: str | None = None) -> MissionView:
        mission_id = uuid.uuid4().hex[:16] if mission_id is None else mission_id
        cfg = self._config(mission_id)
        if not (brief or "").strip() or not (sender_name or "").strip() or not categories:
            raise ValueError("a mission needs a brief, a sender name and at least one category")
        if self.graph.get_state(cfg).created_at is not None:
            raise ValueError("a mission with that id already exists; resume or read it")
        state = {"brief": brief, "today": (today or date.today()).isoformat(), "sender_name": sender_name.strip(), "categories": list(dict.fromkeys(categories)),
                 "limit": int(limit), "trail": []}
        if source:
            state["source"] = source
        self.graph.invoke(state, cfg)
        return self.view(mission_id)

    def resume(self, mission_id: str, decision: dict[str, Any]) -> MissionView:
        """Answer the question the mission is waiting on. The answer is checked against that question's schema before the graph sees it."""
        cfg, cur = self._config(mission_id), self.view(mission_id)
        if cur.waiting_for is None:
            raise ValueError("this mission is not waiting for a decision")
        checked = REVIEWS[cur.waiting_for].model_validate(decision)
        self.graph.invoke(Command(resume=checked.model_dump()), cfg)
        return self.view(mission_id)

    def view(self, mission_id: str) -> MissionView:
        snap = self.graph.get_state(self._config(mission_id))
        if snap.created_at is None:
            raise ValueError("unknown mission")
        v, asked = snap.values, [i for t in snap.tasks for i in t.interrupts]
        common = {"mission_id": mission_id, "plan_id": v.get("plan_id"), "trail": tuple(v.get("trail", ()))}
        if asked:
            prompt = asked[0].value
            return MissionView(status="waiting", stage=v.get("stage", "started"), waiting_for=prompt["kind"], prompt=prompt, **common)
        failed = bool(v.get("failure"))
        return MissionView(status="failed" if failed else "done", stage=v.get("stage", "started"), failure=v.get("failure", ""), **common)
