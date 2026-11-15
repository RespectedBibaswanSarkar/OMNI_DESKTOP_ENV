from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from omni.desktop.backends.base import DesktopBackend, Row


class HyprlandBackend(DesktopBackend):
    name = "hyprland"

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess], which: Callable[[str], str | None]) -> None:
        self.runner = runner
        self.which = which

    def available(self) -> bool:
        return self.which("hyprctl") is not None

    def _json(self, *args: str) -> Any:
        exe = self.which("hyprctl")
        if exe is None:
            return None
        try:
            res = self.runner([exe, *args, "-j"], capture_output=True, text=True, timeout=5, check=False)
            return json.loads(res.stdout or "null")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None

    @staticmethod
    def _row(c: dict) -> Row:
        ws = c.get("workspace") or {}
        return {"address": str(c.get("address", "")), "class": str(c.get("class") or c.get("initialClass") or ""),
                "title": str(c.get("title") or ""), "workspace": ws.get("id") if isinstance(ws, dict) else None,
                "pid": c.get("pid"), "focus": int(c.get("focusHistoryID", 99) or 0)}

    def clients(self) -> list[Row]:
        data = self._json("clients")
        return [self._row(c) for c in data if isinstance(c, dict) and c.get("mapped", True)] if isinstance(data, list) else []

    def active_window(self) -> Row | None:
        data = self._json("activewindow")
        return self._row(data) if isinstance(data, dict) and data.get("address") else None

    def active_workspace(self) -> int | None:
        data = self._json("activeworkspace")
        return data.get("id") if isinstance(data, dict) else None

    def _dispatch(self, *args: str) -> tuple[bool, str]:
        exe = self.which("hyprctl")
        if exe is None:
            return False, "Required executable not found: hyprctl"
        try:
            res = self.runner([exe, "dispatch", *args], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        return res.returncode == 0, (res.stdout or res.stderr or "ok").strip()[:200]

    def focus(self, address: str) -> tuple[bool, str]:
        return self._dispatch("focuswindow", f"address:{address}")

    def close(self, address: str) -> tuple[bool, str]:
        return self._dispatch("closewindow", f"address:{address}")

    def move(self, address: str, workspace: int) -> tuple[bool, str]:
        return self._dispatch("movetoworkspacesilent", f"{workspace},address:{address}")

    def switch_workspace(self, workspace: int) -> tuple[bool, str]:
        return self._dispatch("workspace", str(workspace))
