from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from omni.desktop.apps import AppEntry, AppIndex
from omni.desktop.backends import DesktopBackend, detect_backend
from omni.desktop.tracker import AtspiTracker, ProcessTracker


@dataclass(frozen=True)
class ActionResult:
    action: str
    tier: str
    success: bool
    output: str = ""
    confirmation_required: bool = False
    confirmation_token: str | None = None
    data: Any = None


@dataclass(frozen=True)
class ActionSpec:
    tier: str
    confirmation: bool
    description: str
    args: str = ""          # argument signature shown to the planner model


# Linux input-event keycodes for ydotool (Wayland-compositor independent).
KEYCODES = {
    "Escape": 1, "Return": 28, "Tab": 15, "space": 57, "BackSpace": 14, "Delete": 111,
    "Up": 103, "Down": 108, "Left": 105, "Right": 106, "Home": 102, "End": 107,
    "Page_Up": 104, "Page_Down": 109, "F5": 63, "F11": 87,
    **{c: k for c, k in zip("abcdefghijklmnopqrstuvwxyz", [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44])},
    **{str(d): k for d, k in zip("1234567890", range(2, 12))},
}
MODIFIER_CODES = {"ctrl": 29, "alt": 56, "shift": 42}
INFO_KINDS = {"time", "date", "battery", "memory", "disk", "cpu", "uptime"}
USER_DIRS = ("Documents", "Downloads", "Desktop", "Pictures", "Videos", "Music")
GSETTINGS = {   # allowlisted GNOME settings (schema, key)
    "theme": ("org.gnome.desktop.interface", "color-scheme"),
    "nightlight": ("org.gnome.settings-daemon.plugins.color", "night-light-enabled"),
    "dnd": ("org.gnome.desktop.notifications", "show-banners"),
}


