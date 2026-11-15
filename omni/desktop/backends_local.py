"""Window-system backends for GNOME (X11 and Wayland). Each returns Hyprland-shaped dicts
and *argv lists*, so DesktopActionRegistry keeps running everything through its own
injectable runner (no shell, fully testable)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Callable


class DesktopBackend:
    name = "base"

    def __init__(self, runner: Callable[..., Any] | None = None,
                 which: Callable[[str], str | None] | None = None) -> None:
        self.runner = runner or subprocess.run
        self._which = which or shutil.which

    def _out(self, cmd: list[str], timeout: float = 5.0) -> str:
        exe = self._which(cmd[0])
        if exe is None:
            return ""
        try:
            res = self.runner([exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return res.stdout or ""

    # --- queries (Hyprland-shaped rows) ---
    def clients(self) -> list[dict]: return []
    def active_window(self) -> dict: return {}
    def active_workspace(self) -> int | None: return None   # 1-based; None = unknown

    # --- commands (argv lists) ---
    def focus_cmd(self, address: str) -> list[str]: raise NotImplementedError
    def close_cmd(self, address: str) -> list[str]: raise NotImplementedError
    def move_cmd(self, address: str, workspace: int) -> list[str]: raise NotImplementedError
    def minimize_cmd(self, address: str) -> list[str]: raise NotImplementedError
    def workspace_cmd(self, workspace: int) -> list[str]: raise NotImplementedError
    def type_cmd(self, text: str) -> list[str]: raise NotImplementedError
    def key_cmd(self, mods: list[str], base: str) -> list[str]: raise NotImplementedError


class X11Backend(DesktopBackend):
    """Ubuntu on Xorg: wmctrl + xdotool. Workspaces are 0-based in X, 1-based in OMNI."""
    name = "x11"

    def _active_id(self) -> str | None:
        out = self._out(["xdotool", "getactivewindow"]).strip()
        return f"0x{int(out):08x}" if out.isdigit() else None

    def clients(self) -> list[dict]:
        active = self._active_id()
        rows: list[dict] = []
        # wmctrl -lpx:  <id> <desktop> <pid> <instance.Class> <host> <title...>
        for line in self._out(["wmctrl", "-lpx"]).splitlines():
            parts = line.split(None, 5)
            if len(parts) < 5:
                continue
            addr, desk, pid, wmclass = parts[:4]
            title = parts[5] if len(parts) > 5 else ""
            try:
                ws = int(desk) + 1
            except ValueError:
                continue
            if ws < 1:                      # -1 = sticky/desktop/panel windows
                continue
            cls = wmclass.rsplit(".", 1)[-1]
            rows.append({"address": addr, "class": cls, "initialClass": cls, "title": title,
                         "workspace": {"id": ws}, "mapped": True,
                         "focusHistoryID": 0 if addr.lower() == active else 1,
                         "pid": int(pid) if pid.isdigit() else None})
        return rows

    def active_window(self) -> dict:
        active = self._active_id()
        return next((c for c in self.clients() if c["address"].lower() == active), {}) if active else {}

    def active_workspace(self) -> int | None:
        out = self._out(["xdotool", "get_desktop"]).strip()
        return int(out) + 1 if out.isdigit() else None

    def focus_cmd(self, address): return ["wmctrl", "-i", "-a", address]
    def close_cmd(self, address): return ["wmctrl", "-i", "-c", address]
    def move_cmd(self, address, workspace): return ["wmctrl", "-i", "-r", address, "-t", str(workspace - 1)]
    def minimize_cmd(self, address): return ["xdotool", "windowminimize", str(int(address, 16))]
    def workspace_cmd(self, workspace): return ["wmctrl", "-s", str(workspace - 1)]
    def type_cmd(self, text): return ["xdotool", "type", "--delay", "15", "--", text]
    def key_cmd(self, mods, base): return ["xdotool", "key", "--", "+".join([*mods, base])]


class GnomeShellBackend(DesktopBackend):
    """Wayland: Window Calls extension over D-Bus. Window ids are decimal there; OMNI's address
    validator wants 0x-hex, so we convert both ways."""
    name = "gnome-shell"
    _BASE = ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", "/org/gnome/Shell/Extensions/Windows", "--method"]

    @classmethod
    def _wc(cls, method: str, *args: Any) -> list[str]:
        return [*cls._BASE, f"org.gnome.Shell.Extensions.Windows.{method}", *map(str, args)]

    @staticmethod
    def _unwrap(out: str) -> str | None:
        m = re.match(r"^\(\s*'(.*)'\s*,\s*\)$", out.strip(), re.S)
        return re.sub(r"\\(['\\])", r"\1", m.group(1)) if m else None

    def clients(self) -> list[dict]:
        raw = self._unwrap(self._out(self._wc("List")))
        try:
            data = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            return []
        rows = []
        for w in data if isinstance(data, list) else []:
            if w.get("window_type", 0) != 0:
                continue
            ws = w.get("workspace")                         # present only in some extension versions
            rows.append({"address": f"0x{int(w['id']):x}", "class": str(w.get("wm_class") or ""),
                         "initialClass": str(w.get("wm_class_instance") or ""),
                         "title": str(w.get("title") or ""),
                         "workspace": {"id": ws + 1 if isinstance(ws, int) else None},
                         "mapped": True, "focusHistoryID": 0 if w.get("focus") else 1, "pid": w.get("pid")})
        return rows

    def active_window(self) -> dict:
        return next((c for c in self.clients() if c["focusHistoryID"] == 0), {})

    def active_workspace(self) -> int | None:
        return None        # Mutter exposes no query; verification is skipped for workspace.switch

    @staticmethod
    def _id(address: str) -> int: return int(address, 16)

    def focus_cmd(self, address): return self._wc("Activate", self._id(address))
    def close_cmd(self, address): return self._wc("Close", self._id(address))
    def move_cmd(self, address, workspace): return self._wc("MoveToWorkspace", self._id(address), workspace - 1)
    def minimize_cmd(self, address): return self._wc("Minimize", self._id(address))

    def workspace_cmd(self, workspace):
        # needs install.sh: it binds <Super><Alt>N to switch-to-workspace-N; ydotool presses it.
        code = 2 + workspace - 1 if workspace < 10 else 11       # KEY_1..KEY_9, KEY_0
        return ["ydotool", "key", "125:1", "56:1", f"{code}:1", f"{code}:0", "56:0", "125:0"]

    def type_cmd(self, text): return ["ydotool", "type", "--", text]

    def key_cmd(self, mods, base):
        raise ValueError("key.press is not supported on the Wayland backend; use an Xorg session.")


def make_backend(runner=None, which=None) -> DesktopBackend:
    forced = os.environ.get("OMNI_DESKTOP_BACKEND", "").lower()
    if forced == "x11":
        return X11Backend(runner, which)
    if forced in {"gnome-shell", "wayland"}:
        return GnomeShellBackend(runner, which)
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    # systemd --user may not export XDG_SESSION_TYPE; WAYLAND_DISPLAY is only set on Wayland sessions
    wayland = session == "wayland" or (not session and bool(os.environ.get("WAYLAND_DISPLAY")))
    return (GnomeShellBackend if wayland else X11Backend)(runner, which)