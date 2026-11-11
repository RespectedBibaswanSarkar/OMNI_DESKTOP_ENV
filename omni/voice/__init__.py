"""Optional local voice input and output."""

__all__ = [
    "VoiceUnavailable",
    "record_and_transcribe",
    "speak_text",
    "transcribe_file",
]

def __getattr__(name):
    if name in __all__:
        from omni.voice import service
        return getattr(service, name)
    raise AttributeError(name)
