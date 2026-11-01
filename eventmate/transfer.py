"""Taking a workspace's data out and putting it back (Phase 3.2).

Export is one zip with a manifest: every plan version exactly as stored (so its hash still checks), the ledger, saved leads, guest lists, and the outreach
history and jobs as history only. It holds no credential, key or recovery material (none is kept in a workspace), and it is plaintext: the manifest says
it contains personal data (vendor contact details, guest names). Logs, traces, caches and assistant memory are not exported; they can be rebuilt or are
operational.

Import treats the archive as hostile. Member names must match a fixed grammar and are never used as paths (every destination is built from validated ids);
sizes, counts, ratios and nesting are limited while reading, not trusted from the headers; the manifest must list exactly the members present with their hashes;
every record is validated by the same models the application uses; and nothing is written until all of it has passed. Imported outreach and jobs are kept as
history files that no part of the sending machinery reads, so an archive can never cause a message to be sent: the person drafts and approves afresh.
A plan id that already exists in the workspace is replaced by a new one everywhere it appears and the new plan versions are hashed again.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import Field, ValidationError

from eventmate import migrations
from eventmate.plan_store import PlanStore, _canonical, new_plan_id
from eventmate.schemas import DealLead, Guest, LedgerEntry, PlanVersion, _Model

FORMAT = "todoh-export"
SCHEMA = 1
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_MEMBER = 5_000_000
MAX_TOTAL = 60 * 1024 * 1024
MAX_RATIO = 100
MAX_PLANS = 200
MAX_VERSIONS = 200
MAX_GUESTS = 500
MAX_LINES = 5000
PID = r"[A-Za-z0-9_-]{1,64}"
MEMBER = re.compile(
    rf"manifest\.json|plans/{PID}/v\d{{4}}\.json|ledger/{PID}\.jsonl|guests/{PID}\.json|leads/{PID}\.jsonl|history/(?:outreach|inbound)/{PID}\.jsonl|history/jobs\.jsonl"
)
PERSONAL = ("guests/", "history/")


class TransferError(ValueError):
    """The archive cannot be used. ``code`` is machine readable; the message is safe to show and names no path of ours."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


class _FileEntry(_Model):
    path: str = Field(max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0, le=MAX_MEMBER)
    kind: str = Field(max_length=30)
    personal_data: bool


class _PlanEntry(_Model):
    plan_id: str = Field(pattern=rf"^{PID}$")
    versions: Tuple[Tuple[int, str], ...] = Field(max_length=MAX_VERSIONS)  # (version, the hash stored inside the version file)


class Manifest(_Model):
    format: str
    schema_version: int
    exported_at: str = Field(max_length=40)
    contains_personal_data: bool
    not_included: Tuple[str, ...] = Field(max_length=20)
    files: Tuple[_FileEntry, ...] = Field(max_length=MAX_MEMBERS)
    plans: Tuple[_PlanEntry, ...] = Field(max_length=MAX_PLANS)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zipinfo(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


# --------------------------------------------------------------------------- export


def export_workspace(em, now: Optional[datetime] = None) -> bytes:
    """The archive for the workspace ``em`` belongs to. Plan versions are re-verified as they are read: a damaged one stops the export rather than being passed on."""
    root, store = Path(em.store.root), em.store
    members: Dict[str, Tuple[bytes, str]] = {}
    plans: List[_PlanEntry] = []
    for plan_id in store.list_plans():
        entry = []
        for plan in store.versions(plan_id):  # verifies the hash and the model of every version
            raw = (root / f"plan-{plan_id}-v{plan.version:04d}.json").read_bytes()
            members[f"plans/{plan_id}/v{plan.version:04d}.json"] = (raw, "plan_version")
            entry.append((plan.version, json.loads(raw)["sha256"]))
        plans.append(_PlanEntry(plan_id=plan_id, versions=tuple(entry)))
        for member, rel, kind in (
            (f"ledger/{plan_id}.jsonl", f"ledger/ledger-{plan_id}.jsonl", "ledger"), (f"guests/{plan_id}.json", f"guests/guests-{plan_id}.json", "guest_list"),
            (f"leads/{plan_id}.jsonl", f"leads/lead-{plan_id}.jsonl", "leads"), (f"history/outreach/{plan_id}.jsonl", f"outreach/outreach-{plan_id}.jsonl", "outreach_history"),
            (f"history/inbound/{plan_id}.jsonl", f"inbound/inbound-{plan_id}.jsonl", "inbound_history"),
        ):
            path = root / rel
            if path.is_file() and not path.is_symlink():
                members[member] = (path.read_bytes(), kind)
    jobs = em.jobs.store.list() if (root / "jobs.db").exists() else []
    if jobs:
        members["history/jobs.jsonl"] = ("".join(j.model_dump_json() + "\n" for j in jobs).encode("utf-8"), "jobs_history")
    manifest = Manifest(
        format=FORMAT, schema_version=SCHEMA, exported_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), contains_personal_data=True,
        not_included=("logs, traces and the assistant's memory", "answer caches", "any credential, key or recovery code", "send caps and the do-not-contact lists (they are not per plan)"),
        files=tuple(_FileEntry(path=n, sha256=_sha(d), bytes=len(d), kind=k, personal_data=n.startswith(PERSONAL)) for n, (d, k) in sorted(members.items())), plans=tuple(plans),
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_zipinfo("manifest.json"), manifest.model_dump_json(indent=2))
        for name, (data, _) in sorted(members.items()):
            zf.writestr(_zipinfo(name), data)
    return out.getvalue()


