"""The opt-in store of original voice notes, tested directly (the web layer is covered in test_voice_api)."""

import os
import stat

import pytest

from eventmate.voice import audio
from eventmate.voice.notes import CONTENT_TYPE, NOTE_ID, NotesDisabled, VoiceNoteStore
from tests.test_voice_audio import WEBM, mp4, ogg, wav

DAY = 86400


@pytest.fixture
def clock():
    t = [1_000_000.0]
    return t


@pytest.fixture
def store(tmp_path, clock):
    return VoiceNoteStore(tmp_path, enabled=True, retention_days=30, clock=lambda: clock[0])


def age(store, plan, note_id, days, clock):
    f = next(p for p in (store.dir / plan).iterdir() if p.stem == note_id)
    t = clock[0] - days * DAY
    os.utime(f, (t, t))
    return f


def test_nothing_is_stored_unless_the_owner_turned_it_on(tmp_path):
    off = VoiceNoteStore(tmp_path, enabled=False)
    with pytest.raises(NotesDisabled):
        off.save("p1", wav(), "audio/wav")
    assert not (tmp_path / "voicenotes").exists()


@pytest.mark.parametrize("data,declared,ext", [(wav(1.5), "audio/wav", ".wav"), (WEBM, "audio/webm;codecs=opus", ".webm"), (ogg(2), "audio/ogg", ".ogg"), (mp4(2), "audio/mp4", ".m4a")])
def test_each_container_is_stored_under_a_random_name_with_its_own_type(store, data, declared, ext):
    note = store.save("p1", data, declared)
    assert NOTE_ID.fullmatch(note["note_id"]) and note["bytes"] == len(data)
    f = store.dir / "p1" / (note["note_id"] + ext)
    assert f.read_bytes() == data and stat.S_IMODE(f.stat().st_mode) == 0o600 and stat.S_IMODE(f.parent.stat().st_mode) == 0o700
    assert store.read("p1", note["note_id"]) == (data, CONTENT_TYPE[ext])


def test_the_length_is_reported_only_when_the_headers_say(store):
    assert store.save("p1", wav(1.5), "audio/wav")["duration_s"] == 1.5
    assert store.save("p1", WEBM, "audio/webm")["duration_s"] is None


def test_the_same_checks_as_any_upload_apply_and_nothing_is_written_when_they_fail(store):
    for data, declared in ((b"", "audio/wav"), (b"junk", "audio/wav"), (wav(), "audio/webm")):
        with pytest.raises(audio.AudioRejected):
            store.save("p1", data, declared)
    with pytest.raises(audio.AudioRejected):
        store.save("p1", WEBM + b"\x00" * audio.MAX_BYTES, "audio/webm")
    assert not (store.dir / "p1").exists() or list((store.dir / "p1").iterdir()) == []
    with pytest.raises(ValueError):
        store.save("../x", wav(), "audio/wav")


def test_ids_are_exact_and_a_note_belongs_to_its_plan(store):
    nid = store.save("p1", wav(), "audio/wav")["note_id"]
    assert store.read("p1", nid) and store.read("p2", nid) is None
    for bad in (None, 5, "", nid.upper(), nid[:-1], nid + "0", "../" + nid, "g" * 32, nid + ".wav"):
        assert store.read("p1", bad) is None and store.delete("p1", bad) is False
    with pytest.raises(ValueError):
        store.read("../x", nid)


def test_a_symlink_where_a_note_should_be_is_never_followed_or_served(store, tmp_path):
    nid = store.save("p1", wav(), "audio/wav")["note_id"]
    f = store.dir / "p1" / f"{nid}.wav"
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"secret")
    f.unlink()
    f.symlink_to(secret)
    assert store.read("p1", nid) is None and store.delete("p1", nid) is False and secret.read_bytes() == b"secret"


def test_deleting_overwrites_the_recording_first(store, monkeypatch):
    from pathlib import Path

    nid = store.save("p1", wav(), "audio/wav")["note_id"]
    seen = {}
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (seen.setdefault("c", self.read_bytes()), real(self, *a, **k)))
    assert store.delete("p1", nid) and set(seen["c"]) == {0} and len(seen["c"]) == len(wav())
    assert store.delete("p1", nid) is False and store.read("p1", nid) is None


def test_a_note_is_readable_up_to_the_retention_period_and_not_a_moment_after(store, clock):
    nid = store.save("p1", wav(), "audio/wav")["note_id"]
    age(store, "p1", nid, 29.99, clock)
    assert store.read("p1", nid)
    age(store, "p1", nid, 30, clock)
    assert store.read("p1", nid) is None and store.delete("p1", nid) is False  # expired notes are gone to every caller
    assert store.purge_expired() == [nid]


def test_purge_removes_only_expired_notes_and_tidies_empty_folders_leaving_strangers_alone(store, clock, tmp_path):
    keep = store.save("p1", wav(), "audio/wav")["note_id"]
    old1 = store.save("p1", wav(), "audio/wav")["note_id"]
    old2 = store.save("p2", WEBM, "audio/webm")["note_id"]
    age(store, "p1", old1, 31, clock)
    age(store, "p2", old2, 90, clock)
    stranger = store.dir / "p1" / "notes.txt"
    stranger.write_text("not a voice note")
    old = clock[0] - 400 * DAY
    os.utime(stranger, (old, old))
    (store.dir / "not a plan id").mkdir()
    (store.dir / "stray-file").write_text("x")
    assert sorted(store.purge_expired()) == sorted([old1, old2])
    assert store.read("p1", keep) and stranger.exists() and not (store.dir / "p2").exists() and (store.dir / "not a plan id").exists() and (store.dir / "stray-file").exists()
    assert VoiceNoteStore(tmp_path / "empty", enabled=True).purge_expired() == []


def test_deleting_a_plan_removes_every_note_and_its_folder(store):
    for _ in range(3):
        store.save("p1", wav(), "audio/wav")
    other = store.save("p2", wav(), "audio/wav")["note_id"]
    assert store.delete_plan("p1") == 3 and not (store.dir / "p1").exists()
    assert store.delete_plan("p1") == 0 and store.read("p2", other)
    with pytest.raises(ValueError):
        store.delete_plan("../x")


def test_the_settings_and_their_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTMATE_VOICE_KEEP_ORIGINAL", raising=False)
    monkeypatch.delenv("EVENTMATE_VOICE_NOTE_DAYS", raising=False)
    s = VoiceNoteStore(tmp_path)
    assert s.enabled is False and s.retention_days == 30
    s.enabled = True
    assert s.enabled is True
    for word in ("1", "true", "YES", " yes "):
        monkeypatch.setenv("EVENTMATE_VOICE_KEEP_ORIGINAL", word)
        assert VoiceNoteStore(tmp_path).enabled is True
    for word in ("0", "false", "no", "", "on"):
        monkeypatch.setenv("EVENTMATE_VOICE_KEEP_ORIGINAL", word)
        assert VoiceNoteStore(tmp_path).enabled is False
    assert VoiceNoteStore(tmp_path, retention_days=3).retention_days == 3
