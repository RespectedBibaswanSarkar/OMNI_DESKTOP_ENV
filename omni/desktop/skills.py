"""GNOME desktop skills for DesktopActionRegistry. Pure helpers: no shell, allowlisted inputs only."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from omni.scheduler.telemetry import process_table, system_state

NEWS_FEEDS = {
    "world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "india": "https://feeds.bbci.co.uk/news/world/asia/india/rss.xml",
}
SETTINGS = {
    "dark-mode": ("org.gnome.desktop.interface", "color-scheme", lambda v: "'prefer-dark'" if v else "'default'"),
    "night-light": ("org.gnome.settings-daemon.plugins.color", "night-light-enabled", lambda v: "true" if v else "false"),
    "animations": ("org.gnome.desktop.interface", "enable-animations", lambda v: "true" if v else "false"),
}
SKILL_SPECS = {
    "pdf.open": ("reversible", True, "Open a PDF from home/workspace in a viewer", '{"path"?: str, "query"?: str, "viewer"?: "default|browser"}'),
    "calendar.add": ("reversible", True, "Create a calendar event (.ics) and open it in GNOME Calendar", '{"title": str, "start": "YYYY-MM-DD HH:MM", "minutes"?: int, "location"?: str}'),
    "alarm.set": ("reversible", True, "Schedule an alarm or reminder notification", '{"label": str, "seconds"?: int, "at"?: "YYYY-MM-DD HH:MM[:SS]"}'),
    "notify.send": ("reversible", False, "Show a desktop notification", '{"title": str, "body": str}'),
    "notify.dnd": ("reversible", True, "Turn Do-Not-Disturb on/off (suppress notification banners)", '{"enabled": bool}'),
    "settings.set": ("reversible", True, "Change an allowlisted GNOME setting", '{"key": "dark-mode|night-light|animations", "value": bool}'),
    "system.status": ("read-only", False, "Report CPU, memory, disk, battery, pressure and top processes", "{}"),
    "news.fetch": ("read-only", False, "Fetch latest news headlines (BBC RSS)", '{"topic"?: "world|technology|science|business|india", "limit"?: 1-15}'),
    "web.search": ("reversible", True, "Search the web in the default browser", '{"query": str, "engine"?: "duckduckgo|google"}'),
    "window.minimize": ("reversible", True, "Minimise a window by class/title text", '{"target": str}'),
    "focus.only": ("reversible", True, "Keep one window focused, minimise the rest, silence banners", '{"target"?: str, "silence"?: bool}'),
}
SKILL_ACTIONS = set(SKILL_SPECS)
_SKIP_DIRS = {"node_modules", "site-packages", "venv", ".venv", "__pycache__"}
_DND = ["gsettings", "set", "org.gnome.desktop.notifications", "show-banners"]


# ------------------------------------------------------------------ helpers
def _parse_dt(value: Any) -> datetime:
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    raise ValueError("time must look like 'YYYY-MM-DD HH:MM'.")


def _alarm_delay(args: dict) -> float:
    if args.get("seconds") is not None:
        return float(args["seconds"])
    return (_parse_dt(args.get("at")) - datetime.now()).total_seconds()


def _roots(reg) -> list[Path]:  # noqa: ANN001
    return [Path.home().resolve(), Path(reg.workspace_root).resolve()]


def _inside(path: Path, roots: list[Path]) -> bool:
    for r in roots:
        try:
            path.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _search_pdf(roots, words, exact, max_files=20000):  # noqa: ANN001
    best, seen = None, 0
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
            for f in files:
                seen += 1
                if seen > max_files:
                    return best[1] if best else None
                low = f.lower()
                if not low.endswith(".pdf"):
                    continue
                if (exact and low == words[0]) or (not exact and all(w in low for w in words)):
                    full = Path(dirpath) / f
                    try:
                        mt = full.stat().st_mtime
                    except OSError:
                        continue
                    if best is None or mt > best[0]:
                        best = (mt, full)
    return best[1] if best else None


def _find_pdf(reg, args: dict) -> Path | None:  # noqa: ANN001
    key = (args.get("path"), args.get("query"))
    cache = reg.__dict__.setdefault("_pdf_cache", {})
    hit = cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    roots, found = _roots(reg), None
    if args.get("path"):
        raw = Path(str(args["path"])).expanduser()
        for c in ([raw] if raw.is_absolute() else [r / raw for r in roots]):
            try:
                c = c.resolve()
            except OSError:
                continue
            if c.suffix.lower() == ".pdf" and c.is_file() and _inside(c, roots):
                found = c
                break
        if found is None:
            found = _search_pdf(roots, [raw.name.lower()], exact=True)
    else:
        words = re.findall(r"[a-z0-9]+", str(args.get("query", "")).lower())
        found = _search_pdf(roots, words, exact=False) if words else None
    cache[key] = (time.time() + 30, found)
    return found


def _fetch_feed(url: str, limit: int) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "OMNI/1.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:  # allowlisted BBC feeds only
        root = ET.fromstring(resp.read())
    items = []
    for it in root.iter("item"):
        items.append({"title": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                      "date": (it.findtext("pubDate") or "").strip()})
        if len(items) >= limit:
            break
    return items


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _status_text(st: dict, top: list[dict]) -> str:
    lines = [f"CPU {st['cpu']:.0f}% | load {st['load1']:.2f} on {st['ncpu']} cores | RAM {st['mem']:.0f}% | swap {st['swap']:.0f}% | disk {st['disk']:.0f}%"]
    if st.get("battery") is not None:
        lines.append(f"Battery {st['battery']:.0f}% ({'charging' if st.get('plugged') else 'on battery'})")
    lines.append(f"Pressure (PSI avg10): cpu {st['psi_cpu']:.1f} | mem {st['psi_mem']:.1f} | io {st['psi_io']:.1f}")
    if top:
        lines.append("Top: " + ", ".join(f"{r['name']} {r['cpu']:.0f}%" for r in top))
    return "\n".join(lines)


# ----------------------------------------------------------------- validate
def validate(reg, action: str, args: dict) -> None:  # noqa: ANN001
    def text(key: str, lo: int = 1, hi: int = 200) -> str:
        v = args.get(key)
        if not isinstance(v, str) or not lo <= len(v.strip()) <= hi:
            raise ValueError(f"{key} must be a string of {lo}-{hi} characters.")
        return v

    if action == "pdf.open":
        if not (args.get("path") or args.get("query")):
            raise ValueError("pdf.open needs 'path' or 'query'.")
        if args.get("viewer", "default") not in {"default", "browser"}:
            raise ValueError("viewer must be default or browser.")
        if _find_pdf(reg, args) is None:
            raise ValueError("PDF not found under home or workspace.")
    elif action == "calendar.add":
        text("title")
        _parse_dt(args.get("start"))
        mins = args.get("minutes", 60)
        if not isinstance(mins, int) or isinstance(mins, bool) or not 5 <= mins <= 1440:
            raise ValueError("minutes must be 5-1440.")
        if not isinstance(args.get("location", ""), str) or len(args.get("location", "")) > 200:
            raise ValueError("location too long.")
    elif action == "alarm.set":
        text("label", 1, 100)
        if not 1 <= _alarm_delay(args) <= 7 * 86400:
            raise ValueError("alarm must be between 1 second and 7 days from now.")
    elif action == "notify.send":
        text("title", 1, 100)
        text("body", 1, 300)
    elif action == "notify.dnd":
        if not isinstance(args.get("enabled"), bool):
            raise ValueError("enabled must be true or false.")
    elif action == "settings.set":
        if args.get("key") not in SETTINGS or not isinstance(args.get("value"), bool):
            raise ValueError(f"key must be one of {sorted(SETTINGS)} with a boolean value.")
    elif action == "news.fetch":
        if args.get("topic", "world") not in NEWS_FEEDS:
            raise ValueError(f"topic must be one of {sorted(NEWS_FEEDS)}.")
        limit = args.get("limit", 8)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 15:
            raise ValueError("limit must be 1-15.")
    elif action == "web.search":
        text("query")
        if args.get("engine", "duckduckgo") not in {"duckduckgo", "google"}:
            raise ValueError("engine must be duckduckgo or google.")
    elif action == "window.minimize":
        reg._validate_target(args)
    elif action == "focus.only":
        if args.get("target"):
            reg._validate_target(args)


# ---------------------------------------------------------------------- run
def run(reg, action: str, spec, args: dict):  # noqa: ANN001
    R = lambda ok, out, data=None: reg._result(action, spec, ok, out, data)  # noqa: E731

    if action == "pdf.open":
        path = _find_pdf(reg, args)
        if path is None:
            return R(False, "PDF not found.")
        cmd = ["xdg-open", str(path)]
        if args.get("viewer") == "browser" and reg._which("firefox"):
            cmd = ["firefox", str(path)]
        return reg._spawn(action, spec, cmd, label=path.name)

    if action == "calendar.add":
        start = _parse_dt(args["start"])
        end = start + timedelta(minutes=int(args.get("minutes", 60)))
        uid = hashlib.sha1(f"{args['title']}{start.isoformat()}".encode()).hexdigest()[:16]
        fmt = "%Y%m%dT%H%M%S"
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//OMNI//Desktop Agent//EN", "BEGIN:VEVENT",
                 f"UID:{uid}@omni", f"DTSTAMP:{datetime.utcnow().strftime(fmt)}Z",
                 f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}",
                 f"SUMMARY:{_ics_escape(args['title'])}"]
        if args.get("location"):
            lines.append(f"LOCATION:{_ics_escape(args['location'])}")
        lines += ["BEGIN:VALARM", "TRIGGER:-PT10M", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "END:VALARM",
                  "END:VEVENT", "END:VCALENDAR"]
        folder = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "omni" / "events"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{uid}.ics"
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        reg._last_event = path
        res = reg._spawn(action, spec, ["xdg-open", str(path)], label=path.name)
        if not res.success:
            return res
        return R(True, f"event '{args['title']}' {start:%a %d %b %H:%M} saved to {path}; GNOME Calendar import dialog opened", {"path": str(path)})

    if action == "alarm.set":
        delay = max(1, int(_alarm_delay(args)))
        unit = f"omni-alarm-{secrets.token_hex(3)}"
        cmd = ["systemd-run", "--user", "--collect", f"--on-active={delay}s", f"--unit={unit}",
               "--description=OMNI alarm", sys.executable, "-m", "omni.desktop.alarm", args["label"]]
        res = reg._run(action, spec, cmd)
        if res.success:
            reg._last_alarm = unit
            return R(True, f"alarm '{args['label']}' fires in {delay}s ({unit})", {"unit": unit, "delay_s": delay})
        return res

    if action == "notify.send":
        return reg._run(action, spec, ["notify-send", "-a", "OMNI", args["title"], args["body"]])

    if action == "notify.dnd":
        res = reg._run(action, spec, [*_DND, "false" if args["enabled"] else "true"])
        if res.success:
            note = "Do-not-disturb ON: new banners suppressed." if args["enabled"] else "Do-not-disturb OFF."
            return R(True, note + " (GNOME exposes no public API to purge the existing notification list.)")
        return res

    if action == "settings.set":
        schema, gkey, enc = SETTINGS[args["key"]]
        return reg._run(action, spec, ["gsettings", "set", schema, gkey, enc(args["value"])])

    if action == "system.status":
        st = system_state()
        try:
            top = sorted(process_table(0.3), key=lambda r: r["cpu"], reverse=True)[:5]
        except Exception:  # noqa: BLE001 - psutil missing
            top = []
        return R(True, _status_text(st, top), {"state": st, "top": top})

    if action == "news.fetch":
        topic, limit = args.get("topic", "world"), int(args.get("limit", 8))
        try:
            items = _fetch_feed(NEWS_FEEDS[topic], limit)
        except (urllib.error.URLError, ET.ParseError, TimeoutError, OSError) as exc:
            return R(False, f"news fetch failed: {exc}")
        text = "\n".join(f"{i + 1}. {it['title']}" for i, it in enumerate(items))
        return R(bool(items), text or "no items", items)

    if action == "web.search":
        base = "https://www.google.com/search?q=" if args.get("engine") == "google" else "https://duckduckgo.com/?q="
        url = base + urllib.parse.quote_plus(args["query"])
        return reg._spawn(action, spec, ["xdg-open", url], label=url)

    if action == "window.minimize":
        address = reg._resolve_window(args)
        if address is None:
            return R(False, f"No window matches {args.get('target') or args.get('address')!r}.")
        try:
            return reg._run(action, spec, reg.backend.minimize_cmd(address))
        except NotImplementedError:
            return R(False, "minimise not supported by this backend.")

    if action == "focus.only":
        keep = reg._resolve_window({"target": args["target"]}) if args.get("target") else reg.active_window().get("address")
        if not keep:
            return R(False, "No window to keep focused.")
        minimised = 0
        for c in reg.clients():
            addr = reg._row(c)["address"]
            if addr.lower() == str(keep).lower():
                continue
            try:
                minimised += int(reg._run("window.minimize", spec, reg.backend.minimize_cmd(addr)).success)
            except NotImplementedError:
                return R(False, "minimise not supported by this backend.")
        if args.get("target"):
            reg._run(action, spec, reg.backend.focus_cmd(keep))
        if args.get("silence", True):
            reg._run(action, spec, [*_DND, "false"])
        reg._focus_kept = keep
        return R(True, f"focus kept on {keep}; minimised {minimised} other window(s); banners silenced")

    raise ValueError(f"Unsupported skill: {action}")


# ------------------------------------------------------------------- verify
def verify(reg, action: str, args: dict):  # noqa: ANN001
    if action == "alarm.set":
        unit, exe = getattr(reg, "_last_alarm", None), reg._which("systemctl")
        if not unit or not exe:
            return None
        out = reg.runner([exe, "--user", "list-timers", "--all", "--no-pager"], capture_output=True, text=True, timeout=5, check=False).stdout or ""
        return {"passed": unit in out, "detail": f"timer {unit} {'scheduled' if unit in out else 'missing'}"}
    if action == "calendar.add":
        p = getattr(reg, "_last_event", None)
        return {"passed": bool(p) and Path(p).exists(), "detail": f"event file {p}"}
    if action == "focus.only":
        keep, active = getattr(reg, "_focus_kept", None), reg.active_window().get("address")
        if not keep or not active:
            return None
        return {"passed": str(keep).lower() == str(active).lower(), "detail": f"active window {active}"}
    return None