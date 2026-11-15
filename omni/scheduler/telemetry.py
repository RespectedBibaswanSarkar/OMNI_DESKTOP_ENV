"""System telemetry: utilisation + Linux PSI + per-process table."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def read_psi(kind: str = "cpu") -> dict[str, float]:
    out = {"some10": 0.0, "some60": 0.0, "full10": 0.0}
    try:
        for line in Path(f"/proc/pressure/{kind}").read_text().splitlines():
            parts = line.split()
            kv = dict(p.split("=", 1) for p in parts[1:])
            if parts[0] == "some":
                out["some10"], out["some60"] = float(kv["avg10"]), float(kv["avg60"])
            elif parts[0] == "full":
                out["full10"] = float(kv["avg10"])
    except (OSError, ValueError, KeyError):
        pass
    return out


def system_state() -> dict[str, Any]:
    st: dict[str, Any] = {"cpu": 0.0, "mem": 0.0, "swap": 0.0, "disk": 0.0, "load1": 0.0,
                          "ncpu": os.cpu_count() or 1, "battery": None, "plugged": None,
                          "psi_cpu": read_psi("cpu")["some10"], "psi_mem": read_psi("memory")["some10"],
                          "psi_io": read_psi("io")["some10"]}
    try:
        st["load1"] = os.getloadavg()[0]
    except OSError:
        pass
    try:
        import psutil
        st["cpu"] = float(psutil.cpu_percent(interval=0.1))
        st["mem"] = float(psutil.virtual_memory().percent)
        st["swap"] = float(psutil.swap_memory().percent)
        st["disk"] = float(psutil.disk_usage("/").percent)
        bat = psutil.sensors_battery()
        if bat is not None:
            st["battery"], st["plugged"] = float(bat.percent), bool(bat.power_plugged)
    except Exception:  # noqa: BLE001 - psutil missing or restricted
        st["cpu"] = min(100.0, 100.0 * st["load1"] / st["ncpu"])
    return st


def cgroup_unit(pid: int) -> str:
    """systemd user-scope name (e.g. app-gnome-firefox-4242.scope) or '' for non-GUI processes."""
    try:
        path = Path(f"/proc/{pid}/cgroup").read_text().strip().splitlines()[-1].split("::", 1)[-1]
    except (OSError, IndexError):
        return ""
    last = path.rsplit("/", 1)[-1]
    return last if "app.slice" in path and last.endswith((".scope", ".service")) else ""


def process_table(sample_s: float = 0.4) -> list[dict[str, Any]]:
    import psutil
    uid, procs = os.getuid(), []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            if p.uids().real != uid:
                continue
            p.cpu_percent(None)
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(sample_s)
    rows = []
    for p in procs:
        try:
            rows.append({"pid": p.pid, "name": p.info.get("name") or "", "cpu": float(p.cpu_percent(None)),
                         "mem": float(p.info.get("memory_percent") or 0.0), "unit": cgroup_unit(p.pid)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return rows