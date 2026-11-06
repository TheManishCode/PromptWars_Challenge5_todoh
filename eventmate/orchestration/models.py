"""What crosses the graph boundary: the user's decisions (resume values) and the view handed back to callers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from eventmate.schemas import _Model

_ID = r"^[A-Za-z0-9_-]{1,64}$"


class ContactDecision(_Model):
    vendor_id: str = Field(pattern=_ID)
    candidate_id: str = Field(pattern=_ID)
    action: Literal["confirm", "reject"]
    by: str | None = Field(default=None, min_length=1, max_length=60)  # who confirmed (ADR-049): required to confirm
    edited_value: str | None = Field(default=None, max_length=254)
    business_name: str | None = Field(default=None, max_length=120)  # the name the user confirms for a Google-listed business: its listing name is not stored

    @model_validator(mode="after")
    def _confirm_needs_a_name(self) -> ContactDecision:
        if self.action == "confirm" and not (self.by or "").strip():
            raise ValueError("say who confirmed the address (a name)")
        if self.action == "reject" and (self.by or self.edited_value or self.business_name):
            raise ValueError("a rejection carries no name or edited address")
        return self


class ContactReview(_Model):
    decisions: tuple[ContactDecision, ...] = Field(max_length=200)


class DraftDecision(_Model):
    draft_id: str = Field(pattern=_ID)
    action: Literal["approve", "deny"]
    content_hash: str = Field(default="", max_length=64)  # the hash of the text the user was shown

    @model_validator(mode="after")
    def _approve_names_the_text(self) -> DraftDecision:
        if self.action == "approve" and not self.content_hash:
            raise ValueError("approving needs the content hash of the text that was shown")
        return self


class ApprovalReview(_Model):
    decisions: tuple[DraftDecision, ...] = Field(max_length=200)


Waiting = Literal["contact_review", "draft_approval"]


class MissionView(_Model):
    mission_id: str = Field(pattern=_ID)
    status: Literal["waiting", "done", "failed"]
    stage: str = Field(max_length=40)
    waiting_for: Waiting | None = None
    prompt: dict[str, Any] = {}  # what the user is asked to decide; plain JSON
    plan_id: str | None = None
    trail: tuple[str, ...] = ()
    failure: str = ""

    @model_validator(mode="after")
    def _shape(self) -> MissionView:
        if (self.status == "waiting") != (self.waiting_for is not None):
            raise ValueError("waiting_for is set iff the mission is waiting")
        return self
