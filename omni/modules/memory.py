from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryState:
    captured_at: float
    mem_total_mb: float
    mem_available_mb: float
    cgroup_current_mb: float | None
    cgroup_max_mb: float | None
    psi_some_avg10: float | None
    pressure: float
    budget_mb: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at,
            "mem_total_mb": round(self.mem_total_mb, 1),
            "mem_available_mb": round(self.mem_available_mb, 1),
            "cgroup_current_mb": None if self.cgroup_current_mb is None else round(self.cgroup_current_mb, 1),
            "cgroup_max_mb": None if self.cgroup_max_mb is None else round(self.cgroup_max_mb, 1),
            "psi_some_avg10": self.psi_some_avg10,
            "pressure": round(self.pressure, 4),
            "budget_mb": round(self.budget_mb, 2),
        }


def _read_meminfo(path: Path) -> tuple[float, float] | None:
    try:
        values: dict[str, float] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.strip().split()
            if parts and parts[0].replace(".", "", 1).isdigit():
                values[key] = float(parts[0]) / 1024.0
        return values.get("MemTotal", 0.0), values.get("MemAvailable", 0.0)
    except OSError:
        return None


def _read_psi(path: Path) -> float | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                for field in line.split():
                    if field.startswith("avg10="):
                        return float(field.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def _read_cgroup(directory: Path) -> tuple[float | None, float | None]:
    def read_mb(name: str) -> float | None:
        try:
            text = (directory / name).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text == "max":
            return None
        try:
            return float(text) / (1024.0 * 1024.0)
        except ValueError:
            return None

    return read_mb("memory.current"), read_mb("memory.max")


def measure_memory(
    max_budget_mb: float = 256.0,
    floor_ratio: float = 0.15,
    meminfo_path: Path | None = None,
    psi_path: Path | None = None,
    cgroup_dir: Path | None = None,
) -> MemoryState:
    """Read real memory pressure and shrink the module budget accordingly.

    pressure = max(available-pressure, PSI-pressure):
      available-pressure rises linearly as MemAvailable/T MemTotal falls below 0.35
      PSI-pressure = min(psi_some_avg10 / 50, 1)  (design mapping, documented)
    budget = max_budget * max(floor_ratio, 1 - pressure)
    """
    mem = _read_meminfo(meminfo_path or Path("/proc/meminfo"))
    if mem is None:
        total, available = 0.0, 0.0
        avail_pressure = 1.0
    else:
        total, available = mem
        ratio = (available / total) if total > 0 else 0.0
        avail_pressure = min(1.0, max(0.0, (0.35 - ratio) / 0.35))
    psi = _read_psi(psi_path or Path("/proc/pressure/memory"))
    psi_pressure = min(1.0, max(0.0, (psi or 0.0) / 50.0))
    pressure = max(avail_pressure, psi_pressure)
    budget = float(max_budget_mb) * max(float(floor_ratio), 1.0 - pressure)
    current, maximum = _read_cgroup(cgroup_dir or Path("/sys/fs/cgroup"))
    return MemoryState(
        captured_at=time.time(),
        mem_total_mb=total,
        mem_available_mb=available,
        cgroup_current_mb=current,
        cgroup_max_mb=maximum,
        psi_some_avg10=psi,
        pressure=pressure,
        budget_mb=budget,
    )
