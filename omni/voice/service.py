from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable


class VoiceUnavailable(RuntimeError):
    """Raised when an optional local audio capability is unavailable."""


def transcribe_file(
    audio_path: str | Path,
    engine: Callable[[str], str] | None = None,
) -> str:
    """Transcribe an audio file using faster-whisper or an injected engine."""
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    if engine is not None:
        text = engine(str(path))
        return str(text).strip()

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceUnavailable(
            "Speech recognition is unavailable. Install faster-whisper "
            "or use OMNI's text input."
        ) from exc

    model_size = os.environ.get("OMNI_WHISPER_MODEL", "base")
    try:
        model = WhisperModel(
            model_size,
            device=os.environ.get("OMNI_WHISPER_DEVICE", "auto"),
            compute_type=os.environ.get("OMNI_WHISPER_COMPUTE", "int8"),
        )
        segments, _ = model.transcribe(str(path), vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        raise VoiceUnavailable(f"Speech recognition failed: {exc}") from exc


def speak_text(text, engine=None):
    """Speak using pyttsx3, falling back to espeak-ng/espeak."""
    text = str(text).strip()
    if not text:
        return False

    if engine is not None:
        engine(text)
        return True

    try:
        import pyttsx3
        speaker = pyttsx3.init()
        speaker.say(text)
        speaker.runAndWait()
        speaker.stop()
        return True
    except Exception:
        pass

    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if not executable:
        return False

    try:
        result = subprocess.run(
            [executable, text],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def record_and_transcribe(
    sample_rate: int = 16000,
    channels: int = 1,
    engine: Callable[[str], str] | None = None,
) -> str:
    """Push-to-talk style terminal capture: Enter starts, Enter stops.

    The recording is temporary and deleted after transcription. No
    background microphone listener is installed.
    """
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError as exc:
        raise VoiceUnavailable(
            "Microphone capture is unavailable. Install sounddevice and "
            "numpy, or use text input."
        ) from exc

    chunks = []
    print("Press Enter to start recording.")
    input()
    print("Recording. Press Enter to stop.")

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            callback=lambda data, frames, timing, status: chunks.append(
                data.copy()
            ),
        ):
            input()
    except Exception as exc:
        raise VoiceUnavailable(f"Microphone capture failed: {exc}") from exc

    if not chunks:
        raise VoiceUnavailable("No audio was captured.")

    audio = np.concatenate(chunks, axis=0)
    if audio.size == 0:
        raise VoiceUnavailable("The recording was empty.")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = Path(tmp.name)

        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(audio.tobytes())

        return transcribe_file(temp_path, engine=engine)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="OMNI optional voice tools")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("listen", help="Record with explicit Enter-to-start/stop")
    say = sub.add_parser("speak", help="Speak text using local TTS")
    say.add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.command == "listen":
        try:
            result = record_and_transcribe()
        except (VoiceUnavailable, FileNotFoundError) as exc:
            print(f"Voice unavailable: {exc}", file=sys.stderr)
            print("Use the normal text input instead.")
            return 2
        print(result)
        return 0 if result else 1

    if args.command == "speak":
        text = " ".join(args.text)
        if not speak_text(text):
            print(
                "Local TTS unavailable. Text response remains available.",
                file=sys.stderr,
            )
            return 2
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
