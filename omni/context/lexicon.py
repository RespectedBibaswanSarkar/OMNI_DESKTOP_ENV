from __future__ import annotations

# Curated lexical vocabulary for the deterministic local embedding. This is not
# a neural embedding: it gives exact, collision-free token alignment between app
# context and agent capability descriptions. Replace with LLMClient.embed when a
# real embedding model is configured.
LEXICON: tuple[str, ...] = (
    "file", "files", "filesystem", "paths", "workspace", "nautilus", "thunar", "dolphin", "nemo", "pcmanfm",
    "create", "rename", "copy", "move", "delete", "list", "inspect", "archive", "permissions", "cleanup",
    "document", "documents", "editor", "text", "markdown", "writing", "writer", "gedit", "obsidian", "notion",
    "readme", "insert", "replace", "paragraph", "structure", "libreoffice", "pdf",
    "terminal", "shell", "bash", "zsh", "alacritty", "kitty", "foot", "konsole", "wezterm",
    "code", "vscode", "codium", "build", "test", "pytest", "git", "python", "py", "command", "process",
    "debug", "compile", "repository", "routing", "rust", "typescript", "commit", "diff",
    "browser", "chrome", "chromium", "firefox", "brave", "edge", "web", "url", "tabs", "forms", "form",
    "navigation", "navigate", "page", "download", "tab", "inbox", "mail", "youtube", "video", "whatsapp",
    "screen", "screenshot", "visual", "image", "ui", "geometry", "mpv", "vlc", "imv", "eog", "gimp",
    "click", "scroll", "layout", "frame", "window", "application", "button", "entry", "field", "menu",
    "link", "table", "grid", "canvas", "item", "scrollbar", "toggle",
    "plan", "workflow", "cross", "cross-app", "reasoning", "handoff", "multi", "goal", "steps", "tools",
    "slack", "discord", "spotify", "spreadsheet", "calc", "excel", "gnumeric", "invoice",
)

LEXICON_INDEX: dict[str, int] = {token: index for index, token in enumerate(LEXICON)}
