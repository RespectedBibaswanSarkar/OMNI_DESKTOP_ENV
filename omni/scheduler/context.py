from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from omni.context.features import ContextSnapshot
from omni.modules.memory import MemoryState, measure_memory

# Hand-authored interactivity prior by app class. This is a design prior used
# only when the KRLS model has no observations; it is not a measurement.
_INTERACTIVE_PATTERNS = (
    "chrome", "chromium", "firefox", "brave", "edge", "browser",
    "code", "codium", "vscode", "editor", "writer", "obsidian", "notion",
    "terminal", "alacritty", "kitty", "foot", "konsole", "wezterm",
    "nautilus", "thunar", "dolphin", "nemo", "slack", "discord",
)

FEATURE_NAMES: tuple[str, ...] = (
    "load_norm",
    "cpu_pressure",
    "mem_pressure",
    "client_norm",
    "interactive",
    "focus_age_norm",
)


@dataclass(frozen=True)
class ScheduleFeatures:
    load_norm: float
    cpu_pressure: float
    mem_pressure: float
    client_norm: float
    interactive: float
    focus_age_norm: float

    def as_array(self):
        import numpy as np

        return np.array(
            [
                self.load_norm,
                self.cpu_pressure,
                self.mem_pressure,
                self.client_norm,
                self.interactive,
                self.focus_age_norm,
            ],
            dtype=np.float64,
        )

    def as_dict(self) -> dict[str, float]:
        return {name: round(float(value), 4) for name, value in zip(FEATURE_NAMES, self.as_array())}


def _read_loadavg(path: Path) -> float:
    try:
        return float(path.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def _read_psi_some(path: Path) -> float | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                for field in line.split():
                    if field.startswith("avg10="):
                        return float(field.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def is_interactive_app(app_class: str) -> bool:
    app = (app_class or "").lower()
    return any(pattern in app for pattern in _INTERACTIVE_PATTERNS)


def collect_features(
    snapshot: ContextSnapshot,
    memory: MemoryState | None = None,
    loadavg_path: Path | None = None,
    cpu_psi_path: Path | None = None,
    cpu_count: int | None = None,
) -> ScheduleFeatures:
    """Read real load/PSI/memory and combine with the focused-window context."""
    cpus = max(int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)), 1)
    load_norm = min(1.0, max(0.0, _read_loadavg(loadavg_path or Path("/proc/loadavg")) / cpus))
    psi = _read_psi_some(cpu_psi_path or Path("/proc/pressure/cpu"))
    cpu_pressure = min(1.0, max(0.0, (psi or 0.0) / 50.0))
    memory = memory or measure_memory()
    client_norm = min(1.0, max(0.0, float(snapshot.client_count) / 12.0))
    interactive = 1.0 if is_interactive_app(snapshot.app_class) else 0.0
    focus_age_norm = min(1.0, max(0.0, math.log1p(max(snapshot.focused_age_s, 0.0)) / 5.0))
    return ScheduleFeatures(
        load_norm=load_norm,
        cpu_pressure=cpu_pressure,
        mem_pressure=min(1.0, max(0.0, float(memory.pressure))),
        client_norm=client_norm,
        interactive=interactive,
        focus_age_norm=focus_age_norm,
    )
