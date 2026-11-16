"""GNOME Shell / Mutter backend (Wayland).

GNOME deliberately exposes no window list or focus API to ordinary clients, so OMNI
ships a tiny Shell extension (``gnome-extension/omni-bridge@omni.local``) that exports
``org.omni.Bridge`` on the session bus.  This class talks to it with ``busctl``
(JSON output, no GVariant parsing).
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from omni.desktop.backends.base import DesktopBackend, Row

DEST = "org.omni.Bridge"
PATH = "/org/omni/Bridge"


class GnomeBackend(DesktopBackend):
    name = "gnome"

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess], which: Callable[[str], str | None], tracker: Any = None) -> None:
        self.runner = runner
        self.which = which
        self.tracker = tracker           # AtspiTracker or None: read-only fallback when the bridge is off
        self.last_error = ""
        self._bridge_ok: bool | None = None

    # ------------------------------------------------------------ transport
    def _call(self, method: str, *args: str) -> Any:
        exe = self.which("busctl")
        if exe is None:
            self.last_error = "busctl not found"
            return None
        sig = "".join(a[0] for a in args)
        cmd = [exe, "--user", "--json=short", "call", DEST, PATH, DEST, method]
        if args:
            cmd += [sig, *[a[1:] for a in args]]
        try:
            res = self.runner(cmd, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = str(exc)
            return None
        if res.returncode != 0:
            self.last_error = (res.stderr or "bridge call failed").strip()[:200]
            return None
        try:
            data = json.loads(res.stdout or "null")["data"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            self.last_error = "unparseable bridge reply"
            return None
        self.last_error = ""
        return data

    def _json(self, method: str) -> Any:
        raw = self._call(method)
        if not isinstance(raw, str):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    # ------------------------------------------------------------ interface
    def bridge_up(self) -> bool:
        self._bridge_ok = self._call("Ping") is not None
        return self._bridge_ok

    def available(self) -> bool:
        return self.bridge_up()

    def layers(self) -> dict[str, bool]:
        return {"bridge": self.bridge_up(), "atspi": bool(self.tracker and self.tracker.available())}

    @staticmethod
    def _row(c: dict) -> Row:
        ws = c.get("workspace")
        return {"address": str(c.get("id", "")), "class": str(c.get("class") or c.get("app") or ""),
                "title": str(c.get("title") or ""), "workspace": (ws + 1) if isinstance(ws, int) and ws >= 0 else None,
                "pid": c.get("pid"), "focus": 0 if c.get("focused") else int(c.get("focus", 1) or 1)}

    def clients(self) -> list[Row]:
        data = self._json("ListWindows")
        if isinstance(data, list):
            return [self._row(c) for c in data if isinstance(c, dict)]
        if self.tracker is not None:          # bridge unreachable: accessibility tree still lists windows
            try:
                return self.tracker.windows()
            except Exception:  # noqa: BLE001
                return []
        return []

    def active_window(self) -> Row | None:
        data = self._json("ActiveWindow")
        if isinstance(data, dict):
            return self._row(data) if data.get("id") else None
        if self.tracker is not None:
            try:
                return next((w for w in self.tracker.windows() if w["focus"] == 0), None)
            except Exception:  # noqa: BLE001
                return None
        return None

    def active_workspace(self) -> int | None:
        value = self._call("ActiveWorkspace")
        return value + 1 if isinstance(value, int) else None

    def _bool(self, method: str, *args: str) -> tuple[bool, str]:
        value = self._call(method, *args)
        if value is None:
            return False, self.last_error or "OMNI Bridge extension not reachable (see docs/UBUNTU_GNOME_SETUP.md)"
        return bool(value), "ok" if value else f"{method} refused"

    def focus(self, address: str) -> tuple[bool, str]:
        return self._bool("Focus", f"t{address}")

    def close(self, address: str) -> tuple[bool, str]:
        return self._bool("Close", f"t{address}")

    def move(self, address: str, workspace: int) -> tuple[bool, str]:
        return self._bool("MoveToWorkspace", f"t{address}", f"i{workspace - 1}")

    def switch_workspace(self, workspace: int) -> tuple[bool, str]:
        return self._bool("SwitchWorkspace", f"i{workspace - 1}")

    def status(self) -> dict[str, Any]:
        layers = self.layers()
        err = "" if layers["bridge"] else (self.last_error or "bridge not reachable")
        hint = "" if layers["bridge"] else "Enable the OMNI Bridge extension (docs/UBUNTU_GNOME_SETUP.md) for focus/close/workspace control."
        return {"backend": self.name, "available": layers["bridge"] or layers["atspi"], "layers": layers, "error": err, "hint": hint}
