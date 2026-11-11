from __future__ import annotations
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class DesktopAdapter:
    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()

    def inspect_desktop(self) -> dict[str, Any]:
        result: dict[str, Any] = {"desktop": "unknown", "hyprland": False}
        hyprctl = shutil.which("hyprctl")
        if hyprctl:
            try:
                monitors = subprocess.run([hyprctl, "monitors", "-j"], capture_output=True, text=True, timeout=10)
                if monitors.returncode == 0:
                    result["hyprland"] = True
                    result["desktop"] = "Hyprland"
                    try:
                        result["monitors"] = json.loads(monitors.stdout or "[]")
                    except json.JSONDecodeError:
                        result["monitors"] = []
            except Exception:
                result["hyprland"] = False
        else:
            result["desktop"] = "unsupported"
        return result

    def launch_application(self, app_name: str, args: list[str] | None = None) -> dict[str, Any]:
        if shutil.which(app_name) is None:
            return {"success": False, "error": f"Application not installed: {app_name}"}
        cmd = [app_name] + (args or [])
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return {"success": True, "pid": proc.pid, "command": cmd}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
