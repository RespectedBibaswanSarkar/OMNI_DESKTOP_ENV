"""Application tracking that keeps working when the GNOME bridge is off.

Three independent evidence layers, strongest first:
  1. bridge   GNOME Shell extension: windows, focus, workspace          (backends/gnome.py)
  2. atspi    accessibility tree: windows, active state, visible text,  (AtspiTracker)
              and the ability to click / type inside any accessible app
  3. proc     /proc: is the application's process alive, since when     (ProcessTracker)
A task is judged on the best available layer, never on a missing one.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Callable

WORD = re.compile(r"[a-z0-9]+")


# ------------------------------------------------------------------ /proc layer
class ProcessTracker:
    def __init__(self, proc_root: str | Path = "/proc", boot_time: float | None = None, clk_tck: int | None = None) -> None:
        self.root = Path(proc_root)
        self.uid = os.getuid()
        self.clk = clk_tck or (os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100)
        self._boot = boot_time

    def boot_time(self) -> float:
        if self._boot is None:
            try:
                for line in (self.root / "stat").read_text().splitlines():
                    if line.startswith("btime"):
                        self._boot = float(line.split()[1])
            except (OSError, ValueError):
                self._boot = 0.0
        return self._boot or 0.0

    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        try:
            entries = [p for p in self.root.iterdir() if p.name.isdigit()]
        except OSError:
            return out
        for p in entries:
            try:
                if p.stat().st_uid != self.uid:
                    continue
                cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                comm = (p / "comm").read_text().strip()
                stat = (p / "stat").read_text()
                fields = stat[stat.rindex(")") + 2:].split()
                start = self.boot_time() + int(fields[19]) / self.clk
                out.append({"pid": int(p.name), "comm": comm, "cmd": cmd[:300], "start": start, "state": fields[0]})
            except (OSError, ValueError, IndexError):
                continue
        return out

    def find(self, names: set[str], since: float | None = None) -> list[dict[str, Any]]:
        wanted = {n.lower() for n in names if n and len(n) >= 3}
        hits = []
        for proc in self.snapshot():
            if proc["state"] in {"Z", "X"}:
                continue
            exe = Path(proc["cmd"].split(" ")[0]).name.lower() if proc["cmd"] else ""
            tokens = set(WORD.findall(proc["comm"].lower())) | set(WORD.findall(exe))
            text = proc["cmd"].lower()
            if any(w == proc["comm"].lower() or w == exe or w in tokens or f"/{w}/" in text or f"/{w} " in text + " " for w in wanted):
                if since is None or proc["start"] >= since - 2.0:
                    hits.append(proc)
        return hits


# ------------------------------------------------------------- AT-SPI layer
class RealAtspi:
    """Thin adapter over gi.repository.Atspi (kept separate so tests can fake it)."""

    def __init__(self) -> None:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        self.A = Atspi
        Atspi.init()

    def roots(self) -> list[Any]:
        desk = self.A.get_desktop(0)
        return [desk.get_child_at_index(i) for i in range(desk.get_child_count())]

    def _safe(self, fn, default=None):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - AT-SPI raises GLib.Error for dead apps
            return default

    def name(self, n) -> str: return self._safe(n.get_name, "") or ""
    def role(self, n) -> str: return self._safe(n.get_role_name, "") or ""
    def pid(self, n) -> int | None: return self._safe(n.get_process_id)
    def active(self, n) -> bool: return bool(self._safe(lambda: n.get_state_set().contains(self.A.StateType.ACTIVE), False))

    def children(self, n) -> list[Any]:
        count = self._safe(n.get_child_count, 0) or 0
        return [c for c in (self._safe(lambda i=i: n.get_child_at_index(i)) for i in range(count)) if c is not None]

    def actions(self, n) -> list[str]:
        iface = self._safe(lambda: n.get_action_iface()) if hasattr(n, "get_action_iface") else self._safe(n.get_action)
        if not iface:
            return []
        return [self._safe(lambda i=i: iface.get_action_name(i), "") for i in range(self._safe(iface.get_n_actions, 0) or 0)]

    def do_action(self, n, index: int) -> bool:
        iface = self._safe(lambda: n.get_action_iface()) if hasattr(n, "get_action_iface") else self._safe(n.get_action)
        return bool(iface and self._safe(lambda: iface.do_action(index), False))

    def set_text(self, n, text: str) -> bool:
        iface = self._safe(lambda: n.get_editable_text_iface()) if hasattr(n, "get_editable_text_iface") else self._safe(n.get_editable_text)
        return bool(iface and self._safe(lambda: iface.set_text_contents(text), False))


WINDOW_ROLES = {"frame", "window", "dialog", "alert"}
CONTROL_ROLES = {"push button", "link", "menu item", "entry", "text", "label", "heading", "page tab", "check box",
                 "toggle button", "list item", "radio button", "combo box", "paragraph", "terminal", "section"}
EDIT_ROLES = {"entry", "text", "password text", "terminal", "paragraph", "document text", "document web"}
CLICK_ACTIONS = ("click", "press", "activate", "jump", "open", "select")


class AtspiTracker:
    def __init__(self, adapter: Any | None = None) -> None:
        self.adapter = adapter

    @classmethod
    def default(cls) -> "AtspiTracker | None":
        try:
            return cls(RealAtspi())
        except Exception:  # noqa: BLE001 - no gi / no Atspi typelib / no a11y bus
            return None

    def available(self) -> bool:
        try:
            return bool(self.adapter and self.adapter.roots() is not None)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- windows
    def _app_windows(self) -> list[tuple[Any, Any]]:
        a = self.adapter
        out = []
        for app in a.roots():
            for child in a.children(app):
                if a.role(child) in WINDOW_ROLES:
                    out.append((app, child))
        return out

    def windows(self) -> list[dict[str, Any]]:
        a, rows = self.adapter, []
        for i, (app, win) in enumerate(self._app_windows()):
            rows.append({"address": f"atspi:{a.pid(win) or a.pid(app) or 0}:{i}", "class": a.name(app).lower(),
                         "title": a.name(win), "workspace": None, "pid": a.pid(win) or a.pid(app),
                         "focus": 0 if a.active(win) else 1})
        return rows

    def _target_window(self, hint: str | None = None):
        wins = self._app_windows()
        a = self.adapter
        if hint:
            h = hint.lower()
            for app, win in wins:
                if h in a.name(app).lower() or h in a.name(win).lower():
                    return app, win
        for app, win in wins:
            if a.active(win):
                return app, win
        return wins[0] if wins else (None, None)

    def _walk(self, root, limit: int = 600):
        a, stack, seen = self.adapter, [root], 0
        while stack and seen < limit:
            node = stack.pop(0)
            seen += 1
            yield node
            stack.extend(a.children(node))

    # ---------------------------------------------------------- reading
    def read(self, window: str | None = None, limit: int = 40) -> dict[str, Any]:
        """What is on screen in the active (or named) window, as plain text for the planner/critic."""
        a = self.adapter
        app, win = self._target_window(window)
        if win is None:
            return {"window": None, "app": None, "items": []}
        items, seen = [], set()
        for node in self._walk(win):
            role, name = a.role(node), a.name(node).strip()
            if role in CONTROL_ROLES and name and (role, name) not in seen:
                seen.add((role, name))
                items.append({"role": role, "name": name[:90]})
                if len(items) >= limit:
                    break
        return {"window": a.name(win), "app": a.name(app), "items": items}

    def find(self, name: str, role: str | None = None, window: str | None = None, nth: int = 0, actionable: bool = False) -> Any | None:
        _, win = self._target_window(window)
        if win is None:
            return None
        a, needle, hits = self.adapter, name.lower(), []
        for node in self._walk(win):
            if node is win:
                continue                                   # the window's own title is not a control
            if needle and needle not in a.name(node).lower():
                continue
            if role and role.lower() != a.role(node):
                continue
            if actionable and not a.actions(node):
                continue
            hits.append(node)
        return hits[nth] if 0 <= nth < len(hits) else None

    # ----------------------------------------------------------- acting
    def click(self, name: str, role: str | None = None, window: str | None = None, nth: int = 0) -> tuple[bool, str]:
        node = self.find(name, role, window, nth, actionable=True)
        if node is None:
            return False, f"no clickable control named {name!r} found in the active window"
        a = self.adapter
        names = [x.lower() for x in a.actions(node)]
        for want in CLICK_ACTIONS:
            for i, got in enumerate(names):
                if want in got:
                    return (True, f"{want} on {a.role(node)} {a.name(node)[:60]!r}") if a.do_action(node, i) else (False, "action refused")
        return (True, "default action") if names and a.do_action(node, 0) else (False, "control has no action")

    def type_into(self, text: str, name: str | None = None, window: str | None = None) -> tuple[bool, str]:
        _, win = self._target_window(window)
        if win is None:
            return False, "no accessible window"
        a = self.adapter
        for node in self._walk(win):
            if a.role(node) in EDIT_ROLES and (not name or name.lower() in a.name(node).lower()):
                if a.set_text(node, text):
                    return True, f"typed {len(text)} chars into {a.role(node)} {a.name(node)[:40]!r}"
        return False, "no editable field accepted the text"
