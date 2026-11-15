from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from omni.desktop.apps import AppEntry, AppIndex
from omni.desktop.backends import DesktopBackend, detect_backend


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
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.runner = runner or subprocess.run
        self.popen = popen or subprocess.Popen
        self._which = which or shutil.which
        self.backend = backend if backend is not None else detect_backend(self.runner, self._which)
        self.apps = apps if apps is not None else AppIndex()
        self.launch_wait = max(0.0, float(launch_wait))
        self._sleep = sleep
        self.token_ttl = max(5.0, float(token_ttl))
        self.user_dirs = user_dirs if user_dirs is not None else [Path.home() / d for d in USER_DIRS] + [Path.home()]
        self.clock = clock
        self._pending: dict[str, tuple[str, dict, float]] = {}

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

    def _launch(self, action: str, spec: ActionSpec, name: str) -> ActionResult:
        target = self._resolve_app(name)
        if target is None:
            return ActionResult(action, spec.tier, False, f"Application not found: {name}")
        command, label = (self._entry_command(target), target.name) if isinstance(target, AppEntry) else ([target], name)
        result = self._spawn(action, spec, command, label=label)
        if not result.success:
            return result
        window = self._await_window(target, self.launch_wait)
        suffix = (f"; window {window['address']} [{window['class']}] on workspace {window['workspace']}"
                  if window else "; no window detected yet")
        return ActionResult(action, spec.tier, True, result.output + suffix, data={"window": window, "app": label})

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
    def verify(self, action: str, args: dict) -> dict | None:
        """Independent post-condition check. ``None`` means no check exists."""
        if action == "app.launch":
            target = self._resolve_app(args["app"])
            if target is None:
                return {"passed": False, "detail": "app not resolvable"}
            row = self._await_window(target, self.launch_wait)
            return {"passed": row is not None, "detail": f"window {row['class']} present" if row else "no matching window appeared"}
        if action == "workspace.switch":
            ws = self.backend.active_workspace()
            return {"passed": ws == args["workspace"], "detail": f"active workspace is {ws}"}
        if action == "window.focus":
            address = self._resolve_window(args)
            active = self.backend.active_window()
            return {"passed": bool(address) and bool(active) and active["address"] == address,
                    "detail": f"active window {active['address'] if active else None}"}
        if action == "window.close":
            gone = self._resolve_window(args) is None
            return {"passed": gone, "detail": "window gone" if gone else "window still open"}
        if action == "window.move":
            address = self._resolve_window(args)
            for row in self.clients():
                if row["address"] == address:
                    return {"passed": row["workspace"] == args["workspace"], "detail": f"window on workspace {row['workspace']}"}
            return {"passed": False, "detail": "window not found"}
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
