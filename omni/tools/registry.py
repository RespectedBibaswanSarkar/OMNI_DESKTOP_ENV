"""Re-export for backwards-compatible import path used by bench/docs."""

from __future__ import annotations

from .discovery import ToolRegistry, ToolSpec, discover_all  # noqa: F401

__all__ = ["ToolRegistry", "ToolSpec", "discover_all"]
