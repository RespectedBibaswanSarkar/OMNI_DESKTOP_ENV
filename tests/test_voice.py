from __future__ import annotations

import wave

import pytest

from omni.voice.service import (
    VoiceUnavailable,
    speak_text,
    transcribe_file,
)


def make_wav(path):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 160)


def test_transcribe_uses_injected_engine(tmp_path):
    audio = tmp_path / "sample.wav"
    make_wav(audio)
    result = transcribe_file(audio, engine=lambda path: "  hello OMNI  ")
    assert result == "hello OMNI"


def test_transcribe_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_file(tmp_path / "missing.wav", engine=lambda path: "x")


def test_transcribe_engine_exception_propagates(tmp_path):
    audio = tmp_path / "sample.wav"
    make_wav(audio)

    def broken(path):
        raise RuntimeError("engine failed")

    with pytest.raises(RuntimeError, match="engine failed"):
        transcribe_file(audio, engine=broken)


def test_speak_uses_injected_engine():
    spoken = []
    assert speak_text("Hello", engine=spoken.append)
    assert spoken == ["Hello"]


def test_speak_empty_text_does_not_call_engine():
    spoken = []
    assert not speak_text("  ", engine=spoken.append)
    assert spoken == []