class DesktopActionRegistry:
    """Allowlisted desktop controls; no shell execution or arbitrary commands."""

    ACTIONS = {
        "file.search": ActionSpec("read-only", False, "Search workspace filenames", '{"query": str, "limit"?: int}'),
        "system.info": ActionSpec("read-only", False, "Report time, date, battery, memory, disk, cpu or uptime", '{"what": "time|date|battery|memory|disk|cpu|uptime"}'),
        "app.list": ActionSpec("read-only", False, "List installed desktop applications", "{}"),
        "window.list": ActionSpec("read-only", False, "List open windows (id, class, title, workspace)", "{}"),
        "notify.send": ActionSpec("read-only", False, "Show a desktop notification", '{"title": str, "body"?: str}'),
        "speech.say": ActionSpec("read-only", False, "Speak text aloud", '{"text": str}'),
        "ui.read": ActionSpec("read-only", False, "Read what is on screen in the active window (controls, labels, title)", "{}"),
        "window.wait": ActionSpec("read-only", False, "Wait until a window/app appears or its title contains text", '{"target": str, "title_contains"?: str, "timeout"?: 1-30}'),
        "ui.click": ActionSpec("reversible", True, "Click/press a control by its visible name inside the active app", '{"name": str, "role"?: str, "nth"?: int}'),
        "ui.type": ActionSpec("consequential", True, "Type text into a field of the active app", '{"text": str, "name"?: str}'),
        "web.search": ActionSpec("reversible", True, "Search the web (or play the top YouTube result) in the default browser", '{"query": str, "site"?: str, "play"?: bool}'),
        "project.open": ActionSpec("reversible", True, "Open the project folder just created in a code editor", '{"app"?: str}'),
        "overleaf.open": ActionSpec("reversible", True, "Open a .tex file from this task in Overleaf", '{"path": str, "engine"?: "pdflatex|xelatex|lualatex"}'),
        "app.launch": ActionSpec("reversible", True, "Launch an installed application by name", '{"app": str}'),
        "window.focus": ActionSpec("reversible", True, "Focus a window by class/title text", '{"target": str}'),
        "window.move": ActionSpec("reversible", True, "Move a window to a workspace", '{"target": str, "workspace": 1-10}'),
        "window.close": ActionSpec("consequential", True, "Close a window by class/title text", '{"target": str}'),
        "workspace.switch": ActionSpec("reversible", True, "Switch workspace", '{"workspace": 1-10}'),
        "url.open": ActionSpec("reversible", True, "Open an http(s) URL in the default browser", '{"url": str}'),
        "file.open": ActionSpec("reversible", True, "Open a file from the user's folders by name text", '{"query": str}'),
        "theme.set": ActionSpec("reversible", True, "Switch GNOME light/dark appearance", '{"mode": "dark|light"}'),
        "nightlight.set": ActionSpec("reversible", True, "Enable or disable Night Light", '{"enabled": bool}'),
        "dnd.set": ActionSpec("reversible", True, "Enable or disable Do Not Disturb", '{"enabled": bool}'),
        "screen.lock": ActionSpec("consequential", True, "Lock the screen", "{}"),
        "text.type": ActionSpec("consequential", True, "Type text into the focused window", '{"text": str}'),
        "key.press": ActionSpec("consequential", True, "Press a key or ctrl/alt/shift combo, e.g. ctrl+l", '{"key": str}'),
        "volume.set": ActionSpec("reversible", True, "Set audio volume", '{"percent": 0-100}'),
        "brightness.set": ActionSpec("reversible", True, "Set display brightness", '{"percent": 0-100}'),
        "wifi.set": ActionSpec("reversible", True, "Enable or disable Wi-Fi", '{"enabled": bool}'),
        "media.control": ActionSpec("reversible", True, "Control media playback", '{"command": "play-pause|next|previous"}'),
    }

    # Legacy fallback allowlist: still honoured when an app has no .desktop entry.
    APPLICATIONS = {"firefox": "firefox", "code": "code", "alacritty": "alacritty", "nautilus": "nautilus",
                    "gnome-terminal": "gnome-terminal"}
    MEDIA_ACTIONS = {"play-pause", "next", "previous"}

    def __init__(
        self,
        workspace_root: str | Path,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        token_ttl: float = 60.0,
        popen: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] | None = None,
        apps: AppIndex | None = None,
        launch_wait: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        backend: DesktopBackend | None = None,
        user_dirs: list[Path] | None = None,
        clock: Callable[[], datetime] = datetime.now,
        tracker: Any = None,
        procs: ProcessTracker | None = None,
        embedder: Callable[[list[str]], Any] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.runner = runner or subprocess.run
        self.popen = popen or subprocess.Popen
        self._which = which or shutil.which
        self.backend = backend if backend is not None else detect_backend(self.runner, self._which)
        self.apps = apps if apps is not None else AppIndex(mime_default=self._mime_default, embedder=embedder)
        self.launch_wait = max(0.0, float(launch_wait))
        self._sleep = sleep
        self.token_ttl = max(5.0, float(token_ttl))
        self.user_dirs = user_dirs if user_dirs is not None else [Path.home() / d for d in USER_DIRS] + [Path.home()]
        self.clock = clock
        self._pending: dict[str, tuple[str, dict, float]] = {}
        self.tracker = tracker if tracker is not None else (getattr(self.backend, "tracker", None) or AtspiTracker.default())
        self.procs = procs if procs is not None else ProcessTracker()
        self.task_root: Path | None = None            # set by the pipeline: where this task's files live
        self._launched: dict[str, float] = {}        # app key -> launch time (for process-age checks)

    # ------------------------------------------------------------ describe
    def describe(self) -> list[dict]:
        return [{"name": n, "tier": s.tier, "requires_confirmation": s.confirmation,
                 "description": s.description, "args": s.args} for n, s in self.ACTIONS.items()]

    def tier_of(self, action: str) -> str:
        return self._spec(action).tier

    # ------------------------------------------------------ prepare/confirm
    def prepare(self, action: str, arguments: dict | None = None) -> ActionResult:
        args = dict(arguments or {})
        spec = self._spec(action)
        self._validate(action, args)
        if not spec.confirmation:
            return self.execute(action, args)
        token = secrets.token_urlsafe(24)
        self._pending[token] = (action, args, time.monotonic() + self.token_ttl)
        return ActionResult(action=action, tier=spec.tier, success=False,
                            output="Confirmation required before this desktop change.",
                            confirmation_required=True, confirmation_token=token)

    def confirm(self, token: str) -> ActionResult:
        pending = self._pending.pop(token, None)
        if pending is None:
            raise ValueError("Unknown or already-used confirmation token.")
        action, args, expires = pending
        if time.monotonic() > expires:
            raise ValueError("Confirmation token expired.")
        return self._execute(action, args, confirmed=True)

    def deny(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def execute(self, action: str, arguments: dict | None = None) -> ActionResult:
        return self._execute(action, arguments, confirmed=False)

    # ------------------------------------------------------------- execute
    def _execute(self, action: str, arguments: dict | None = None, *, confirmed: bool = False) -> ActionResult:
        args = dict(arguments or {})
        spec = self._spec(action)
        self._validate(action, args)
        if spec.confirmation and not confirmed:
            raise PermissionError("Use prepare() and confirm() for this action.")
        t = spec.tier

        if action == "file.search":
            return self._search(args["query"], args.get("limit", 30))
        if action == "system.info":
            return self._info(spec, args["what"])
        if action == "app.list":
            names = self.apps.names()
            return ActionResult(action, t, True, "\n".join(names[:200]) or "No applications discovered.", data=names)
        if action == "window.list":
            rows = self.clients()
            text = "\n".join(f"{r['address']}  [{r['workspace']}] {r['class']} — {r['title'][:60]}" for r in rows)
            if not rows and not self.backend.available():
                text = f"No window data: {self.backend.name} backend unavailable (see docs/UBUNTU_GNOME_SETUP.md)."
            return ActionResult(action, t, True, text or "No windows open.", data=rows)
        if action == "notify.send":
            return self._run(action, spec, ["notify-send", "-a", "OMNI", args["title"], args.get("body", "")])
        if action == "speech.say":
            tool = "spd-say" if self._which("spd-say") else "espeak-ng"
            return self._run(action, spec, [tool, "--", args["text"]] if tool == "spd-say" else [tool, args["text"]])
        if action == "ui.read":
            if self.tracker is None:
                return ActionResult(action, t, False, "Accessibility tree unavailable (enable: gsettings set org.gnome.desktop.interface toolkit-accessibility true).")
            state = self.tracker.read(limit=40)
            text = f"{state['app']} — {state['window']}\n" + "\n".join(f"[{i['role']}] {i['name']}" for i in state["items"])
            return ActionResult(action, t, state["window"] is not None, text or "Nothing readable.", data=state)
        if action == "window.wait":
            found = self._wait_window(args["target"], args.get("title_contains"), float(args.get("timeout", 8)))
            return ActionResult(action, t, found is not None, f"window {found['class']}: {found['title'][:70]}" if found else "window did not appear in time", data=found)
        if action == "ui.click":
            if self.tracker is None:
                return ActionResult(action, t, False, "Accessibility tree unavailable.")
            ok, detail = self.tracker.click(args["name"], args.get("role"), None, int(args.get("nth", 0)))
            return ActionResult(action, t, ok, detail)
        if action == "ui.type":
            if self.tracker is not None:
                ok, detail = self.tracker.type_into(args["text"], args.get("name"))
                if ok:
                    return ActionResult(action, t, True, detail)
            return self._run(action, spec, ["ydotool", "type", "--", args["text"]])      # fallback: synthetic keystrokes
        if action == "web.search":
            url = self._search_url(args)
            return self._spawn(action, spec, ["xdg-open", url], label=url)
        if action == "project.open":
            return self._open_project(action, spec, args.get("app"))
        if action == "overleaf.open":
            return self._open_overleaf(action, spec, args)
        if action == "app.launch":
            return self._launch(action, spec, args["app"])
        if action in {"window.focus", "window.close", "window.move"}:
            address = self._resolve_window(args)
            if address is None:
                return ActionResult(action, t, False, f"No window matches {args.get('target') or args.get('address')!r}.")
            if action == "window.focus":
                ok, detail = self.backend.focus(address)
            elif action == "window.close":
                ok, detail = self.backend.close(address)
            else:
                ok, detail = self.backend.move(address, args["workspace"])
            return ActionResult(action, t, ok, detail)
        if action == "workspace.switch":
            ok, detail = self.backend.switch_workspace(args["workspace"])
            return ActionResult(action, t, ok, detail)
        if action == "url.open":
            return self._spawn(action, spec, ["xdg-open", args["url"]], label=args["url"])
        if action == "file.open":
            path = self._find_user_file(args["query"])
            if path is None:
                return ActionResult(action, t, False, f"No file matching {args['query']!r} in your folders.")
            return self._spawn(action, spec, ["xdg-open", str(path)], label=path.name)
        if action in {"theme.set", "nightlight.set", "dnd.set"}:
            schema, key = GSETTINGS[action.split(".")[0]]
            value = self._gsettings_value(action, args)
            return self._run(action, spec, ["gsettings", "set", schema, key, value])
        if action == "screen.lock":
            return self._run(action, spec, ["loginctl", "lock-session"])
        if action == "text.type":
            return self._run(action, spec, ["ydotool", "type", "--", args["text"]])
        if action == "key.press":
            return self._run(action, spec, self._ydotool_key(args["key"]))
        if action == "volume.set":
            if self._which("wpctl") or not self._which("pactl"):
                return self._run(action, spec, ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{args['percent']}%"])
            return self._run(action, spec, ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{args['percent']}%"])
        if action == "brightness.set":
            if self._which("gdbus"):     # GNOME settings daemon: works without udev tweaks
                res = self._run(action, spec, ["gdbus", "call", "--session", "--dest", "org.gnome.SettingsDaemon.Power",
                                                "--object-path", "/org/gnome/SettingsDaemon/Power", "--method",
                                                "org.freedesktop.DBus.Properties.Set", "org.gnome.SettingsDaemon.Power.Screen",
                                                "Brightness", f"<int32 {args['percent']}>"])
                if res.success:
                    return res
            return self._run(action, spec, ["brightnessctl", "set", f"{args['percent']}%"])
        if action == "wifi.set":
            return self._run(action, spec, ["nmcli", "radio", "wifi", "on" if args["enabled"] else "off"])
        if action == "media.control":
            return self._run(action, spec, ["playerctl", args["command"]])
        raise ValueError(f"Unsupported action: {action}")

    # ---------------------------------------------------------- validation
    def _spec(self, action: str) -> ActionSpec:
        try:
            return self.ACTIONS[action]
        except KeyError as exc:
            raise ValueError(f"Unknown desktop action: {action}") from exc

    def _validate_target(self, args: dict) -> None:
        if "address" in args:
            address = args.get("address", "")
            if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-fA-F]+|\d{1,12}", address):
                raise ValueError("Invalid window address.")
            return
        target = args.get("target")
        if not isinstance(target, str) or not target.strip() or len(target) > 100:
            raise ValueError("A window target (class or title text, max 100 chars) or address is required.")

    @staticmethod
    def _workspace(args: dict) -> None:
        ws = args.get("workspace")
        if not isinstance(ws, int) or isinstance(ws, bool) or not 1 <= ws <= 10:
            raise ValueError("Workspace must be an integer from 1 to 10.")

    def _validate(self, action: str, args: dict) -> None:
        if action == "file.search":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("A non-empty search query is required.")
            if len(query) > 200:
                raise ValueError("Search query is too long.")
            limit = args.get("limit", 30)
            if not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100.")
        elif action == "system.info":
            if args.get("what") not in INFO_KINDS:
                raise ValueError(f"what must be one of {sorted(INFO_KINDS)}.")
        elif action == "notify.send":
            title, body = args.get("title"), args.get("body", "")
            if not isinstance(title, str) or not title.strip() or len(title) > 80 or not isinstance(body, str) or len(body) > 300:
                raise ValueError("title (<=80) and body (<=300) must be strings.")
        elif action == "speech.say":
            text = args.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 400:
                raise ValueError("text must be 1-400 characters.")
        elif action == "ui.read":
            pass
        elif action == "window.wait":
            tgt, tc, to = args.get("target"), args.get("title_contains", ""), args.get("timeout", 8)
            if not isinstance(tgt, str) or not tgt.strip() or len(tgt) > 100 or not isinstance(tc, str) or len(tc) > 100:
                raise ValueError("target (<=100 chars) required; title_contains must be text.")
            if not isinstance(to, (int, float)) or isinstance(to, bool) or not 1 <= to <= 30:
                raise ValueError("timeout must be 1-30 seconds.")
        elif action == "ui.click":
            nm = args.get("name")
            if not isinstance(nm, str) or not nm.strip() or len(nm) > 120 or not isinstance(args.get("nth", 0), int):
                raise ValueError("name (<=120 chars) required.")
        elif action == "ui.type":
            tx = args.get("text")
            if not isinstance(tx, str) or not tx or len(tx) > 2000 or any(ord(c) < 32 and c not in "\n\t" for c in tx):
                raise ValueError("text must be 1-2000 printable characters.")
        elif action == "web.search":
            q, site = args.get("query"), args.get("site", "")
            if not isinstance(q, str) or not q.strip() or len(q) > 200 or not isinstance(site, str) or not re.fullmatch(r"[\w.\-]{0,60}", site):
                raise ValueError("query (<=200 chars) required; site must look like a domain or name.")
        elif action == "project.open":
            if args.get("app") is not None and (not isinstance(args["app"], str) or len(args["app"]) > 100):
                raise ValueError("app must be a short application name or type.")
        elif action == "overleaf.open":
            path, eng = args.get("path"), args.get("engine", "pdflatex")
            if not isinstance(path, str) or not path.endswith(".tex") or ".." in path or path.startswith("/") or eng not in {"pdflatex", "xelatex", "lualatex"}:
                raise ValueError("path must be a relative .tex file; engine pdflatex|xelatex|lualatex.")
        elif action == "app.launch":
            name = args.get("app")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("An application name is required.")
            if self._resolve_app(name) is None:
                raise ValueError("Application is not installed or not in the launch allowlist.")
        elif action in {"window.focus", "window.close"}:
            self._validate_target(args)
        elif action == "window.move":
            self._validate_target(args)
            self._workspace(args)
        elif action == "workspace.switch":
            self._workspace(args)
        elif action == "url.open":
            url = args.get("url")
            if not isinstance(url, str) or len(url) > 2048 or not re.fullmatch(r"https?://[^\s]+", url):
                raise ValueError("url must be an http(s) URL without spaces.")
        elif action == "file.open":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > 120 or "/" in query or ".." in query:
                raise ValueError("query must be a file-name fragment (no path separators).")
        elif action == "theme.set":
            if args.get("mode") not in {"dark", "light"}:
                raise ValueError("mode must be 'dark' or 'light'.")
        elif action in {"nightlight.set", "dnd.set", "wifi.set"}:
            if not isinstance(args.get("enabled"), bool):
                raise ValueError("enabled must be true or false.")
        elif action == "text.type":
            text = args.get("text")
            if not isinstance(text, str) or not text or len(text) > 500:
                raise ValueError("text must be 1-500 characters.")
            if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
                raise ValueError("text contains control characters.")
        elif action == "key.press":
            self._ydotool_key(args.get("key"))
        elif action in {"volume.set", "brightness.set"}:
            percent = args.get("percent")
            if not isinstance(percent, int) or isinstance(percent, bool) or not 0 <= percent <= 100:
                raise ValueError("percent must be an integer from 0 to 100.")
        elif action == "media.control":
            if args.get("command") not in self.MEDIA_ACTIONS:
                raise ValueError("Unsupported media command.")

    # -------------------------------------------------------------- helpers
    def _resolve_app(self, name: str) -> AppEntry | str | None:
        entry = self.apps.resolve(name)
        if entry is not None:
            return entry
        legacy = self.APPLICATIONS.get(name.strip().lower())
        return legacy if legacy and self._which(legacy) else None

    @staticmethod
    def _ydotool_key(key: Any) -> list[str]:
        if not isinstance(key, str) or not key or len(key) > 40:
            raise ValueError("key must be a non-empty string.")
        *mods, base = key.split("+")
        if any(m.lower() not in MODIFIER_CODES for m in mods):
            raise ValueError("Only ctrl/alt/shift modifiers are allowed.")
        if base not in KEYCODES:
            raise ValueError(f"Key not in allowlist: {base}")
        seq = [f"{MODIFIER_CODES[m.lower()]}:1" for m in mods] + [f"{KEYCODES[base]}:1", f"{KEYCODES[base]}:0"]
        seq += [f"{MODIFIER_CODES[m.lower()]}:0" for m in reversed(mods)]
        return ["ydotool", "key", *seq]

    @staticmethod
    def _gsettings_value(action: str, args: dict) -> str:
        if action == "theme.set":
            return "prefer-dark" if args["mode"] == "dark" else "default"
        if action == "nightlight.set":
            return "true" if args["enabled"] else "false"
        return "false" if args["enabled"] else "true"      # dnd on == banners off

    def clients(self) -> list[dict]:
        return self.backend.clients()

    def _resolve_window(self, args: dict) -> str | None:
        if args.get("address"):
            return str(args["address"])
        needle = str(args.get("target", "")).strip().lower()
        best: tuple[tuple[int, int], str] | None = None
        for row in self.clients():
            cls, title = row["class"].lower(), row["title"].lower()
            if needle == cls:
                rank = 0
            elif (cls and (needle in cls or cls in needle)):
                rank = 1
            elif needle in title:
                rank = 2
            else:
                continue
            key = (rank, int(row.get("focus", 99)))
            if best is None or key < best[0]:
                best = (key, row["address"])
        return best[1] if best else None

    def _window_matches(self, entry: AppEntry | str, rows: list[dict]) -> dict | None:
        wanted = entry.window_classes() if isinstance(entry, AppEntry) else {entry.lower()}
        for row in rows:
            cls = row["class"].lower()
            if cls and (cls in wanted or any(w and (w in cls or cls in w) for w in wanted)):
                return row
        return None

    def _entry_command(self, entry: AppEntry) -> list[str]:
        if entry.path and self._which("gio"):
            return ["gio", "launch", entry.path]       # lets GNOME associate the window with the app
        return entry.command()

    def _mime_default(self, mime: str) -> str | None:
        """Installed default handler for a MIME type or URL scheme (`gio mime`)."""
        exe = self._which("gio")
        if exe is None:
            return None
        try:
            out = self.runner([exe, "mime", mime], capture_output=True, text=True, timeout=5, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"([\w.\-+]+\.desktop)", out.splitlines()[0] if out.strip() else "")
        return m.group(1) if m else None

    def _wanted_names(self, target: AppEntry | str) -> set[str]:
        return target.window_classes() if isinstance(target, AppEntry) else {target.lower()}

    def _running(self, target: AppEntry | str, since: float | None = None) -> list[dict]:
        names = target.process_names() if isinstance(target, AppEntry) else {str(target).lower()}
        return self.procs.find(names, since)

    def _launch(self, action: str, spec: ActionSpec, name: str, extra: Sequence[str] = ()) -> ActionResult:
        target = self._resolve_app(name)
        if target is None:
            return ActionResult(action, spec.tier, False, f"Application not found: {name}")
        label = target.name if isinstance(target, AppEntry) else name
        key = target.desktop_id if isinstance(target, AppEntry) else str(target)
        if not extra:                          # idempotent: never start a second copy of something already open
            row = self._window_matches(target, self.clients())
            if row or self._running(target):
                if row and row["address"].isdigit() or (row and row["address"].startswith("0x")):
                    self.backend.focus(row["address"])
                return ActionResult(action, spec.tier, True, f"{label} is already running" + (f"; window {row['address']}" if row else ""),
                                    data={"window": row, "app": label, "already_running": True})
        command = (self._entry_command(target) if isinstance(target, AppEntry) else [target]) + list(extra)
        self._launched[key] = time.time()
        result = self._spawn(action, spec, command, label=label)
        if not result.success:
            return result
        window = self._await_window(target, self.launch_wait)
        suffix = (f"; window {window['address']} [{window['class']}]" if window
                  else "; window not seen yet (process tracking continues)")
        return ActionResult(action, spec.tier, True, result.output + suffix, data={"window": window, "app": label})

    def _wait_window(self, target: str, title_contains: str | None, timeout: float) -> dict | None:
        deadline, needle = time.monotonic() + timeout, (title_contains or "").lower()
        while True:
            for row in self.clients():
                hay = (row["class"] + " " + row["title"]).lower()
                if target.lower() in hay and (not needle or needle in row["title"].lower()):
                    return row
            if time.monotonic() >= deadline:
                return None
            self._sleep(0.3)

    def _search_url(self, args: dict) -> str:
        q, site = args["query"].strip(), (args.get("site") or "").strip().lower()
        enc = urllib.parse.quote_plus(q)
        if "youtube" in site:
            if args.get("play") and self._which("yt-dlp"):          # resolve the top hit so it can start playing
                try:
                    out = self.runner([self._which("yt-dlp"), "--no-warnings", "--get-id", "--default-search", "ytsearch1:", q],
                                      capture_output=True, text=True, timeout=25, check=False).stdout.strip().splitlines()
                    if out and re.fullmatch(r"[\w\-]{11}", out[0]):
                        return f"https://www.youtube.com/watch?v={out[0]}&autoplay=1"
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return f"https://www.youtube.com/results?search_query={enc}"
        if site and "." in site:
            return f"https://www.google.com/search?q={urllib.parse.quote_plus('site:' + site + ' ' + q)}"
        return f"https://www.google.com/search?q={enc}"

    def _open_project(self, action: str, spec: ActionSpec, app: str | None) -> ActionResult:
        if self.task_root is None or not self.task_root.is_dir():
            return ActionResult(action, spec.tier, False, "No project folder exists for this task.")
        for wanted in ([app] if app else []) + ["code editor", "text editor"]:
            entry = self.apps.resolve(wanted)
            if entry is not None:
                res = self._launch(action, spec, entry.name, extra=[str(self.task_root)])
                return ActionResult(action, spec.tier, res.success, f"{res.output}; folder {self.task_root}", data={"app": entry.name, "folder": str(self.task_root)})
        return self._spawn(action, spec, ["xdg-open", str(self.task_root)], label=str(self.task_root))

    def _open_overleaf(self, action: str, spec: ActionSpec, args: dict) -> ActionResult:
        base = self.task_root or self.workspace_root
        tex = (base / args["path"]).resolve()
        if not str(tex).startswith(str(base.resolve())) or not tex.is_file():
            return ActionResult(action, spec.tier, False, f"{args['path']} not found in this task's folder.")
        src = tex.read_text(encoding="utf-8", errors="replace")
        if len(src) > 400_000:
            return ActionResult(action, spec.tier, False, "LaTeX source too large for Overleaf's open-in-Overleaf form.")
        form = ("<!doctype html><meta charset=utf-8><title>Opening Overleaf…</title><p>Opening Overleaf…</p>"
                '<form id=f action="https://www.overleaf.com/docs" method="post">'
                f'<input type=hidden name=snip_name value="{html.escape(tex.stem, quote=True)}">'
                f'<input type=hidden name=engine value="{args.get("engine", "pdflatex")}">'
                f'<input type=hidden name=snip value="{html.escape(src, quote=True)}"></form>'
                "<script>document.getElementById('f').submit()</script>")
        out_dir = self.workspace_root / ".omni"
        out_dir.mkdir(parents=True, exist_ok=True)
        page = out_dir / f"overleaf_{hashlib.sha1(src.encode()).hexdigest()[:10]}.html"
        page.write_text(form, encoding="utf-8")
        res = self._spawn(action, spec, ["xdg-open", page.as_uri()], label="Overleaf")
        return ActionResult(action, spec.tier, res.success, res.output + f"; source {tex.name} ({len(src)} chars) sent to Overleaf", data={"tex": str(tex), "page": str(page)})

    def _await_window(self, entry: AppEntry | str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while True:
            row = self._window_matches(entry, self.clients())
            if row:
                return row
            if time.monotonic() >= deadline:
                return None
            self._sleep(0.25)

    def _spawn(self, action: str, spec: ActionSpec, command: Sequence[str], label: str = "") -> ActionResult:
        """Start a long-running GUI process detached; never wait for it to exit."""
        executable = self._which(command[0])
        if executable is None:
            return ActionResult(action, spec.tier, False, f"Required executable not found: {command[0]}")
        try:
            proc = self.popen([executable, *command[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        except OSError as exc:
            return ActionResult(action, spec.tier, False, str(exc))
        threading.Thread(target=proc.wait, daemon=True, name="omni-reap").start()
        return ActionResult(action, spec.tier, True, f"started {label or command[0]} (pid {proc.pid})")

    def _find_user_file(self, query: str) -> Path | None:
        words = [w for w in re.findall(r"[a-z0-9]+", query.casefold()) if w]
        best: tuple[float, Path] | None = None
        scanned = 0
        for root in self.user_dirs:
            if not root.is_dir():
                continue
            depth0 = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                if len(Path(dirpath).parts) - depth0 >= (1 if root == Path.home() else 3):
                    dirnames[:] = []
                for fname in filenames:
                    scanned += 1
                    if scanned > 20000:
                        break
                    if fname.startswith("."):
                        continue
                    stem = fname.casefold()
                    if all(w in stem for w in words):
                        full = Path(dirpath) / fname
                        try:
                            mtime = full.stat().st_mtime
                        except OSError:
                            continue
                        if best is None or mtime > best[0]:
                            best = (mtime, full)
        return best[1] if best else None

    # ---------------------------------------------------------- system info
    def _info(self, spec: ActionSpec, what: str) -> ActionResult:
        now = self.clock()
        try:
            if what == "time":
                text = now.strftime("It is %H:%M")
            elif what == "date":
                text = now.strftime("Today is %A, %d %B %Y")
            elif what == "battery":
                bats = sorted(Path("/sys/class/power_supply").glob("BAT*"))
                if not bats:
                    return ActionResult("system.info", spec.tier, False, "No battery found.")
                cap = (bats[0] / "capacity").read_text().strip()
                status = (bats[0] / "status").read_text().strip().lower()
                text = f"Battery is at {cap}% ({status})"
            elif what == "memory":
                info = {k: int(v.split()[0]) for k, v in (l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines())}
                used = (info["MemTotal"] - info["MemAvailable"]) / 2**20
                text = f"Memory: {used:.1f} GiB used of {info['MemTotal'] / 2**20:.1f} GiB"
            elif what == "disk":
                usage = shutil.disk_usage("/")
                text = f"Disk: {usage.used / 2**30:.0f} GiB used of {usage.total / 2**30:.0f} GiB"
            elif what == "cpu":
                text = "CPU load average (1/5/15 min): " + " / ".join(f"{x:.2f}" for x in os.getloadavg())
            else:
                secs = float(Path("/proc/uptime").read_text().split()[0])
                text = f"Uptime: {int(secs // 3600)} h {int(secs % 3600 // 60)} min"
        except (OSError, KeyError, ValueError) as exc:
            return ActionResult("system.info", spec.tier, False, f"Could not read {what}: {exc}")
        return ActionResult("system.info", spec.tier, True, text, data={"what": what})

    # ---------------------------------------------------------- verification
    def layers(self) -> dict[str, bool]:
        base = self.backend.layers() if hasattr(self.backend, "layers") else {"bridge": self.backend.available(), "atspi": False}
        base = dict(base)
        base["atspi"] = bool(base.get("atspi") or (self.tracker is not None and self.tracker.available()))
        base["proc"] = True
        return base

    def _window_layer(self) -> bool:
        lay = self.layers()
        return bool(lay.get("bridge") or lay.get("atspi"))

    def _nav_tokens(self, args: dict, action: str) -> set[str]:
        if action == "web.search":
            raw = f"{args.get('site', '')} {args['query']}"
        elif action == "url.open":
            u = urllib.parse.urlparse(args["url"])
            raw = u.netloc + " " + urllib.parse.unquote_plus(u.query)
        else:
            raw = args.get("path", "overleaf")
        return {w for w in re.findall(r"[a-z0-9]{3,}", raw.lower()) if w not in {"www", "com", "org", "https", "http", "search", "query", "results", "watch", "autoplay"}}

    def verify(self, action: str, args: dict) -> dict | None:
        """Independent post-condition check. ``None`` means no check exists."""
        if action in {"app.launch", "project.open"}:
            name = args.get("app") if action == "app.launch" else (args.get("app") or "code editor")
            target = self._resolve_app(name) or (self._resolve_app("text editor") if action == "project.open" else None)
            if target is None:
                return {"passed": False, "detail": "app not resolvable"}
            key = target.desktop_id if isinstance(target, AppEntry) else str(target)
            row = self._await_window(target, self.launch_wait)
            if row:
                return {"passed": True, "detail": f"window {row['class']} present", "layer": "window"}
            procs = self._running(target, self._launched.get(key))
            if procs or self._running(target):
                note = "window tracking unavailable" if not self._window_layer() else "window not matched by class"
                return {"passed": True, "detail": f"process {procs[0]['comm'] if procs else 'running'} (pid {(procs or self._running(target))[0]['pid']}) alive; {note}", "layer": "process"}
            return {"passed": False, "detail": "neither a window nor a running process was found"}
        if action in {"url.open", "web.search", "overleaf.open"}:
            toks = self._nav_tokens(args, action)
            deadline = time.monotonic() + max(self.launch_wait, 0.0)
            while True:
                for row in self.clients():
                    if any(t in row["title"].lower() for t in toks):
                        return {"passed": True, "detail": f"window titled {row['title'][:60]!r}", "layer": "window"}
                if time.monotonic() >= deadline:
                    break
                self._sleep(0.3)
            browser = self.apps.resolve("web browser")
            if browser is not None and self._running(browser):
                note = "title not matched yet" if self._window_layer() else "window tracking unavailable"
                # A live browser proves a browser is open, not that navigation succeeded:
                # reported as passed (it is not a failure) but flagged non-conclusive so the
                # pipeline can say the end state was not independently confirmed.
                return {"passed": True, "detail": f"browser process alive; {note}", "layer": "process", "conclusive": False}
            return {"passed": False, "detail": "no browser window or process found"}
        if action == "workspace.switch":
            ws = self.backend.active_workspace()
            if ws is None:
                return None                                  # cannot observe it: unverified, not failed
            return {"passed": ws == args["workspace"], "detail": f"active workspace is {ws}"}
        if action == "window.focus":
            address = self._resolve_window(args)
            active = self.backend.active_window()
            if active is None and not self._window_layer():
                return None
            return {"passed": bool(address) and bool(active) and active["address"] == address,
                    "detail": f"active window {active['address'] if active else None}"}
        if action == "window.close":
            if not self._window_layer():
                return None
            gone = self._resolve_window(args) is None
            return {"passed": gone, "detail": "window gone" if gone else "window still open"}
        if action == "window.move":
            address = self._resolve_window(args)
            for row in self.clients():
                if row["address"] == address:
                    return {"passed": row["workspace"] == args["workspace"], "detail": f"window on workspace {row['workspace']}"}
            return None if not self._window_layer() else {"passed": False, "detail": "window not found"}
        if action in {"theme.set", "nightlight.set", "dnd.set"}:
            if not self._which("gsettings"):
                return None
            schema, key = GSETTINGS[action.split(".")[0]]
            try:
                out = self.runner([self._which("gsettings"), "get", schema, key], capture_output=True, text=True, timeout=5, check=False).stdout.strip().strip("'")
            except (OSError, subprocess.TimeoutExpired):
                return None
            want = self._gsettings_value(action, args)
            return {"passed": out == want, "detail": f"{key} = {out}"}
        if action == "volume.set":
            exe = self._which("wpctl")
            if exe is None:
                return None
            try:
                out = self.runner([exe, "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=5, check=False).stdout
                match = re.search(r"([0-9]*\.?[0-9]+)", out or "")
                level = round(float(match.group(1)) * 100) if match else None
            except (OSError, subprocess.TimeoutExpired, ValueError):
                return None
            return {"passed": level is not None and abs(level - args["percent"]) <= 1, "detail": f"volume now {level}%"}
        return None

    def app_state(self) -> dict[str, Any]:
        """Everything the planner/critic may need to know about what is happening on the desktop right now."""
        state: dict[str, Any] = {"layers": self.layers(), "windows": [], "active": None, "ui": None, "processes": []}
        try:
            state["windows"] = self.clients()[:12]
            state["active"] = self.backend.active_window()
        except Exception:  # noqa: BLE001
            pass
        if self.tracker is not None:
            try:
                state["ui"] = self.tracker.read(limit=25)
            except Exception:  # noqa: BLE001
                pass
        try:
            seen = {}
            for p in self.procs.snapshot():
                if re.search(r"/(snap|usr/lib|opt|usr/bin)/", p["cmd"] or "") and p["comm"] not in {"bash", "zsh", "sh", "python3"}:
                    seen.setdefault(p["comm"], p["pid"])
            state["processes"] = sorted(seen)[:25]
        except Exception:  # noqa: BLE001
            pass
        return state

    def _search(self, query: str, limit: int) -> ActionResult:
        needle = query.casefold()
        matches = []
        try:
            for path in self.workspace_root.rglob("*"):
                if len(matches) >= limit:
                    break
                if path.is_file() and needle in path.name.casefold():
                    matches.append(str(path.relative_to(self.workspace_root)))
        except OSError as exc:
            return ActionResult("file.search", "read-only", False, str(exc))
        return ActionResult("file.search", "read-only", True, "\n".join(matches) if matches else "No matching filenames found.")

    def _run(self, action: str, spec: ActionSpec, command: Sequence[str]) -> ActionResult:
        executable = self._which(command[0])
        if executable is None:
            return ActionResult(action, spec.tier, False, f"Required executable not found: {command[0]}")
        try:
            result = self.runner([executable, *command[1:]], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ActionResult(action, spec.tier, False, str(exc))
        output = (result.stdout or result.stderr or "").strip()[:2000]
        return ActionResult(action, spec.tier, result.returncode == 0, output or f"Command exited with status {result.returncode}.")
