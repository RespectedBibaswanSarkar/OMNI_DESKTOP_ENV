from __future__ import annotations

from typing import Any

Row = dict[str, Any]   # {"address": str, "class": str, "title": str, "workspace": int|None(1-based), "pid": int|None, "focus": int}


class DesktopBackend:
    """Window/workspace control surface the action registry depends on."""

    name = "base"

    def available(self) -> bool:
        return False

    def clients(self) -> list[Row]:
        return []

    def active_window(self) -> Row | None:
        return None

    def active_workspace(self) -> int | None:
        return None

    def focus(self, address: str) -> tuple[bool, str]:
        return False, f"{self.name}: focus not supported"

    def close(self, address: str) -> tuple[bool, str]:
        return False, f"{self.name}: close not supported"

    def move(self, address: str, workspace: int) -> tuple[bool, str]:
        return False, f"{self.name}: move not supported"

    def switch_workspace(self, workspace: int) -> tuple[bool, str]:
        return False, f"{self.name}: workspace switching not supported"

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "available": self.available()}


class NullBackend(DesktopBackend):
    name = "none"
