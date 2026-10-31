"""Uploads are judged by their bytes; transcription is bounded, private and never invents text."""

import stat
import struct
import threading
import time
import pytest

from eventmate.voice import audio
from eventmate.voice.transcribe import FasterWhisperEngine, TranscribeError, Transcriber, Transcript, engine_from_env


def wav(seconds=1.0, rate=8000, body=64):
    size = int(seconds * rate * 2)
    head = b"RIFF" + struct.pack("<I", 36 + size) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", size)
    return head + b"\x00" * body


def ogg(seconds=5.0, codec="opus", rate=48000):
    first = b"OggS\x00\x02" + struct.pack("<q", 0) + b"\x00" * 12 + (b"OpusHead\x01\x01" if codec == "opus" else b"\x01vorbis" + struct.pack("<I", 0) + b"\x01" + struct.pack("<I", rate))  # vorbis identification header: version, channels, sample rate
    return first + b"\x00" * 40 + b"OggS\x00\x04" + struct.pack("<q", int(seconds * rate)) + b"\x00" * 20


def mp4(seconds=4.0, timescale=1000, version=0):
    payload = bytes([version, 0, 0, 0]) + (struct.pack(">II", 0, 0) if version == 0 else struct.pack(">QQ", 0, 0))
    payload += struct.pack(">I", timescale) + (struct.pack(">I", int(seconds * timescale)) if version == 0 else struct.pack(">Q", int(seconds * timescale))) + b"\x00" * 80
    mvhd = struct.pack(">I4s", 8 + len(payload), b"mvhd") + payload
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    return struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x00\x00" + moov


WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 60


# ------------------------------------------------------------------ sniffing and checks


@pytest.mark.parametrize("data,kind", [(WEBM, "webm"), (ogg(), "ogg"), (mp4(), "mp4"), (wav(), "wav"), (b"ID3\x03" + b"\x00" * 40, None), (b"%PDF-1.4", None), (b"MZ\x90", None), (b"", None), (b"RIFF\x00\x00\x00\x00AVI ", None)])
def test_the_container_is_decided_from_the_first_bytes(data, kind):
    assert audio.sniff(data) == kind


@pytest.mark.parametrize("data,declared", [
    (WEBM, "audio/webm"), (WEBM, "audio/webm;codecs=opus"), (WEBM, "video/webm"), (ogg(), "audio/ogg"), (ogg(), "audio/ogg; codecs=opus"), (mp4(), "audio/mp4"), (mp4(), "audio/x-m4a"),
    (wav(), "audio/wav"), (wav(), "audio/x-wav"), (wav(), "AUDIO/WAV"),
])
def test_a_declared_type_that_matches_the_bytes_is_accepted(data, declared):
    assert audio.check(data, declared) in ("webm", "ogg", "mp4", "wav")


@pytest.mark.parametrize("data,declared,status", [
    (b"", "audio/webm", 400), (WEBM, "audio/wav", 415), (wav(), "audio/webm", 415), (WEBM, "", 415), (WEBM, "text/html", 415), (WEBM, "application/octet-stream", 415),
    (b"<script>alert(1)</script>", "audio/webm", 415), (b"%PDF-1.4 " + b"x" * 50, "audio/wav", 415),
])
def test_everything_else_is_refused_with_a_reason(data, declared, status):
    with pytest.raises(audio.AudioRejected) as e:
        audio.check(data, declared)
    assert e.value.status == status and str(e.value)


def test_the_size_limit_is_exact():
    ok = WEBM + b"\x00" * (audio.MAX_BYTES - len(WEBM))
    assert audio.check(ok, "audio/webm") == "webm"
    with pytest.raises(audio.AudioRejected) as e:
        audio.check(ok + b"\x00", "audio/webm")
    assert e.value.status == 413


# ------------------------------------------------------------------ duration from headers


@pytest.mark.parametrize("data,kind,seconds", [
    (wav(2.5), "wav", 2.5), (wav(130), "wav", 130), (ogg(5), "ogg", 5.0), (ogg(3.5, "vorbis", 44100), "ogg", 3.5), (mp4(4), "mp4", 4.0), (mp4(7.5, 600, 1), "mp4", 7.5),
])
def test_the_length_is_read_from_the_headers_when_they_say(data, kind, seconds):
    assert audio.probe_seconds(data, kind) == pytest.approx(seconds)


