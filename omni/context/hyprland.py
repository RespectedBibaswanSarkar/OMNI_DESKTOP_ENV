from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from omni.context.atspi import AtspiCollector
from omni.context.features import ContextSnapshot

Runner = Callable[[list[str], float], subprocess.CompletedProcess]


def _default_runner(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


class HyprlandEventStream:
    """Minimal socket2 reader. Keeps aggregates and focus timing, never keystrokes."""

    FOCUS_EVENTS = {"activewindow", "activewindowv2", "focuswindow", "workspace", "workspacev2"}

    def __init__(self, instance_signature: str | None = None, runtime_dir: str | None = None, on_event: Callable[[str, str], None] | None = None) -> None:
        self.instance = instance_signature or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        runtime = Path(runtime_dir or os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.socket_path = runtime / "hypr" / self.instance / ".socket2.sock"
        self.on_event = on_event
        self.started_at = time.monotonic()
        self.last_focus_monotonic = self.started_at
        self.last_event: dict[str, str] | None = None
        self.event_counts: dict[str, int] = {}
        self._thread = None
        self._stop = False

    @property
    def focused_age_s(self) -> float:
        return max(0.0, time.monotonic() - self.last_focus_monotonic)

    @staticmethod
    def parse(line: str) -> tuple[str, str] | None:
        if ">>" not in line:
            return None
        event, payload = line.split(">>", 1)
        if not event:
            return None
        return event, payload

    def handle_line(self, line: str) -> None:
        parsed = self.parse(line)
        if parsed is None:
            return
        event, payload = parsed
        self.last_event = {"event": event, "payload": payload}
        self.event_counts[event] = self.event_counts.get(event, 0) + 1
        if event in self.FOCUS_EVENTS:
            self.last_focus_monotonic = time.monotonic()
        if self.on_event:
            self.on_event(event, payload)

    def start(self) -> bool:
        if not self.socket_path.exists() or self._thread is not None:
            return False
        import socket
        import threading

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.socket_path))
            sock.settimeout(0.5)
        except OSError:
            sock.close()
            return False

        def loop() -> None:
            buffer = ""
            while not self._stop:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buffer += chunk.decode("utf-8", "replace")
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        self.handle_line(line)
                except TimeoutError:
                    continue
                except OSError:
                    break
            try:
                sock.close()
            except OSError:
                pass

        self._thread = threading.Thread(target=loop, daemon=True, name="hypr-socket2")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop = True


def capture_screen_stats(stats_only: bool = True) -> dict[str, float]:
    """Low-res grim frame -> a few local aggregate stats. Never saved unless OMNI_DEBUG_FRAMES=1."""
    if os.environ.get("OMNI_SCREENSHOT", "0") not in {"1", "true", "yes"}:
        return {}
    if not shutil.which("grim"):
        return {}
    try:
        proc = subprocess.run(["grim", "-s", "0.08", "-t", "ppm", "-"], capture_output=True, timeout=3, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if proc.returncode != 0 or not proc.stdout:
        return {}
    data = proc.stdout
    match = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not match:
        return {}
    width, height, maxval = (int(x) for x in match.groups())
    offset = match.end()
    expected = width * height * 3
    if maxval != 255 or len(data) < offset + expected:
        return {}
    pixels = np.frombuffer(data[offset : offset + expected], dtype=np.uint8).reshape(height, width, 3).astype(np.float32) / 255.0
    if os.environ.get("OMNI_DEBUG_FRAMES", "0") in {"1", "true", "yes"}:
        debug = Path(os.environ.get("OMNI_DEBUG_FRAME_DIR", Path.cwd() / "workspace" / "debug_frames"))
        debug.mkdir(parents=True, exist_ok=True)
        (debug / f"frame_{int(time.time() * 1000)}.ppm").write_bytes(data)
    gray = pixels.mean(axis=2)
    return {
        "width": float(width),
        "height": float(height),
        "brightness": float(gray.mean()),
        "edge_density": float((np.abs(np.diff(gray, axis=0)).mean() + np.abs(np.diff(gray, axis=1)).mean()) / 2.0),
    }


class HyprlandContextCollector:
    """Hyprland IPC context collector with privacy-preserving defaults."""

    def __init__(
        self,
        runner: Runner | None = None,
        event_stream: HyprlandEventStream | None = None,
        atspi: AtspiCollector | None = None,
        screenshot: bool | None = None,
    ) -> None:
        self.runner = runner or _default_runner
        self.events = event_stream or HyprlandEventStream()
        self.atspi = atspi if atspi is not None else AtspiCollector(runner=self.runner)
        self.screenshot = screenshot if screenshot is not None else os.environ.get("OMNI_SCREENSHOT", "0") in {"1", "true", "yes"}
        self._started = False

    def start(self) -> bool:
        if self._started:
            return True
        self._started = self.events.start()
        return self._started

    def close(self) -> None:
        self.events.stop()

    def _json(self, *args: str) -> Any:
        try:
            proc = self.runner(["hyprctl", *args, "-j"], 3.0)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode != 0:
            return None
        try:
            return json.loads(proc.stdout or "null")
        except json.JSONDecodeError:
            return None

    def _clipboard_types(self) -> tuple[str, ...]:
        if not shutil.which("wl-paste"):
            return ()
        try:
            proc = self.runner(["wl-paste", "--list-types"], 2.0)
        except (subprocess.TimeoutExpired, OSError):
            return ()
        if proc.returncode != 0:
            return ()
        return tuple(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())[:16]

    def snapshot(self) -> ContextSnapshot:
        active = self._json("activewindow") or {}
        workspace = self._json("activeworkspace") or {}
        clients = self._json("clients") or []
        monitors = self._json("monitors") or []
        atspi = self.atspi.collect() if self.atspi else {"available": False, "roles": {}}
        screen = capture_screen_stats() if self.screenshot else {}
        event = self.events.last_event or {}
        return ContextSnapshot(
            timestamp=time.time(),
            app_class=str(active.get("class") or active.get("initialClass") or "unknown"),
            title=str(active.get("title") or ""),
            workspace_id=int(workspace.get("id") or active.get("workspace", {}).get("id") or 0),
            workspace_name=str(workspace.get("name") or ""),
            client_count=len(clients) if isinstance(clients, list) else 0,
            monitor_count=len(monitors) if isinstance(monitors, list) else 0,
            focused_age_s=self.events.focused_age_s,
            clipboard_types=self._clipboard_types(),
            atspi_roles=dict(atspi.get("roles", {})),
            atspi_available=bool(atspi.get("available", False)),
            screenshot=screen,
            recent_events=(event.get("event", ""),) if event else (),
        )
