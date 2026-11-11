from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench.tasks import BenchTask


@dataclass
class AdapterResult:
    ok: bool
    duration_s: float
    mock: bool = False
    cached_replay: bool = False
    returncode: int | None = None
    output: str = ""
    error: str | None = None
    timed_out: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "duration_s": round(self.duration_s, 4),
            "mock": self.mock,
            "cached_replay": self.cached_replay,
            "returncode": self.returncode,
            "output_tail": self.output[-800:],
            "error": self.error,
            "timed_out": self.timed_out,
            "metadata": self.metadata,
        }


class Adapter:
    name = "base"
    mock = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def run(self, task: BenchTask, workspace: Path) -> AdapterResult:
        raise NotImplementedError


class MockAdapter(Adapter):
    """Deterministic fixture adapter. Always tagged mock; excluded from figures."""

    name = "mock"
    mock = True

    def run(self, task: BenchTask, workspace: Path) -> AdapterResult:
        started = time.monotonic()
        if task.fixture is None:
            return AdapterResult(False, time.monotonic() - started, mock=True, error="task has no fixture")
        try:
            task.fixture(workspace)
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(False, time.monotonic() - started, mock=True, error=f"fixture failed: {exc}")
        return AdapterResult(
            True,
            time.monotonic() - started,
            mock=True,
            output="fixture applied",
            metadata={"adapter": "fixture", "excluded_reason": "mock run"},
        )


class OpenCodeAdapter(Adapter):
    """Run the installed OpenCode CLI non-interactively in the task workspace."""

    name = "opencode"
    mock = False

    def __init__(self, model: str | None = None, timeout_pad_s: float = 10.0) -> None:
        self.model = model
        self.timeout_pad_s = timeout_pad_s

    def available(self) -> tuple[bool, str]:
        binary = shutil.which("opencode")
        if not binary:
            return False, "opencode CLI not installed"
        return True, binary

    def run(self, task: BenchTask, workspace: Path) -> AdapterResult:
        available, detail = self.available()
        started = time.monotonic()
        if not available:
            return AdapterResult(False, 0.0, error=detail)
        prompt = (
            f"{task.goal}\n\n"
            "Work only inside the current directory. Do not ask clarifying questions. "
            "Make the required file changes directly, then stop."
        )
        command = [
            detail,
            "run",
            "--auto",
            "--format",
            "json",
            "--title",
            f"OMNI bench {task.id}",
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append(prompt)
        try:
            proc = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=task.timeout_s + self.timeout_pad_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return AdapterResult(
                False,
                time.monotonic() - started,
                timed_out=True,
                error=f"timed out after {exc.timeout}s",
            )
        except OSError as exc:
            return AdapterResult(False, time.monotonic() - started, error=str(exc))
        output = (proc.stdout or "") + (proc.stderr or "")
        metadata = _parse_usage(proc.stdout or "")
        metadata["adapter"] = "opencode"
        metadata["command"] = command[:7]
        return AdapterResult(
            ok=proc.returncode == 0,
            duration_s=time.monotonic() - started,
            returncode=proc.returncode,
            output=output,
            error=None if proc.returncode == 0 else f"opencode exited {proc.returncode}",
            metadata=metadata,
        )


class UnavailableAdapter(Adapter):
    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self.reason


def _parse_usage(stdout: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    events = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        if isinstance(event, dict):
            for key in ("sessionID", "session_id"):
                if key in event:
                    metadata["session_id"] = event[key]
            usage = event.get("usage")
            if isinstance(usage, dict):
                metadata["usage"] = usage
            tokens = event.get("tokens")
            if isinstance(tokens, dict):
                metadata["tokens"] = tokens
    metadata["json_events"] = events
    return metadata


def get_adapter(name: str) -> Adapter:
    if name == "mock":
        return MockAdapter()
    if name == "opencode":
        return OpenCodeAdapter()
    if name == "claude":
        return UnavailableAdapter("claude", "Claude CLI not installed")
    if name == "aider":
        return UnavailableAdapter("aider", "Aider CLI not installed")
    if name == "interpreter":
        return UnavailableAdapter("interpreter", "Open Interpreter not installed")
    raise KeyError(f"unknown adapter: {name}")


ADAPTER_NAMES: tuple[str, ...] = ("opencode", "claude", "aider", "interpreter")