@pytest.mark.parametrize("data,kind", [(WEBM, "webm"), (b"RIFF\x00\x00\x00\x00WAVE", "wav"), (b"OggS" + b"\x00" * 5, "ogg"), (mp4(0), "mp4"), (b"\x00\x00\x00\x10ftypisom\x00\x00", "mp4"), (wav()[:30], "wav")])
def test_a_missing_or_broken_header_means_unknown_not_a_guess_or_a_crash(data, kind):
    assert audio.probe_seconds(data, kind) is None


# ------------------------------------------------------------------ the transcriber


class FakeEngine:
    name, model, languages = "fake", "fake-tiny", ("en", "hi")

    def __init__(self, text="I want a vegetarian caterer", duration=3.0, language="en", gate=None, error=None):
        self.text, self.duration, self.language, self.gate, self.error = text, duration, language, gate, error
        self.seen = []

    def transcribe(self, path, language):
        self.seen.append({"path": path, "exists": path.exists(), "mode": stat.S_IMODE(path.stat().st_mode), "bytes": path.read_bytes(), "language": language})
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return Transcript(self.text, self.language, self.duration)


@pytest.fixture
def tmp(tmp_path):
    return tmp_path / "voice-tmp"


def leftovers(tmp):
    return list(tmp.iterdir()) if tmp.exists() else []


def test_no_engine_means_a_clear_refusal_and_never_an_invented_transcript(tmp):
    t = Transcriber(None, tmp)
    assert t.capabilities() == {"route": "none", "engine": None, "model": None, "languages": [], "max_seconds": 120, "max_bytes": audio.MAX_BYTES}
    with pytest.raises(TranscribeError) as e:
        t.transcribe(wav(), "audio/wav")
    assert e.value.status == 503 and "type your message instead" in str(e.value) and leftovers(tmp) == []


def test_a_note_is_transcribed_and_the_audio_is_gone_afterwards(tmp):
    eng = FakeEngine()
    t = Transcriber(eng, tmp)
    assert t.capabilities()["route"] == "server" and t.capabilities()["engine"] == "fake" and t.capabilities()["languages"] == ["en", "hi"]
    data = wav(3.0)
    got = t.transcribe(data, "audio/wav", "hi")
    assert got == Transcript("I want a vegetarian caterer", "en", 3.0)
    seen = eng.seen[0]
    assert seen["exists"] and seen["mode"] == 0o600 and seen["bytes"] == data and seen["language"] == "hi" and seen["path"].suffix == ".wav"
    assert stat.S_IMODE(tmp.stat().st_mode) == 0o700 and leftovers(tmp) == [] and not seen["path"].exists()


def test_the_audio_is_deleted_when_the_engine_fails_too_and_the_error_leaks_nothing(tmp):
    t = Transcriber(FakeEngine(error=RuntimeError("/home/secret/model.bin: CUDA exploded at 0xdead")), tmp)
    with pytest.raises(TranscribeError) as e:
        t.transcribe(wav(), "audio/wav")
    assert e.value.status == 500 and "secret" not in str(e.value) and "CUDA" not in str(e.value) and leftovers(tmp) == []


def test_text_is_cleaned_capped_and_an_empty_result_is_an_error_not_silence_made_up(tmp):
    assert Transcriber(FakeEngine(text="  hello \n  there  "), tmp).transcribe(wav(), "audio/wav").text == "hello there"
    assert len(Transcriber(FakeEngine(text="x " * 10000), tmp).transcribe(wav(), "audio/wav").text) == 5000
    with pytest.raises(TranscribeError) as e:
        Transcriber(FakeEngine(text="   "), tmp).transcribe(wav(), "audio/wav")
    assert e.value.status == 422 and "type your message" in str(e.value)


@pytest.mark.parametrize("data,declared,status", [(b"", "audio/wav", 400), (b"not audio at all", "audio/wav", 415), (wav(), "audio/webm", 415), (WEBM + b"\x00" * audio.MAX_BYTES, "audio/webm", 413)])
def test_bad_uploads_are_refused_before_the_engine_runs(tmp, data, declared, status):
    eng = FakeEngine()
    with pytest.raises(TranscribeError) as e:
        Transcriber(eng, tmp).transcribe(data, declared)
    assert e.value.status == status and eng.seen == [] and leftovers(tmp) == []


def test_a_recording_that_says_it_is_too_long_is_refused_without_decoding(tmp):
    eng = FakeEngine()
    t = Transcriber(eng, tmp, max_seconds=120)
    with pytest.raises(TranscribeError) as e:
        t.transcribe(wav(121), "audio/wav")
    assert e.value.status == 413 and "120 seconds" in str(e.value) and eng.seen == []
    assert t.transcribe(wav(120), "audio/wav").duration_s == 3.0  # exactly the limit is fine