# --------------------------------------------------------------------------- import: reading


def _read_members(raw: bytes) -> Dict[str, bytes]:
    if len(raw) > MAX_ARCHIVE:
        raise TransferError("archive_too_large", f"the archive is larger than {MAX_ARCHIVE // (1024 * 1024)} MB", 413)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError):
        raise TransferError("not_an_archive", "that is not a valid export archive") from None
    if len(infos) > MAX_MEMBERS:
        raise TransferError("too_many_members", "the archive has too many files")
    seen, total, out = set(), 0, {}
    for info in infos:
        name = info.filename
        if name in seen:
            raise TransferError("duplicate_member", "the archive lists the same file twice")
        seen.add(name)
        mode = (info.external_attr >> 16) & 0o170000
        if not MEMBER.fullmatch(name) or info.is_dir() or (mode and mode != stat.S_IFREG) or info.flag_bits & 0x1:
            raise TransferError("bad_member", "the archive contains a file this application does not write")  # absolute paths, .., symlinks, devices and encrypted members all end here
        if info.file_size > MAX_MEMBER or (info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 64 * 1024):
            raise TransferError("member_too_large", "a file in the archive is too large or compresses suspiciously well")
        total += info.file_size
        if total > MAX_TOTAL:
            raise TransferError("archive_too_large", "the archive expands to more than it is allowed to", 413)
        try:
            with zf.open(info) as fh:
                data = fh.read(MAX_MEMBER + 1)  # read what is really there, not what the header says
        except (zipfile.BadZipFile, ValueError, OSError, NotImplementedError, RuntimeError):
            raise TransferError("damaged_member", "a file in the archive is damaged") from None
        if len(data) > MAX_MEMBER or len(data) != info.file_size:
            raise TransferError("member_too_large", "a file in the archive is larger than it says")
        out[name] = data
    if "manifest.json" not in out:
        raise TransferError("no_manifest", "the archive has no manifest")
    return out


def _json(data: bytes):
    try:
        return json.loads(data)
    except (ValueError, RecursionError):  # RecursionError: nesting deep enough to exhaust the parser
        raise TransferError("bad_json", "a file in the archive is not valid JSON") from None


def _lines(data: bytes, limit: int = MAX_LINES) -> List[bytes]:
    lines = [x for x in data.split(b"\n") if x.strip()]
    if len(lines) > limit:
        raise TransferError("too_many_records", "a file in the archive has too many records")
    return lines


