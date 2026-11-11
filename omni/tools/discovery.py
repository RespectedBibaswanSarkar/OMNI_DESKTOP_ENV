from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path(os.path.expanduser("~/.local/share/applications")),
    Path(os.path.expanduser("~/.config/autostart")),
]

# Destructive / high-risk operations that always require explicit confirmation.
DESTRUCTIVE_KEYWORDS = (
    "rm", "delete", "trash", "uninstall", "erase", "wipe", "format",
    "send", "pay", "purchase", "install", "upgrade", "sudo", "su",
    "kill", "pkill", "reboot", "poweroff", "shutdown", "post", "deploy",
)


@dataclass(frozen=True)
class ToolSpec:
    id: str                          # stable: "<source>:<name>"
    name: str
    kind: str                        # desktop | executable | dbus | atspi | hyprland | file | mcp | browser
    category: str                    # documents | code | web | system | media | files | communication | general
    description: str = ""
    params: dict[str, Any] = field(default_factory=dict)   # JSON schema-ish input schema
    executable: str | None = None
    source: str = ""
    safety_tier: str = "read-only"   # read-only | reversible | destructive
    requires_confirmation: bool = False
    available: bool = True
    discovered_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @property
    def destructive(self) -> bool:
        return self.safety_tier == "destructive" or self.requires_confirmation


def _tier(name: str, description: str) -> str:
    blob = f"{name} {description}".lower()
    if any(k in blob for k in DESTRUCTIVE_KEYWORDS):
        return "destructive"
    return "read-only"


def _categorize(name: str, description: str, exe: str | None) -> str:
    blob = f"{name} {description} {exe or ''}".lower()
    tables = {
        "code": ("code", "git", "vim", "nvim", "python", "node", "rustc", "cargo", "gcc", "make", "docker", "podman"),
        "web": ("firefox", "chrome", "chromium", "browser", "curl", "wget", "lynx"),
        "communication": ("thunderbird", "mail", "signal", "telegram", "slack", "discord", "matrix", "irc"),
        "media": ("mpv", "vlc", "gimp", "inkscape", "audacity", "obs", "ffmpeg", "image", "video", "audio"),
        "files": ("nautilus", "dolphin", "thunar", "pcmanfm", "caja", "explorer", "files"),
        "system": ("hyprctl", "systemctl", "nmcli", "pacman", "apt", "dnf", "grim", "slurp", "wtype", "ydotool"),
        "documents": ("libreoffice", "writer", "calc", "impress", "okular", "evince", "pdf", "docx", "odt"),
    }
    for category, keys in tables.items():
        if any(k in blob for k in keys):
            return category
    return "general"


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.stdout
    except Exception:
        return ""