def test_a_recording_whose_decoded_length_is_too_long_is_refused_and_its_text_dropped(tmp):
    with pytest.raises(TranscribeError) as e:
        Transcriber(FakeEngine(duration=120.5), tmp, max_seconds=120).transcribe(WEBM, "audio/webm")  # a live-recorded webm has no length in its header
    assert e.value.status == 413
    assert Transcriber(FakeEngine(duration=120.0), tmp, max_seconds=120).transcribe(WEBM, "audio/webm").duration_s == 120.0


def test_the_clients_filename_is_never_used_as_a_path(tmp, tmp_path):
    eng = FakeEngine()
    Transcriber(eng, tmp).transcribe(wav(), "audio/wav")  # the API has no filename parameter at all
    assert tmp in eng.seen[0]["path"].parents and eng.seen[0]["path"].name.startswith("tmp") and ".." not in eng.seen[0]["path"].name
    assert not (tmp_path / "etc").exists()


def test_the_engine_is_given_one_note_at_a_time_and_a_full_queue_says_busy(tmp):
    gate = threading.Event()
    eng = FakeEngine(gate=gate)
    t = Transcriber(eng, tmp, queue_limit=2)
    results, errors = [], []

    def go():
        try:
            results.append(t.transcribe(wav(), "audio/wav"))
        except TranscribeError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for th in threads:
        th.start()
    for _ in range(100):
        if len(eng.seen) == 1:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    assert len(eng.seen) == 1  # the second waits its turn
    with pytest.raises(TranscribeError) as e:
        t.transcribe(wav(), "audio/wav")  # a third would exceed the queue of two
    assert e.value.status == 429 and "busy" in str(e.value)
    gate.set()
    for th in threads:
        th.join(5)
    assert len(results) == 2 and errors == [] and leftovers(tmp) == []
    assert t.transcribe(wav(), "audio/wav").text  # and the queue is free again


def test_a_stuck_engine_times_out_and_the_audio_is_still_removed(tmp):
    gate = threading.Event()
    t = Transcriber(FakeEngine(gate=gate), tmp, timeout_margin_s=0)
    started = time.monotonic()
    with pytest.raises(TranscribeError) as e:
        t.transcribe(wav(0.1), "audio/wav")  # 2 x 0.1 s + 0 margin
    assert e.value.status == 504 and time.monotonic() - started < 3 and leftovers(tmp) == []
    gate.set()


def test_the_timeout_scales_with_the_audio_length(tmp):
    gate = threading.Event()
    t = Transcriber(FakeEngine(gate=gate), tmp, timeout_margin_s=0)
    started = time.monotonic()
    with pytest.raises(TranscribeError):
        t.transcribe(wav(0.4), "audio/wav")
    took = time.monotonic() - started
    assert 0.6 < took < 3  # about twice 0.4 s
    gate.set()


# ------------------------------------------------------------------ engine from the environment


def test_the_engine_is_off_unless_the_owner_enables_it_and_names_a_real_model_folder(tmp_path):
    assert engine_from_env({}) is None
    assert engine_from_env({"EVENTMATE_VOICE_ENGINE": "other"}) is None
    assert engine_from_env({"EVENTMATE_VOICE_ENGINE": "faster-whisper"}) is None  # no model named
    assert engine_from_env({"EVENTMATE_VOICE_ENGINE": "faster-whisper", "EVENTMATE_VOICE_MODEL": str(tmp_path / "missing")}) is None
    # a real folder, but faster-whisper is not installed here: voice stays off rather than failing the app
    assert engine_from_env({"EVENTMATE_VOICE_ENGINE": " Faster-Whisper ", "EVENTMATE_VOICE_MODEL": str(tmp_path)}) is None
    assert FasterWhisperEngine.languages == ("en", "hi", "mr")


def test_the_unrun_real_engine_is_labelled_as_such():
    assert "has not been run" in FasterWhisperEngine.__doc__ and "MIT" in FasterWhisperEngine.__doc__


# ------------------------------------------------------------------ container edge cases real files have


def riff(*chunks):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def chunk(cid, payload):
    return cid + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


FMT = chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))