def _validated(members: Dict[str, bytes]) -> Tuple[Manifest, Dict[str, List[PlanVersion]]]:
    try:
        manifest = Manifest.model_validate(_json(members["manifest.json"]))
    except ValidationError:
        raise TransferError("bad_manifest", "the archive's manifest is not valid") from None
    if manifest.format != FORMAT or manifest.schema_version != SCHEMA:
        raise TransferError("unsupported_version", f"this archive is format {manifest.format!r} version {manifest.schema_version}; this application reads {FORMAT} version {SCHEMA}")
    listed = {f.path: f for f in manifest.files}
    if len(listed) != len(manifest.files) or set(listed) != set(members) - {"manifest.json"}:
        raise TransferError("manifest_mismatch", "the manifest and the files in the archive do not agree")
    for path, entry in listed.items():
        if _sha(members[path]) != entry.sha256 or len(members[path]) != entry.bytes:
            raise TransferError("tampered", "a file in the archive does not match its recorded hash")
    plans: Dict[str, List[PlanVersion]] = {}
    for entry in manifest.plans:
        versions = []
        for number, recorded in entry.versions:
            path = f"plans/{entry.plan_id}/v{number:04d}.json"
            if path not in members:
                raise TransferError("manifest_mismatch", "the manifest names a plan version that is not in the archive")
            doc = _json(members[path])
            try:
                if doc["schema_version"] != 1 or _sha(_canonical(doc["plan"]).encode()) != doc["sha256"] or doc["sha256"] != recorded:
                    raise TransferError("tampered", "a plan version fails its own hash")
                plan = PlanVersion.model_validate(doc["plan"])
            except (KeyError, TypeError, ValidationError):
                raise TransferError("bad_plan", "a plan version in the archive is not a valid plan") from None
            if plan.plan_id != entry.plan_id or plan.version != number:
                raise TransferError("bad_plan", "a plan version does not match its place in the archive")
            versions.append(plan)
        if [p.version for p in versions] != list(range(1, len(versions) + 1)) or not versions:
            raise TransferError("bad_plan", "a plan's versions are not consecutive from 1")
        plans[entry.plan_id] = versions
    if {m.split("/")[1] for m in members if m.startswith("plans/")} != set(plans):
        raise TransferError("manifest_mismatch", "the archive holds plan files the manifest does not list")
    return manifest, plans


def _parse_records(members: Dict[str, bytes], plans: Dict[str, List[PlanVersion]]):
    ledger: Dict[str, List[LedgerEntry]] = {}
    leads: Dict[str, List[DealLead]] = {}
    guests: Dict[str, Tuple[Guest, ...]] = {}
    history: Dict[str, List[dict]] = {}
    try:
        for name, data in members.items():
            kind, _, rest = name.partition("/")
            plan_id = Path(rest).stem
            if kind in ("ledger", "leads", "guests") and plan_id not in plans:
                raise TransferError("bad_member", "a record file belongs to a plan that is not in the archive")
            if kind == "ledger":
                ledger[plan_id] = [LedgerEntry.model_validate(migrations.parse_line("ledger", x, name, i)) for i, x in enumerate(_lines(data), 1)]
                if any(e.plan_id != plan_id for e in ledger[plan_id]):
                    raise TransferError("bad_record", "a ledger entry names a different plan")
            elif kind == "leads":
                leads[plan_id] = [DealLead.model_validate(migrations.parse_line("lead", x, name, i)) for i, x in enumerate(_lines(data), 1)]
                if any(e.plan_id != plan_id for e in leads[plan_id]):
                    raise TransferError("bad_record", "a saved lead names a different plan")
            elif kind == "guests":
                doc = _json(data)
                rows = doc["guests"]
                if not isinstance(rows, list) or len(rows) > MAX_GUESTS or doc.get("plan_id") != plan_id:
                    raise TransferError("bad_record", "a guest list is not valid")
                guests[plan_id] = tuple(Guest.model_validate(g) for g in rows)
            elif kind == "history":
                history[name] = [x if isinstance(x, dict) else _bad() for x in (_json(line) for line in _lines(data))]
    except (ValidationError, KeyError, TypeError, migrations.StoreCorrupt):
        raise TransferError("bad_record", "a record in the archive is not valid") from None
    return ledger, leads, guests, history


def _bad():
    raise TransferError("bad_record", "a history record is not an object")


# --------------------------------------------------------------------------- import: applying


