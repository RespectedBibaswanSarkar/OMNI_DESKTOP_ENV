"""Push-to-talk capture for the panel's mic button (local faster-whisper, nothing leaves the machine)."""
from __future__ import annotations

import os
import tempfile
import wave

from omni.voice.service import VoiceUnavailable, transcribe_file


def listen(seconds: float = 6.0, sample_rate: int = 16000) -> str:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError as exc:
        raise VoiceUnavailable("Install sounddevice (pip install sounddevice) for microphone input.") from exc
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(np.asarray(audio).tobytes())
        return transcribe_file(path)
    finally:
        os.unlink(path)          # the recording is never kept