def test_a_wav_with_extra_padded_chunks_before_the_audio_is_still_measured():
    data_chunk = b"data" + struct.pack("<I", 16000 * 3)
    assert audio.probe_seconds(riff(chunk(b"LIST", b"abc"), FMT, chunk(b"fact", b"xyz"), data_chunk), "wav") == pytest.approx(3.0)  # odd-sized chunks are padded to even
    assert audio.probe_seconds(riff(FMT, data_chunk), "wav") == pytest.approx(3.0)
    assert audio.probe_seconds(riff(data_chunk, FMT), "wav") is None  # audio before its format: the rate is unknown
    assert audio.probe_seconds(riff(chunk(b"fmt ", b"\x00" * 8), data_chunk), "wav") is None  # a format chunk too short to carry a rate
    assert audio.probe_seconds(riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 0, 2, 16)), data_chunk), "wav") is None  # zero bytes per second


def box(kind, payload, big=False, zero=False):
    if big:
        return struct.pack(">I4sQ", 1, kind, 16 + len(payload)) + payload
    return struct.pack(">I4s", 0 if zero else 8 + len(payload), kind) + payload


def mvhd(seconds=6.0, timescale=1000):
    return box(b"mvhd", bytes([0, 0, 0, 0]) + struct.pack(">IIII", 0, 0, timescale, int(seconds * timescale)) + b"\x00" * 80)


def test_an_mp4_is_measured_whatever_order_its_boxes_come_in_and_however_their_sizes_are_written():
    ftyp = box(b"ftyp", b"isom\x00\x00\x00\x00")
    assert audio.probe_seconds(ftyp + box(b"mdat", b"\x00" * 40) + box(b"moov", mvhd(6)), "mp4") == pytest.approx(6.0)  # media data first, then the movie header
    assert audio.probe_seconds(box(b"ftyp", b"isom\x00\x00\x00\x00", big=True) + box(b"moov", mvhd(2), big=True), "mp4") == pytest.approx(2.0)  # 64-bit box sizes
    assert audio.probe_seconds(ftyp + box(b"moov", mvhd(9), zero=True), "mp4") == pytest.approx(9.0)  # a last box that runs to the end of the file
    assert audio.probe_seconds(ftyp + box(b"moov", box(b"trak", b"\x00" * 20) + mvhd(3)), "mp4") == pytest.approx(3.0)  # other boxes inside moov come first
    assert audio.probe_seconds(ftyp + box(b"free", b"\x00" * 8), "mp4") is None  # no movie header at all
    assert audio.probe_seconds(ftyp + struct.pack(">I4s", 4, b"moov") + b"\x00" * 40, "mp4") is None  # a box smaller than its own header ends the walk, never loops


def test_an_ogg_whose_codec_header_is_far_in_or_missing_or_whose_length_is_unwritten_is_unknown():
    page = b"OggS\x00\x02" + struct.pack("<q", 0) + b"\x00" * 12
    assert audio.probe_seconds(page + b"\x00" * 200 + b"OpusHead" + b"\x00" * 10 + b"OggS\x00\x04" + struct.pack("<q", 96000) + b"\x00" * 20, "ogg") == pytest.approx(2.0)  # inside the first 400 bytes
    assert audio.probe_seconds(page + b"\x00" * 500 + b"OpusHead" + b"OggS\x00\x04" + struct.pack("<q", 96000) + b"\x00" * 20, "ogg") is None  # too far in to trust
    assert audio.probe_seconds(page + b"OpusHead" + b"OggS\x00\x04" + struct.pack("<q", 0) + b"\x00" * 20, "ogg") is None  # granule 0
    assert audio.probe_seconds(page + b"OpusHead" + b"OggS\x00\x04" + struct.pack("<q", -1) + b"\x00" * 20, "ogg") is None  # granule unset
    assert audio.probe_seconds(page + b"\x01vorbis" + struct.pack("<I", 0) + b"\x01" + struct.pack("<I", 0) + b"OggS\x00\x04" + struct.pack("<q", 5000) + b"\x00" * 20, "ogg") is None  # a rate of 0


def test_the_size_message_names_the_limit_and_the_declared_type_may_carry_spaces():
    with pytest.raises(audio.AudioRejected) as e:
        audio.check(WEBM + b"\x00" * audio.MAX_BYTES, "audio/webm")
    assert str(e.value) == "the recording is larger than 5 MB" and e.value.status == 413
    with pytest.raises(audio.AudioRejected) as e:
        audio.check(b"", "audio/webm")
    assert str(e.value) == "the recording is empty" and e.value.args == ("the recording is empty",)
    assert audio.check(WEBM, "  audio/webm ; codecs=opus ") == "webm"
    assert audio.MAX_BYTES == 5 * 1024 * 1024 and audio.MAX_SECONDS == 120