class ToolRegistry:
    """Typed tool registry assembled from runtime discovery. Refreshed periodically."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.tools: dict[str, ToolSpec] = {}
        self.last_refresh: float = 0.0

    def refresh(self, force: bool = False) -> dict[str, int]:
        now = time.time()
        if not force and (now - self.last_refresh) < self.ttl_seconds and self.tools:
            return {k: 1 for k in self.tools}
        discovered = discover_all()
        self.tools = {t.id: t for t in discovered}
        self.last_refresh = now
        return {source: sum(1 for t in discovered if t.kind == source) for source in
                {"desktop", "executable", "dbus", "atspi", "hyprland", "file", "mcp", "browser"}}

    def __iter__(self):
        return iter(self.tools.values())

    def get(self, tool_id: str) -> ToolSpec | None:
        return self.tools.get(tool_id)

    def by_category(self, category: str) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.category == category]

    def destructive(self) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.destructive]

    def as_dict(self) -> dict[str, Any]:
        return {tool_id: t.as_dict() for tool_id, t in self.tools.items()}


# ----- discovery sources -----


def _tool(tool_id, name, kind, category, description, executable=None, source="", tier="read-only",
          confirm=False, available=True, params=None):
    return ToolSpec(
        id=tool_id,
        name=name,
        kind=kind,
        category=category,
        description=description,
        executable=executable,
        source=source,
        params=params or {"type": "object"},
        safety_tier=tier,
        requires_confirmation=confirm,
        available=available,
    )


def _desktop_entries() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    for ddir in DESKTOP_DIRS:
        if not ddir.exists():
            continue
        for desktop in sorted(ddir.glob("*.desktop")):
            try:
                text = desktop.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            entry: dict[str, str] = {}
            current = ""
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current = line[1:-1]
                    continue
                if "=" in line and current == "Desktop Entry":
                    key, _, value = line.partition("=")
                    entry[key.strip()] = value.strip()
            if entry.get("NoDisplay", "false").lower() == "true":
                continue
            name = entry.get("Name") or desktop.stem
            exec_cmd = entry.get("Exec", "")
            exe = exec_cmd.split()[0] if exec_cmd else ""
            comment = entry.get("Comment", "")
            tools.append(
                _tool(
                    tool_id=f"desktop:{desktop.stem}",
                    name=name,
                    kind="desktop",
                    category=_categorize(name, comment, exe),
                    description=comment,
                    executable=exe,
                    source=str(desktop),
                    tier=_tier(name, comment),
                    confirm=_tier(name, comment) == "destructive",
                    available=shutil.which(exe) is not None if exe else True,
                    params={"type": "object", "properties": {"desktop_file": {"type": "string", "default": str(desktop)}}},
                )
            )
    return tools


def _path_executables(limit: int = 200) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    seen: set[str] = set()
    path_dirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    count = 0
    for ddir in path_dirs:
        pdir = Path(ddir)
        if not pdir.is_dir():
            continue
        for entry in sorted(pdir.iterdir()):
            if count >= limit:
                break
            if not entry.is_file() or not os.access(entry, os.X_OK):
                continue
            name = entry.name
            if name in seen:
                continue
            seen.add(name)
            count += 1
            help_text = _run([str(entry), "--help"], timeout=2.0)
            desc = ""
            for line in help_text.splitlines()[:5]:
                if line.strip():
                    desc = line.strip()
                    break
            tools.append(
                _tool(
                    tool_id=f"executable:{name}",
                    name=name,
                    kind="executable",
                    category=_categorize(name, desc, str(entry)),
                    description=desc,
                    executable=str(entry),
                    source="PATH",
                    tier=_tier(name, desc),
                    confirm=_tier(name, desc) == "destructive",
                    params={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}},
                )
            )
        if count >= limit:
            break
    return tools


def _dbus_services() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    if not shutil.which("dbus-send") and not shutil.which("busctl"):
        return tools
    for bus in ("session", "system"):
        out = _run(["busctl", "--" + bus, "list"], timeout=5.0) if shutil.which("busctl") else ""
        for line in out.splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            if name.startswith(":") or "." not in name:
                continue
            category = "communication"
            if name.startswith("org.mpris"):
                category = "media"
            elif name.startswith("org.freedesktop.Notifications"):
                category = "communication"
            elif name.startswith("org.freedesktop.NetworkManager") or name.startswith("org.freedesktop.systemd1"):
                category = "system"
            tier = "reversible" if name.startswith(("org.freedesktop.systemd1", "org.freedesktop.NetworkManager")) else "read-only"
            tools.append(
                _tool(
                    tool_id=f"dbus:{bus}:{name}",
                    name=name,
                    kind="dbus",
                    category=category,
                    description=f"D-Bus {bus} service {name}",
                    source=f"dbus {bus}",
                    tier=tier,
                    params={"type": "object", "properties": {"bus": {"type": "string"}, "destination": {"type": "string"}, "path": {"type": "string"}, "interface": {"type": "string"}, "member": {"type": "string"}}},
                )
            )
    return tools


def _atspi_apps() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    if not shutil.which("gdbus"):
        return tools
    out = _run(
        ["gdbus", "introspect", "--session", "--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus"],
        timeout=5.0,
    )
    if "RegisterApplication" not in out:
        return tools
    tools.append(
        _tool(
            tool_id="atspi:bus",
            name="AT-SPI bus",
            kind="atspi",
            category="general",
            description="AT-SPI accessibility tree (gdbus fallback; no PyAT-SPI)",
            source="atspi",
            params={"type": "object", "properties": {"application": {"type": "string"}}},
        )
    )
    return tools


def _hyprland_tools() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    if not shutil.which("hyprctl"):
        return tools
    for action, desc in (
        ("activewindow", "focus active window"),
        ("clients", "list all windows"),
        ("workspaces", "list workspaces"),
        ("dispatch", "dispatch a Hyprland command"),
    ):
        tools.append(
            _tool(
                tool_id=f"hyprland:{action}",
                name=f"hyprctl {action}",
                kind="hyprland",
                category="system",
                description=desc,
                source="hyprland ipc",
                tier="read-only" if action in ("activewindow", "clients", "workspaces") else "reversible",
                confirm=action == "dispatch",
                params={"type": "object", "properties": {"command": {"type": "string"}}},
            )
        )
    return tools


def _file_tools() -> list[ToolSpec]:
    return [
        _tool(
            tool_id="file:read", name="read file", kind="file", category="files", description="read a text file",
            source="builtin",
            params={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        ),
        _tool(
            tool_id="file:write", name="write file", kind="file", category="files", description="write a text file",
            source="builtin",
            params={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            tier="reversible",
        ),
        _tool(
            tool_id="file:delete", name="delete file", kind="file", category="files", description="delete a file (destructive)",
            source="builtin",
            params={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            tier="destructive",
            confirm=True,
        ),
    ]


def _mcp_servers() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    config = Path(os.path.expanduser("~/.config/omni/mcp.json"))
    if not config.exists():
        return tools
    try:
        servers = json.loads(config.read_text(encoding="utf-8")).get("servers", {})
    except Exception:
        return tools
    for name, spec in servers.items():
        tools.append(
            _tool(
                tool_id=f"mcp:{name}", name=name, kind="mcp", category="general",
                description=str(spec.get("description", "MCP server " + name)),
                source="mcp config",
                tier="reversible",
                available=bool(spec.get("command")),
                params={"type": "object", "properties": {"server": {"type": "string", "default": name}, "method": {"type": "string"}, "params": {"type": "object"}}},
            )
        )
    return tools


def _browser_tools() -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    if shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("firefox"):
        tools.append(
            _tool(
                tool_id="browser:cdp", name="browser CDP/Playwright", kind="browser", category="web",
                description="browser automation via Chrome DevTools Protocol / Playwright",
                source="builtin",
                tier="reversible",
                params={"type": "object", "properties": {"url": {"type": "string"}, "action": {"type": "string"}}},
            )
        )
    return tools


def discover_all() -> list[ToolSpec]:
    """Discover every runtime capability source. Each source degrades gracefully."""
    all_tools: list[ToolSpec] = []
    for collector in (
        _desktop_entries,
        _path_executables,
        _dbus_services,
        _atspi_apps,
        _hyprland_tools,
        _file_tools,
        _mcp_servers,
        _browser_tools,
    ):
        try:
            all_tools.extend(collector())
        except Exception:
            continue
    seen: set[str] = set()
    ordered: list[ToolSpec] = []
    for t in all_tools:
        if t.id in seen:
            continue
        seen.add(t.id)
        ordered.append(t)
    return ordered
