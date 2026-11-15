"""Context collector for GNOME/Mutter: same ContextSnapshot, fed by the OMNI Bridge extension."""
from __future__ import annotations

import time

from omni.context.hyprland import ContextSnapshot


class GnomeContextCollector:
    def __init__(self, backend) -> None:  # noqa: ANN001 - DesktopBackend
        self.backend = backend
        self._last_id: str | None = None
        self._since = time.time()

    def snapshot(self) -> ContextSnapshot:
        active = self.backend.active_window() or {}
        rows = self.backend.clients()
        ident = active.get("address")
        if ident != self._last_id:
            self._last_id, self._since = ident, time.time()
        return ContextSnapshot(
            timestamp=time.time(),
            app_class=str(active.get("class") or "unknown"),
            title=str(active.get("title") or ""),
            workspace_id=int(self.backend.active_workspace() or 0),
            workspace_name="",
            client_count=len(rows),
            monitor_count=1,
            focused_age_s=max(0.0, time.time() - self._since),
            clipboard_types=(),
            atspi_roles={},
            atspi_available=False,
            screenshot={},
            recent_events=(),
        )
