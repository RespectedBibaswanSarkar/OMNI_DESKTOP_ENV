"""Window-manager backends: one interface, GNOME/Mutter and Hyprland implementations."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable

from omni.desktop.backends.base import DesktopBackend, NullBackend
from omni.desktop.backends.gnome import GnomeBackend
from omni.desktop.backends.hyprland import HyprlandBackend

__all__ = ["DesktopBackend", "NullBackend", "GnomeBackend", "HyprlandBackend", "detect_backend"]


def detect_backend(
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    which: Callable[[str], str | None] | None = None,
    name: str | None = None,
) -> DesktopBackend:
    """Pick a backend from OMNI_BACKEND, else from the running session."""
    runner = runner or subprocess.run
    which = which or shutil.which
    choice = (name or os.environ.get("OMNI_BACKEND", "")).lower()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if choice == "hyprland" or (not choice and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")):
        return HyprlandBackend(runner=runner, which=which)
    if choice == "gnome" or (not choice and "gnome" in desktop):
        return GnomeBackend(runner=runner, which=which)
    return NullBackend()
