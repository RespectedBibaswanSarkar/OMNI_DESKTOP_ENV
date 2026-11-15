from __future__ import annotations

from omni.context.gnome import GnomeContextCollector
from omni.context.hyprland import HyprlandContextCollector
from omni.desktop.backends import detect_backend


def make_collector(backend=None):  # noqa: ANN001
    """GNOME session -> bridge-fed collector; otherwise the Hyprland collector."""
    backend = backend if backend is not None else detect_backend()
    return GnomeContextCollector(backend) if backend.name == "gnome" else HyprlandContextCollector()
