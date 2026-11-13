"""Real system metrics for the OMNI dashboard.

Every value is read from an actual source (/proc, os.statvfs, nvidia-smi).
When a source is unavailable the field is reported as ``None`` and the UI
shows "unavailable" instead of inventing a number.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def cpu_usage() -> dict[str, Any]:
    load1, load5, load15 = os.getloadavg()
    cores = os.cpu_count() or 1
    return {
        "load_1m": round(load1, 3),
        "load_5m": round(load5, 3),
        "load_15m": round(load15, 3),
        "cores": cores,
        "load_pct": round(min(100.0, load1 / max(cores, 1) * 100.0), 1),
    }


def memory_usage() -> dict[str, Any]:
    info = {}
    try:
        text = Path("/proc/meminfo").read_text()
        vals = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            vals[key.strip()] = float(rest.strip().split()[0]) / 1024.0  # MiB
        total = vals.get("MemTotal", 0.0)
        available = vals.get("MemAvailable", vals.get("MemFree", 0.0))
        used = max(total - available, 0.0)
        info = {
            "total_mb": round(total, 1),
            "used_mb": round(used, 1),
            "available_mb": round(available, 1),
            "used_pct": round(used / total * 100.0, 1) if total else None,
        }
    except (OSError, ValueError, IndexError):
        info = {"total_mb": None, "used_mb": None, "available_mb": None, "used_pct": None}
    return info


def disk_usage(path: str | Path = ".") -> dict[str, Any]:
    try:
        total, used, free = shutil.disk_usage(path)
        return {
            "path": str(Path(path).resolve()),
            "total_gb": round(total / 2**30, 1),
            "used_gb": round(used / 2**30, 1),
            "free_gb": round(free / 2**30, 1),
            "used_pct": round(used / total * 100.0, 1) if total else None,
        }
    except OSError:
        return {"path": str(path), "total_gb": None, "used_gb": None, "free_gb": None, "used_pct": None}


def gpu_stats() -> dict[str, Any] | None:
    """Query nvidia-smi when present; otherwise report that no GPU tooling works."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        line = out.stdout.strip().splitlines()[0]
        name, util, used, total = [part.strip() for part in line.split(",")]
        used_f, total_f = float(used), float(total)
        return {
            "name": name,
            "util_pct": float(util),
            "vram_used_mb": used_f,
            "vram_total_mb": total_f,
            "vram_used_pct": round(used_f / total_f * 100.0, 1) if total_f else None,
        }
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None


def snapshot(cwd: str | Path = ".") -> dict[str, Any]:
    return {
        "updated": round(time.time(), 3),
        "cpu": cpu_usage(),
        "memory": memory_usage(),
        "disk": disk_usage(cwd),
        "gpu": gpu_stats(),  # None => "no NVIDIA tooling available", never fabricated
    }
