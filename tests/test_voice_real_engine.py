"""Opt-in: run the real local transcriber on a recording you supply, and report how long it took.

Off by default, and the only test in the suite that skips by design: it needs a transcription engine the owner has chosen to install, a model folder
fetched once in advance, and a sample recording. Set all three to run it:

    EVENTMATE_VOICE_ENGINE=faster-whisper EVENTMATE_VOICE_MODEL=/path/to/model EVENTMATE_VOICE_SAMPLE=/path/to/note.wav python -m pytest tests/test_voice_real_engine.py -s

It proves the engine loads offline and returns text for your recording, that the temporary audio is removed, and prints the measured seconds. It does
not check accuracy: judge that by reading the printed transcript against what was said, on several of your own recordings and accents.
"""

import os
import time
from pathlib import Path

import pytest

from eventmate.voice.transcribe import Transcriber, engine_from_env

SAMPLE = os.environ.get("EVENTMATE_VOICE_SAMPLE", "")
ENGINE = engine_from_env(os.environ) if SAMPLE else None
pytestmark = pytest.mark.skipif(ENGINE is None or not Path(SAMPLE).is_file(), reason="opt-in: needs EVENTMATE_VOICE_ENGINE, EVENTMATE_VOICE_MODEL and EVENTMATE_VOICE_SAMPLE")

DECLARED = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".mp4": "audio/mp4"}


def test_the_real_engine_transcribes_the_sample_and_leaves_no_audio_behind(tmp_path):
    sample = Path(SAMPLE)
    t = Transcriber(ENGINE, tmp_path / "tmp")
    started = time.perf_counter()
    got = t.transcribe(sample.read_bytes(), DECLARED[sample.suffix.lower()])
    took = time.perf_counter() - started
    print(f"\nengine={ENGINE.name} model={ENGINE.model} audio={got.duration_s}s took={took:.2f}s ({took / max(got.duration_s, 0.1):.2f}x real time) language={got.language}")
    print(f"transcript: {got.text}")
    assert got.text and got.duration_s > 0
    assert list((tmp_path / "tmp").iterdir()) == []
