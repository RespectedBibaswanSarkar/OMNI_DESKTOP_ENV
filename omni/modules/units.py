from __future__ import annotations

# Capability units form the shared vocabulary between the application descriptor
# and module coverage maps. They are abstract capabilities, not model names.
UNITS: tuple[str, ...] = (
    "file-edit",
    "file-search",
    "clipboard",
    "doc-edit",
    "code-edit",
    "terminal",
    "test-run",
    "git",
    "browser-nav",
    "forms",
    "download",
    "search",
    "vision",
    "window-control",
    "planning",
    "cross-app",
    "data-table",
)
