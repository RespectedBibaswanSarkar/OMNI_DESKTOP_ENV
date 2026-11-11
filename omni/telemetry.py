from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def mock_enabled() -> bool:
    """True when the process is running in mock-LLM mode."""
    return os.environ.get("OMNI_LLM_MOCK", "").lower() in {"1", "true", "yes"} or os.environ.get("OMNI_LLM_BACKEND", "").lower() == "mock"


class JsonlLogger:
    """Append-only JSONL logger. Every record carries an explicit mock flag."""

    def __init__(self, path: str | Path, mock: bool | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mock = mock_enabled() if mock is None else mock

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {"ts": time.time(), "event": event, **fields}
        record["mock"] = bool(fields.get("mock", self.mock))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record
