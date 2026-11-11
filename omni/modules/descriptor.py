from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from omni.context.features import ContextSnapshot
from omni.modules.units import UNITS


@dataclass(frozen=True)
class AppDescriptor:
    app_class: str
    title: str = ""
    atspi_roles: dict[str, int] = field(default_factory=dict)
    recent_actions: dict[str, int] = field(default_factory=dict)
    clipboard_types: tuple[str, ...] = ()
    workspace_id: int = 0

    @classmethod
    def from_snapshot(cls, snapshot: ContextSnapshot, recent_actions: dict[str, int] | None = None) -> "AppDescriptor":
        return cls(
            app_class=snapshot.app_class,
            title=snapshot.title,
            atspi_roles=dict(snapshot.atspi_roles),
            recent_actions=dict(recent_actions or {}),
            clipboard_types=tuple(snapshot.clipboard_types),
            workspace_id=snapshot.workspace_id,
        )


# Hand-authored priors for common classes. Unknown classes deliberately fall
# through to element-type/action statistics, so no app needs hand configuration.
_APP_PRIORS: tuple[tuple[tuple[str, ...], dict[str, float]], ...] = (
    (("code", "vscode", "codium"), {"code-edit": 1.0, "terminal": 0.55, "git": 0.50, "test-run": 0.45, "file-edit": 0.35}),
    (("chrome", "chromium", "firefox", "brave", "edge"), {"browser-nav": 1.0, "forms": 0.55, "download": 0.45, "search": 0.45, "vision": 0.20}),
    (("alacritty", "kitty", "foot", "konsole", "wezterm", "terminal"), {"terminal": 1.0, "test-run": 0.55, "git": 0.45, "file-edit": 0.30}),
    (("nautilus", "thunar", "dolphin", "nemo", "pcmanfm"), {"file-edit": 0.95, "file-search": 0.85, "clipboard": 0.35}),
    (("libreoffice-writer", "gedit", "obsidian", "notion"), {"doc-edit": 1.0, "file-edit": 0.50, "clipboard": 0.35}),
    (("libreoffice-calc", "gnumeric", "excel"), {"data-table": 0.90, "doc-edit": 0.45, "clipboard": 0.30}),
    (("mpv", "vlc", "imv", "eog", "gimp"), {"vision": 0.80, "window-control": 0.35}),
)

_ROLE_UNITS: tuple[tuple[str, dict[str, float]], ...] = (
    ("document web", {"browser-nav": 0.9}),
    ("link", {"browser-nav": 0.6}),
    ("page tab", {"browser-nav": 0.8}),
    ("text", {"doc-edit": 0.6}),
    ("paragraph", {"doc-edit": 0.7}),
    ("entry", {"forms": 0.8}),
    ("text field", {"forms": 0.9}),
    ("combo box", {"forms": 0.6}),
    ("push button", {"window-control": 0.7}),
    ("toggle", {"window-control": 0.5}),
    ("menu", {"window-control": 0.5}),
    ("terminal", {"terminal": 1.0}),
    ("table", {"data-table": 0.9}),
    ("grid", {"data-table": 0.8}),
    ("image", {"vision": 0.7}),
    ("canvas", {"vision": 0.6}),
    ("list item", {"file-search": 0.5}),
    ("scroll bar", {"window-control": 0.3}),
)

_ACTION_UNITS: tuple[tuple[str, dict[str, float]], ...] = (
    ("write_file", {"file-edit": 1.0}),
    ("create_file", {"file-edit": 1.0}),
    ("read_file", {"file-search": 0.6}),
    ("list_files", {"file-search": 0.8}),
    ("run_tests", {"test-run": 1.0}),
    ("test", {"test-run": 0.8}),
    ("run_python", {"terminal": 0.9}),
    ("shell", {"terminal": 1.0}),
    ("git", {"git": 1.0}),
    ("navigate", {"browser-nav": 1.0}),
    ("open_url", {"browser-nav": 1.0}),
    ("fill_form", {"forms": 1.0}),
    ("download", {"download": 1.0}),
    ("search", {"search": 1.0}),
    ("screenshot", {"vision": 0.8}),
    ("click", {"vision": 0.5, "window-control": 0.5}),
    ("edit_document", {"doc-edit": 1.0}),
    ("plan", {"planning": 1.0}),
)

_TITLE_UNITS: tuple[tuple[str, dict[str, float]], ...] = (
    ("terminal", {"terminal": 0.8}),
    ("shell", {"terminal": 0.7}),
    ("bash", {"terminal": 0.5}),
    ("test", {"test-run": 0.6}),
    ("pytest", {"test-run": 0.9}),
    ("git", {"git": 0.7}),
    ("commit", {"git": 0.6}),
    ("diff", {"git": 0.5}),
    (".py", {"code-edit": 0.7}),
    (".rs", {"code-edit": 0.7}),
    (".ts", {"code-edit": 0.6}),
    ("readme", {"doc-edit": 0.7}),
    (".md", {"doc-edit": 0.7}),
    (".txt", {"doc-edit": 0.6}),
    ("http", {"browser-nav": 0.7}),
    ("localhost", {"browser-nav": 0.6}),
    ("invoice", {"data-table": 0.6}),
    ("spreadsheet", {"data-table": 0.8}),
)


def _add(demand: dict[str, float], units: dict[str, float], scale: float = 1.0) -> None:
    for unit, weight in units.items():
        if unit in UNITS:
            demand[unit] = demand.get(unit, 0.0) + float(weight) * scale


def infer_unit_demand(descriptor: AppDescriptor) -> tuple[dict[str, float], str]:
    """Infer capability-unit demand from app class, AT-SPI roles, actions, title."""
    demand: dict[str, float] = {}
    app = (descriptor.app_class or "").lower()
    known = False
    for patterns, units in _APP_PRIORS:
        if any(pattern in app for pattern in patterns):
            _add(demand, units, 1.0)
            known = True
            break

    total_roles = sum(max(c, 0) for c in descriptor.atspi_roles.values())
    for role, count in descriptor.atspi_roles.items():
        role_l = role.lower()
        share = (max(count, 0) / total_roles) if total_roles else 0.0
        for pattern, units in _ROLE_UNITS:
            if pattern in role_l:
                _add(demand, units, min(1.0, share * 3.0))
                break

    for action, count in descriptor.recent_actions.items():
        action_l = action.lower()
        scale = min(1.0, math.log1p(max(count, 0)) / 2.0)
        for pattern, units in _ACTION_UNITS:
            if pattern in action_l:
                _add(demand, units, scale)
                break

    title_l = (descriptor.title or "").lower()
    for pattern, units in _TITLE_UNITS:
        if pattern in title_l:
            _add(demand, units, 0.5)

    if any("text" in t.lower() for t in descriptor.clipboard_types):
        _add(demand, {"clipboard": 0.4, "doc-edit": 0.2})
    if any("image" in t.lower() for t in descriptor.clipboard_types):
        _add(demand, {"vision": 0.4})

    if not demand:
        demand = {"window-control": 0.5, "planning": 0.2}
        source = "generic"
    elif known:
        source = "app+roles"
    else:
        source = "roles"

    demand = {unit: round(min(weight, 1.0), 4) for unit, weight in demand.items() if weight > 0}
    return dict(sorted(demand.items(), key=lambda item: item[1], reverse=True)), source
