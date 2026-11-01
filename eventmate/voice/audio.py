"""What an uploaded audio file is, decided from its bytes and not from what the browser said it was (or what it was called)."""

from __future__ import annotations

import struct

MAX_BYTES = 5 * 1024 * 1024
MAX_SECONDS = 120

# the container families a browser's MediaRecorder produces, and the declared types each may arrive as
FAMILIES = {
    "webm": ("audio/webm", "video/webm"),
    "ogg": ("audio/ogg", "application/ogg", "audio/opus"),
    "mp4": ("audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac", "video/mp4"),
    "wav": ("audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"),
}
EXTENSION = {"webm": ".webm", "ogg": ".ogg", "mp4": ".m4a", "wav": ".wav"}


class AudioRejected(ValueError):
    """The upload is refused. ``status`` is the HTTP status to report; the message is safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def sniff(data: bytes) -> str | None:
    """The container family from the first bytes, or None."""
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    return None


def check(data: bytes, declared: str, max_bytes: int = MAX_BYTES) -> str:
    """Return the container family, or raise AudioRejected. The declared type must agree with the bytes."""
    if not data:
        raise AudioRejected(400, "the recording is empty")
    if len(data) > max_bytes:
        raise AudioRejected(413, f"the recording is larger than {max_bytes // (1024 * 1024)} MB")
    kind = sniff(data)
    if kind is None:
        raise AudioRejected(415, "that is not a supported audio recording (webm, ogg, mp4 or wav)")
    base = (declared or "").split(";", 1)[0].strip().lower()
    if base not in FAMILIES[kind]:
        raise AudioRejected(415, "the file's type does not match its contents")
    return kind


def probe_seconds(data: bytes, kind: str) -> float | None:
    """The recording's length from its headers when they say, else None (a live-recorded webm usually carries no duration; the decoder then decides)."""
    try:
        if kind == "wav":
            return _wav(data)
        if kind == "ogg":
            return _ogg(data)
        if kind == "mp4":
            return _mp4(data)
    except (struct.error, ValueError, IndexError):
        return None
    return None


def _wav(data: bytes) -> float | None:
    """Length from a PCM WAV's headers. A byte rate that is not rate x channels x bits would make a long recording look short, so it is not believed: the length
    is then unknown and the transcriber's own timeout, not this number, bounds the work (F-006). A data size that is too large only makes the note look longer."""
    pos, byte_rate, fmt = 12, 0, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt " and size >= 16:
            tag, channels, rate, byte_rate, _align, bits = struct.unpack("<HHIIHH", data[pos + 8:pos + 24])
            fmt = (tag, channels, rate, bits)
        if cid == b"data":
            if not byte_rate:
                return None
            if fmt and fmt[0] == 1 and byte_rate != fmt[1] * fmt[2] * fmt[3] // 8:
                return None
            return size / byte_rate
        pos += 8 + size + (size & 1)
    return None


def _ogg(data: bytes) -> float | None:
    first = data.find(b"OpusHead", 0, 400)
    vorbis = data.find(b"\x01vorbis", 0, 400)
    if first >= 0:
        rate = 48000
    elif vorbis >= 0:
        rate = struct.unpack("<I", data[vorbis + 12:vorbis + 16])[0]
    else:
        return None
    last = data.rfind(b"OggS")
    granule = struct.unpack("<q", data[last + 6:last + 14])[0]
    return granule / rate if granule > 0 and rate else None


def _mp4(data: bytes) -> float | None:
    def boxes(buf: bytes, start: int, end: int):
        pos = start
        while pos + 8 <= end:
            size, kind = struct.unpack(">I4s", buf[pos:pos + 8])
            header = 8
            if size == 1:
                size, header = struct.unpack(">Q", buf[pos + 8:pos + 16])[0], 16
            elif size == 0:
                size = end - pos
            if size < header:
                return
            yield kind, pos + header, pos + size
            pos += size

    for kind, a, b in boxes(data, 0, len(data)):
        if kind == b"moov":
            for k2, a2, _ in boxes(data, a, b):
                if k2 == b"mvhd":
                    version = data[a2]
                    if version == 1:
                        timescale, duration = struct.unpack(">IQ", data[a2 + 20:a2 + 32])
                    else:
                        timescale, duration = struct.unpack(">II", data[a2 + 12:a2 + 20])
                    return duration / timescale if timescale and duration else None
    return None