def test_transcribe_errors_carry_only_their_message_and_status(tmp):
    with pytest.raises(TranscribeError) as e:
        Transcriber(None, tmp).transcribe(wav(), "audio/wav")
    assert str(e.value) == "voice transcription is not set up here; type your message instead" and e.value.status == 503


def test_an_engine_that_raises_its_own_transcribe_error_is_passed_through_unchanged(tmp):
    with pytest.raises(TranscribeError) as e:
        Transcriber(FakeEngine(error=TranscribeError(422, "the engine says no")), tmp).transcribe(wav(), "audio/wav")
    assert e.value.status == 422 and str(e.value) == "the engine says no" and leftovers(tmp) == []


def test_the_queue_slot_is_given_back_after_every_note(tmp):
    t = Transcriber(FakeEngine(), tmp, queue_limit=1)
    for _ in range(3):
        assert t.transcribe(wav(), "audio/wav").text  # with queue_limit 1 a leaked slot would turn the second into a 429
    t2 = Transcriber(FakeEngine(error=RuntimeError("x")), tmp, queue_limit=1)
    for _ in range(2):
        with pytest.raises(TranscribeError) as e:
            t2.transcribe(wav(), "audio/wav")
        assert e.value.status == 500  # a failure gives the slot back too


def test_the_duration_is_rounded_to_hundredths_and_the_timeout_is_about_twice_the_audio(tmp):
    assert Transcriber(FakeEngine(duration=3.14159), tmp).transcribe(wav(), "audio/wav").duration_s == 3.14
    gate = threading.Event()
    t = Transcriber(FakeEngine(gate=gate), tmp, timeout_margin_s=0)
    started = time.monotonic()
    with pytest.raises(TranscribeError):
        t.transcribe(wav(0.4), "audio/wav")
    assert 0.7 < time.monotonic() - started < 1.1  # 2 x 0.4 s, not 3 x
    gate.set()


def test_the_engine_is_built_from_the_environment_with_the_owners_settings(tmp_path, monkeypatch):
    from eventmate.voice import transcribe as mod

    made = {}

    class Stub:
        name, model, languages = "stub", "m", ("en",)

        def __init__(self, path, device, compute):
            made.update(path=path, device=device, compute=compute)

    monkeypatch.setattr(mod, "FasterWhisperEngine", Stub)
    env = {"EVENTMATE_VOICE_ENGINE": "faster-whisper", "EVENTMATE_VOICE_MODEL": str(tmp_path)}
    assert isinstance(mod.engine_from_env(env), Stub) and made == {"path": str(tmp_path), "device": "cpu", "compute": "int8"}
    assert isinstance(mod.engine_from_env({**env, "EVENTMATE_VOICE_DEVICE": "cuda", "EVENTMATE_VOICE_COMPUTE": "float16"}), Stub) and made["device"] == "cuda" and made["compute"] == "float16"
    assert mod.engine_from_env({"EVENTMATE_VOICE_ENGINE": "faster-whisper", "EVENTMATE_VOICE_MODEL": ""}) is None
    assert mod.engine_from_env({"EVENTMATE_VOICE_ENGINE": "faster-whisper", "EVENTMATE_VOICE_MODEL": str(tmp_path / "nope")}) is None
    monkeypatch.setattr(mod, "FasterWhisperEngine", lambda *a: (_ for _ in ()).throw(ImportError("not installed")))
    assert mod.engine_from_env(env) is None


def test_the_real_engine_passes_a_known_language_only_and_joins_the_segments(tmp_path, monkeypatch):
    import sys
    import types

    calls = {}

    class Model:
        def __init__(self, path, device, compute_type, local_files_only):
            calls["init"] = (path, device, compute_type, local_files_only)

        def transcribe(self, path, language, vad_filter):
            calls["run"] = (path, language, vad_filter)
            seg = lambda s: types.SimpleNamespace(text=s)  # noqa: E731
            return [seg(" hello "), seg("there ")], types.SimpleNamespace(language="en", duration=2.5)

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    eng = FasterWhisperEngine(str(tmp_path), "cpu", "int8")
    assert calls["init"] == (str(tmp_path), "cpu", "int8", True) and eng.model == tmp_path.name  # offline only: it can never download a model
    assert eng.transcribe(tmp_path / "a.wav", "hi") == Transcript("hello there", "en", 2.5) and calls["run"][1:] == ("hi", True)
    eng.transcribe(tmp_path / "a.wav", "fr")
    assert calls["run"][1] is None  # a language outside the supported set is left to the engine to detect