def _reverify(em, plans: Dict[str, List[PlanVersion]]) -> None:
    """A hash proves the files agree with each other, not that anyone checked the plan: the model only demands ``validation.passed``, so an archive can carry a plan whose
    vendors, prices and checks were written by hand and would be shown as verified (F-010). Every version is therefore run through this application's own validator, against
    its own parent, before anything is written; a plan that does not pass here is refused. (A plan that only fails because the vendor data has moved on since it was made is
    refused too: the message says which plan, and nothing is half-imported.)"""
    from eventmate.plan_store import draft_from_plan

    for plan_id, versions in plans.items():
        by_number = {v.version: v for v in versions}
        for plan in versions:
            parent = by_number.get(plan.parent_version) if plan.parent_version else None
            if plan.parent_version and parent is None:
                raise TransferError("bad_plan", "a plan version names a parent that is not in the archive")
            try:
                report = em.validator.validate(draft_from_plan(plan), parent, released_vendor_ids=plan.released_vendor_ids, newly_confirmed_vendor_ids=plan.newly_confirmed_vendor_ids)
            except Exception:
                raise TransferError("not_verified", f"plan {plan_id} version {plan.version} could not be checked by this application, so it was not imported") from None
            if not report.passed:
                raise TransferError("not_verified", f"plan {plan_id} version {plan.version} does not pass this application's own checks, so it was not imported")


def import_archive(raw: bytes, em, now: Optional[datetime] = None, check_space: Optional[Callable[[int], None]] = None) -> dict:
    """Validate the whole archive, then write it into the workspace ``em``. Returns a report. Raises ``TransferError`` before writing anything for a bad archive.
    ``check_space`` is called with the size the archive will occupy once unpacked, before anything is written, so a quota can refuse it (F-013)."""
    members = _read_members(raw)
    manifest, plans = _validated(members)
    ledger, leads, guests, history = _parse_records(members, plans)
    _reverify(em, plans)
    if check_space is not None:
        check_space(sum(len(d) for d in members.values()))
    store: PlanStore = em.store
    taken = set(store.list_plans())
    mapping: Dict[str, str] = {}
    for old in plans:
        new = old
        while new in taken or new in mapping.values():
            new = new_plan_id()
        taken.add(new)
        if new != old:
            mapping[old] = new
    created: List[str] = []
    counts = {"plans": 0, "versions": 0, "ledger_entries": 0, "guest_lists": 0, "leads": 0}
    try:
        for old, versions in plans.items():
            new = mapping.get(old, old)
            created.append(new)  # before the first save: a failure on any version undoes the ones already written (F-011)
            for plan in versions:
                if new != old:
                    plan = PlanVersion.model_validate({**json.loads(plan.model_dump_json()), "plan_id": new})  # the id field only, never a text replace over the plan (F-012); the version is hashed again on save
                store.save(plan)
                counts["versions"] += 1
            counts["plans"] += 1
            for entry in ledger.get(old, ()):
                em.ledger._file(new)._append(entry.model_copy(update={"plan_id": new}), entry.entry_id)
                counts["ledger_entries"] += 1
            for lead in leads.get(old, ()):
                em.lead_store._file(new)._append(lead.model_copy(update={"plan_id": new}), lead.lead_id)
                counts["leads"] += 1
            if old in guests:
                em.guest_lists.restore(new, guests[old])
                counts["guest_lists"] += 1
        kept = _keep_history(Path(store.root), history, mapping)
    except Exception:
        for plan_id in created:  # nothing half-imported is left behind
            _undo(em, plan_id)
        raise
    return {"imported": counts, "remapped": mapping, "hashes_verified": True, "history_kept": kept,
            "note": "Outreach and jobs are kept as history only and nothing in them can send; draft and approve again if you want to contact a vendor."}


def _undo(em, plan_id: str) -> None:
    from eventmate.retention import delete_plan_files

    em.guest_lists.delete(plan_id)
    delete_plan_files(em.store.root, plan_id)
    em.store.delete(plan_id)


def _keep_history(root: Path, history: Dict[str, List[dict]], mapping: Dict[str, str]) -> Dict[str, int]:
    if not history:
        return {}
    home = root / "imported" / secrets.token_hex(6)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    kept: Dict[str, int] = {}
    for name, rows in history.items():
        label = name.replace("history/", "").replace("/", "-")
        for old, new in mapping.items():
            label = label.replace(old, new)
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        for old, new in mapping.items():
            text = text.replace(old, new)
        fd = os.open(home / label, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        kept[label.rsplit(".", 1)[0]] = len(rows)
    return kept
