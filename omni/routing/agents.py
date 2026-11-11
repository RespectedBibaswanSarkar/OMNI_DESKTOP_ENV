from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    # Design-time constants (not measurements): used only as a routing penalty.
    cost: float = 0.10


# Capability descriptions are the only input to the prior embedding. Users never
# select an agent; IARS explores and learns from implicit feedback.
DEFAULT_AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="file-ops",
        name="File Operator",
        description="Create, rename, move, copy, delete, list and inspect files and directories on disk. Work with paths, archives, permissions and workspace cleanup.",
        tags=("files", "file", "filesystem", "paths", "workspace", "list", "nautilus", "thunar", "dolphin", "nemo", "pcmanfm"),
        cost=0.05,
    ),
    AgentSpec(
        id="document-editor",
        name="Document Editor",
        description="Read and edit text documents, notes and markdown. Insert, replace and reflow paragraphs while preserving structure and avoiding unrelated edits.",
        tags=("document", "editor", "text", "markdown", "writing", "writer", "gedit", "obsidian", "notion", "readme", "libreoffice", "pdf"),
        cost=0.12,
    ),
    AgentSpec(
        id="dev-terminal",
        name="Terminal Developer",
        description="Run terminal commands, inspect processes, build and test code, use git and package managers, and debug command-line failures in a development workspace.",
        tags=("terminal", "shell", "bash", "zsh", "alacritty", "kitty", "foot", "konsole", "wezterm", "code", "vscode", "codium", "build", "test", "pytest", "git", "python", "routing", "py"),
        cost=0.18,
    ),
    AgentSpec(
        id="browser-ops",
        name="Browser Operator",
        description="Inspect and change browser state: tabs, navigation, page text, forms and downloads. Verify URL or page content after interacting with a web application.",
        tags=("browser", "chrome", "chromium", "firefox", "brave", "edge", "web", "url", "tabs", "forms", "form", "link", "page", "inbox", "mail", "youtube", "video"),
        cost=0.22,
    ),
    AgentSpec(
        id="visual-ui",
        name="Visual UI Agent",
        description="Understand what is visible on screen from low-resolution frames and window geometry. Click, scroll and verify visual layout when text and accessibility data are insufficient.",
        tags=("screen", "screenshot", "visual", "image", "ui", "geometry", "button", "window", "mpv", "vlc", "imv", "eog", "gimp", "video"),
        cost=0.30,
    ),
    AgentSpec(
        id="planner",
        name="Multi-step Planner",
        description="Decompose an ambiguous cross-app goal into ordered steps, choose tools and hand off sub-tasks. Useful for multi-step workflows and long-running requests.",
        tags=("plan", "workflow", "cross", "cross-app", "reasoning", "handoff", "multi", "goal", "steps", "tools"),
        cost=0.25,
    ),
)
