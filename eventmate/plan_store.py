"""Immutable, hash-checked plan version storage.

Each version is one JSON file ``plan-<id>-v<NNNN>.json`` holding
``{"schema_version", "sha256", "plan"}``. Guarantees:

- create-only: written to a temp file then hard-linked into place, so an
  existing version is never overwritten (``FileExistsError``), even by racing
  writers; files are then made read-only (0444);
- versions are consecutive and each links to its parent: a new version records the hash of the version before it (``parent_sha256``), and every save
  appends the plan's newest version and hash to ``plan-heads.jsonl``, so ``python -m eventmate verify`` can find a version that was altered, removed from
  the middle, or removed from the end (a rolled-back plan). Versions written before this have no parent hash and are checked for consecutive numbers only;
- ``load`` verifies the SHA-256 of the canonical JSON *and* re-validates the
  model, so a forged "failed validation" plan cannot be constructed even if the
  hash is recomputed;
- ``plan_id`` is allow-listed (never path-joined raw); symlinks are refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from eventmate import migrations
from eventmate.schemas import PlanDraft, PlanVersion, ValidationReport

DEFAULT_PLAN_DIR = Path(__file__).resolve().parent / "plans"
PLAN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
FILE_NAME = re.compile(r"plan-([A-Za-z0-9_-]{1,64})-v(\d{4})\.json")
MAX_FILE_BYTES = 5_000_000


class PlanIntegrityError(Exception):
    """A stored plan failed its hash check or model validation."""


def new_plan_id() -> str:
    return secrets.token_hex(6)


def _check_id(plan_id: str) -> str:
    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise ValueError("plan_id must match [A-Za-z0-9_-]{1,64}")
    return plan_id


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def assemble_plan(
    draft: PlanDraft,
    report: ValidationReport,
    version: int,
    parent_version: int | None,
    reason: str,
    decision_entry_ids: Sequence[str],
    newly_confirmed_vendor_ids: Sequence[str] = (),
    released_vendor_ids: Sequence[str] = (),
) -> PlanVersion:
    """The only way a draft becomes a plan; refuses anything that did not pass validation."""
    if not report.passed:
        raise ValueError("cannot assemble a plan that failed validation")
    return PlanVersion(
        plan_id=draft.plan_id,
        version=version,
        parent_version=parent_version,
        created_at=datetime.now(UTC),
        origin=draft.origin,
        change_reason=reason,
        request=draft.request,
        guests=draft.guests,
        allocation=draft.allocation,
        selections=draft.selections,
        spec=draft.spec,
        solution=draft.solution,
        items=draft.items,
        total_cost=sum(s.cost for s in draft.selections),
        validation=report,
        symbolic=draft.symbolic,
        decision_entry_ids=tuple(decision_entry_ids),
        warnings=report.warnings,
        excluded_vendor_ids=draft.excluded_vendor_ids,
        newly_confirmed_vendor_ids=tuple(newly_confirmed_vendor_ids),
        released_vendor_ids=tuple(sorted(set(released_vendor_ids))),
    )


def draft_from_plan(plan: PlanVersion, **changes) -> PlanDraft:
    """Rebuild the solver-level draft of a stored plan (for re-validation, confirmation, replanning)."""
    fields = dict(
        plan_id=plan.plan_id,
        origin=plan.origin,
        request=plan.request,
        guests=plan.guests,
        allocation=plan.allocation,
        selections=plan.selections,
        spec=plan.spec,
        solution=plan.solution,
        symbolic=plan.symbolic,
        items=plan.items,
        already_done=tuple(sorted(t for t in ("book_venue", "book_caterer", "book_photographer") if _confirmed_task(plan, t))),
        warnings=plan.warnings,
        excluded_vendor_ids=plan.excluded_vendor_ids,
    )
    fields.update(changes)
    return PlanDraft(**fields)


def _confirmed_task(plan: PlanVersion, task_id: str) -> bool:
    from eventmate.schemas import BookingStatus, VendorCategory

    cat = {"book_venue": VendorCategory.VENUE, "book_caterer": VendorCategory.CATERER, "book_photographer": VendorCategory.PHOTOGRAPHER}[task_id]
    sel = plan.selection_for(cat)
    return sel is not None and sel.booking_status == BookingStatus.CONFIRMED


class PlanStore:
    """``signing_key`` (or env ``EVENTMATE_SIGNING_KEY``) adds an HMAC-SHA256 over each plan.

    A bare SHA-256 detects accidents; anyone who can write the file can recompute it. With a
    key, forging or editing a plan also requires the key, which never touches the disk.
    """

    def __init__(self, root: Path | None = None, signing_key: bytes | None = None):
        self.root = Path(root) if root is not None else DEFAULT_PLAN_DIR
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.Lock()
        if signing_key is None and os.environ.get("EVENTMATE_SIGNING_KEY"):
            signing_key = os.environ["EVENTMATE_SIGNING_KEY"].encode("utf-8")
        if signing_key is not None and len(signing_key) < 16:
            raise ValueError("signing key must be at least 16 bytes")
        self._key = signing_key

    def _sign(self, body) -> str:
        return hmac.new(self._key, _canonical(body).encode(), hashlib.sha256).hexdigest()

    def _path(self, plan_id: str, version: int) -> Path:
        return self.root / f"plan-{_check_id(plan_id)}-v{version:04d}.json"

    def _version_numbers(self, plan_id: str) -> list[int]:
        _check_id(plan_id)
        out = []
        for p in self.root.iterdir():
            m = FILE_NAME.fullmatch(p.name)
            if m and m.group(1) == plan_id:
                out.append(int(m.group(2)))
        return sorted(out)

    def delete(self, plan_id: str) -> int:
        """Remove every version of a plan (the user asked for it to be forgotten). Returns how many files were removed."""
        with self._lock:
            numbers = self._version_numbers(plan_id)
            for n in reversed(numbers):  # newest first: a crash midway leaves a plan that still loads
                os.unlink(self._path(plan_id, n))
            self._forget_head(plan_id)
            return len(numbers)

    # ---- the head record (rollback detection)

    @property
    def heads_path(self) -> Path:
        return self.root / "plan-heads.jsonl"

    def _record_head(self, plan: PlanVersion, sha: str) -> None:
        line = json.dumps({"plan_id": plan.plan_id, "version": plan.version, "sha256": sha}, sort_keys=True) + "\n"
        fd = os.open(self.heads_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def _forget_head(self, plan_id: str) -> None:
        """Remove a deleted plan's lines from the head record, so nothing but the tombstone still names it."""
        try:
            data = self.heads_path.read_bytes()
        except FileNotFoundError:
            return
        needle = plan_id.encode()
        if needle not in data:
            return
        kept = b"".join(line for line in data.splitlines(keepends=True) if needle not in line)
        if not kept.strip():
            os.unlink(self.heads_path)  # nothing left to remember: no empty file behind (found by thread C's failed-import test)
            return
        tmp = self.root / f".heads-{secrets.token_hex(6)}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, kept)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.heads_path)

    def heads(self) -> dict:
        """plan id -> (newest version, its hash), from the head record. A line that cannot be read is ignored here; ``verify`` reports it."""
        out: dict = {}
        try:
            lines = self.heads_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return out
        for line in lines:
            try:
                d = json.loads(line)
                if isinstance(d.get("plan_id"), str) and isinstance(d.get("version"), int) and isinstance(d.get("sha256"), str):
                    out[d["plan_id"]] = (d["version"], d["sha256"])
            except (ValueError, AttributeError):
                continue
        return out

    def _raw(self, plan_id: str, version: int) -> dict | None:
        try:
            return json.loads(self._path(plan_id, version).read_bytes())
        except (OSError, ValueError):
            return None

    def check_chain(self, plan_id: str) -> list[str]:
        """What is wrong with this plan's history, in words; an empty list means every version is present, hashes to what it says, links to its parent and
        ends where the head record says it should. Each version is also loaded (hash, schema and model) by ``load``; this adds the links between them."""
        _check_id(plan_id)
        problems: list[str] = []
        numbers = self._version_numbers(plan_id)
        if not numbers:
            return [f"plan {plan_id}: no versions on disk"]
        if numbers != list(range(1, numbers[-1] + 1)):
            missing = sorted(set(range(1, numbers[-1] + 1)) - set(numbers))
            problems.append(f"plan {plan_id}: version(s) {missing} are missing from the middle of its history")
        previous: dict | None = None
        for n in numbers:
            doc = self._raw(plan_id, n)
            if doc is None:
                problems.append(f"plan {plan_id} v{n}: unreadable")
                previous = None
                continue
            parent = doc.get("parent_sha256")
            if parent is not None and previous is not None and parent != previous.get("sha256"):
                problems.append(f"plan {plan_id} v{n}: does not link to the version before it (that version changed, or this one was replaced)")
            previous = doc
        head = self.heads().get(plan_id)
        if head is not None:
            if numbers[-1] < head[0]:
                problems.append(f"plan {plan_id} was rolled back: version {head[0]} was saved but the newest file on disk is version {numbers[-1]}")
            elif numbers[-1] == head[0]:
                last = self._raw(plan_id, numbers[-1])
                if last is not None and last.get("sha256") != head[1]:
                    problems.append(f"plan {plan_id} v{head[0]}: its hash is not the one recorded when it was saved")
        return problems

    def list_plans(self) -> list[str]:
        return sorted({m.group(1) for p in self.root.iterdir() if (m := FILE_NAME.fullmatch(p.name))})

    def save(self, plan: PlanVersion) -> Path:
        with self._lock:
            existing = self._version_numbers(plan.plan_id)
            latest = existing[-1] if existing else 0
            if plan.version <= latest:
                raise FileExistsError(f"{plan.plan_id} v{plan.version} already exists; plans are immutable")
            if plan.version != latest + 1:
                raise ValueError(f"next version must be {latest + 1}, got {plan.version}")
            if (plan.parent_version or None) != (latest or None):
                raise ValueError(f"parent_version must be {latest or None}, got {plan.parent_version}")
            body = json.loads(plan.model_dump_json())
            doc = {"schema_version": migrations.current("plan_version"), "sha256": hashlib.sha256(_canonical(body).encode()).hexdigest(), "plan": body}
            previous = self._raw(plan.plan_id, latest) if latest else None
            if previous is not None and isinstance(previous.get("sha256"), str):
                doc["parent_sha256"] = previous["sha256"]  # this version vouches for the one before it
            if self._key is not None:
                doc["hmac"] = self._sign(body)
            final = self._path(plan.plan_id, plan.version)
            tmp = self.root / f".tmp-{secrets.token_hex(8)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(doc, indent=2, sort_keys=True))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.link(tmp, final)  # atomic create-only: raises FileExistsError if it exists
                os.chmod(final, 0o444)
            finally:
                os.unlink(tmp)
            self._record_head(plan, doc["sha256"])
            return final

    def load(self, plan_id: str, version: int | None = None) -> PlanVersion:
        numbers = self._version_numbers(plan_id)
        if not numbers:
            raise FileNotFoundError(f"no plan {plan_id!r}")
        version = numbers[-1] if version is None else version
        if version not in numbers:
            raise FileNotFoundError(f"plan {plan_id!r} has no version {version}")
        path = self._path(plan_id, version)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise PlanIntegrityError(f"cannot open {path.name} safely: {exc}") from exc
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise PlanIntegrityError(f"{path.name} is too large")
        try:
            doc = json.loads(raw)
            stored_version = doc["schema_version"]
            if isinstance(stored_version, int) and not isinstance(stored_version, bool) and stored_version > migrations.current("plan_version"):
                raise PlanIntegrityError(f"{path.name} was written by a newer version of the application (plan schema {stored_version}; this one reads up to {migrations.current('plan_version')}); upgrade the application")
            if hashlib.sha256(_canonical(doc["plan"]).encode()).hexdigest() != doc["sha256"]:  # the stored hash covers the stored schema, so it is checked before any upgrade
                raise PlanIntegrityError(f"{path.name} failed its integrity hash")
            if self._key is not None and not hmac.compare_digest(str(doc.get("hmac", "")), self._sign(doc["plan"])):
                raise PlanIntegrityError(f"{path.name} has a missing or invalid signature")
            plan = PlanVersion.model_validate(migrations.upgrade("plan_version", dict(doc["plan"]), version=stored_version, source=path.name))
        except PlanIntegrityError:
            raise
        except migrations.StoreCorrupt as exc:
            raise PlanIntegrityError(str(exc)) from exc
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise PlanIntegrityError(f"{path.name} is not a valid plan: {exc}") from exc
        if plan.plan_id != plan_id or plan.version != version:
            raise PlanIntegrityError(f"{path.name} content does not match its file name")
        return plan

    def versions(self, plan_id: str) -> list[PlanVersion]:
        return [self.load(plan_id, n) for n in self._version_numbers(plan_id)]

    def latest(self, plan_id: str) -> PlanVersion:
        return self.load(plan_id)
