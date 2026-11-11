from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omni.scheduler.policy import ScheduleAction


def _detect_cgroup_base() -> Path | None:
    """Find a writable cgroup v2 directory that exposes the cpu controller."""
    override = os.environ.get("OMNI_CGROUP_ROOT")
    if override:
        path = Path(override)
        return path if path.exists() else None
    try:
        relative = Path("/proc/self/cgroup").read_text(encoding="utf-8").strip().split(":")[-1]
    except OSError:
        return None
    current = Path("/sys/fs/cgroup") / relative.lstrip("/")
    while current != Path("/sys/fs/cgroup") and current != current.parent:
        try:
            controllers = (current / "cgroup.controllers").read_text(encoding="utf-8").split()
            delegated = (current / "cgroup.subtree_control").read_text(encoding="utf-8").split()
        except OSError:
            controllers, delegated = [], []
        # A base is useful only if it both exposes cpu and delegates it to
        # children, otherwise a child group has no cpu.weight file.
        if "cpu" in controllers and "cpu" in delegated and os.access(current, os.W_OK):
            return current
        current = current.parent
    return None


@dataclass
class CgroupV2Actuator:
    """CPU/IO scheduling actuation through cgroup v2; dry-run unless requested.

    Never elevates privileges, never loads kernel modules, and never moves
    processes unless a pid is explicitly supplied. ``restore`` is the kill switch.
    """

    base: Path | None = None
    group: str = "omni-sched"
    state_path: Path | None = None
    allow_renice: bool = False
    originals: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.base = Path(self.base) if self.base is not None else _detect_cgroup_base()
        self.state_path = Path(self.state_path) if self.state_path else Path(
            os.environ.get("OMNI_SCHEDULER_STATE", Path.home() / ".cache" / "omni" / "scheduler" / "actuator.json")
        )
        self._load_state()

    @property
    def group_path(self) -> Path | None:
        return (self.base / self.group) if self.base is not None else None

    def available(self) -> bool:
        return self.base is not None and self.base.exists() and os.access(self.base, os.W_OK)

    def status(self) -> dict[str, Any]:
        return {
            "backend": "cgroup-v2",
            "available": self.available(),
            "base": str(self.base) if self.base else None,
            "group": str(self.group_path) if self.group_path else None,
            "tracked_files": len(self.originals),
            "kill_switch": True,
        }

    def _load_state(self) -> None:
        if not self.state_path or not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.originals = {str(k): str(v) for k, v in payload.get("originals", {}).items()}
        except (OSError, json.JSONDecodeError, AttributeError):
            self.originals = {}

    def _save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"updated": time.time(), "originals": self.originals}, indent=2),
            encoding="utf-8",
        )

    def _write(self, path: Path, value: str) -> None:
        key = str(path)
        if key not in self.originals:
            try:
                self.originals[key] = path.read_text(encoding="utf-8")
            except OSError:
                self.originals[key] = "100\n"
        path.write_text(value, encoding="utf-8")

    def _weight_text(self, path: Path, value: int) -> str:
        return f"default {value}\n" if path.name == "io.weight" else f"{value}\n"

    def apply(self, action: ScheduleAction, pid: int | None = None, dry_run: bool = True) -> dict[str, Any]:
        if dry_run:
            return {
                "applied": False,
                "dry_run": True,
                "backend": "cgroup-v2",
                "reason": "dry-run (pass --apply to actuate)",
                "would_write": {"cpu.weight": action.cpu_weight, "io.weight": action.io_weight},
            }
        if not self.available() or self.group_path is None:
            return {
                "applied": False,
                "dry_run": False,
                "backend": "cgroup-v2",
                "reason": "no writable delegated cgroup v2 with cpu controller",
            }
        written: dict[str, Any] = {}
        try:
            self.group_path.mkdir(parents=True, exist_ok=True)
            for name, value in (("cpu.weight", action.cpu_weight), ("io.weight", action.io_weight)):
                path = self.group_path / name
                if path.exists():
                    self._write(path, self._weight_text(path, value))
                    written[name] = value
            if pid is not None:
                procs = self.group_path / "cgroup.procs"
                if not procs.exists():
                    raise OSError("cgroup.procs missing")
                procs.write_text(f"{int(pid)}\n", encoding="utf-8")
                written["pid"] = int(pid)
            self._save_state()
        except OSError as exc:
            return {"applied": False, "dry_run": False, "backend": "cgroup-v2", "reason": f"cgroup write failed: {exc}"}
        if pid is not None and self.allow_renice:
            written["renice"] = self.apply_nice(pid, action, dry_run=False)
        return {
            "applied": bool(written),
            "dry_run": False,
            "backend": "cgroup-v2",
            "group": str(self.group_path),
            "written": written,
        }

    def nice_commands(self, pid: int, action: ScheduleAction) -> list[list[str]]:
        commands: list[list[str]] = []
        if shutil.which("renice"):
            commands.append(["renice", "-n", str(action.nice), "-p", str(int(pid))])
        if shutil.which("ionice"):
            commands.append(["ionice", "-c", "2", "-n", "4", "-p", str(int(pid))])
        return commands

    def apply_nice(self, pid: int, action: ScheduleAction, dry_run: bool = True) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for command in self.nice_commands(pid, action):
            if dry_run:
                results.append({"command": command, "dry_run": True})
                continue
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
                results.append({"command": command, "returncode": proc.returncode})
            except (OSError, subprocess.TimeoutExpired) as exc:
                results.append({"command": command, "error": str(exc)})
        return results

    def restore(self) -> dict[str, Any]:
        """Kill switch: restore every touched weight file to its original value."""
        restored: dict[str, str] = {}
        errors: list[str] = []
        for key, original in list(self.originals.items()):
            path = Path(key)
            try:
                path.write_text(original, encoding="utf-8")
                restored[key] = original.strip()
            except OSError as exc:
                errors.append(f"{key}: {exc}")
        group_path = self.group_path
        if group_path is not None:
            for name in ("cpu.weight", "io.weight"):
                path = group_path / name
                if path.exists() and str(path) not in self.originals:
                    try:
                        path.write_text(self._weight_text(path, 100), encoding="utf-8")
                        restored[str(path)] = "100"
                    except OSError as exc:
                        errors.append(f"{path}: {exc}")
        self.originals.clear()
        if self.state_path and self.state_path.exists():
            try:
                self.state_path.unlink()
            except OSError as exc:
                errors.append(f"{self.state_path}: {exc}")
        return {"restored": restored, "errors": errors, "kill_switch": "restored"}
