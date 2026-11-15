"""Dynamic application discovery from freedesktop ``.desktop`` entries.

Replaces the hard-coded 4-app allowlist.  The safety model is unchanged in
spirit: only applications that are *installed and advertised by the desktop*
can be launched, and no free-form command string ever reaches a shell.
"""
from __future__ import annotations

import difflib
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class AppEntry:
    desktop_id: str          # "firefox.desktop"
    name: str                # "Firefox Web Browser"
    exec: str                # raw Exec= line
    wm_class: str = ""       # StartupWMClass (used to match Hyprland windows)
    generic: str = ""        # GenericName, e.g. "Web Browser"
    keywords: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    path: str = ""           # full .desktop path (used with `gio launch`, which GNOME associates with the window)

    @property
    def stem(self) -> str:
        return self.desktop_id[:-8] if self.desktop_id.endswith(".desktop") else self.desktop_id

    def tokens(self) -> set[str]:
        text = " ".join([self.stem, self.name, self.generic, *self.keywords])
        return set(_WORD.findall(text.lower()))

    def window_classes(self) -> set[str]:
        out = {self.stem.lower(), self.name.lower()}
        if self.wm_class:
            out.add(self.wm_class.lower())
        try:
            exe = Path(self.command()[0]).name.lower()
            if exe and exe not in {"env", "sh", "bash", "flatpak", "snap", "gio"}:
                out.add(exe)
        except (ValueError, IndexError):
            pass
        return out

    def command(self) -> list[str]:
        """Exec= with field codes stripped, as an argv list (no shell)."""
        cleaned = _FIELD_CODES.sub("", self.exec).replace("%%", "%")
        return shlex.split(cleaned)


def default_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    dirs = [home / "applications"]
    for base in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if base:
            dirs.append(Path(base) / "applications")
    dirs += [
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    seen: set[Path] = set()
    out = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_desktop_file(path: Path) -> AppEntry | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    data: dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if "[" in key:                      # localized key, e.g. Name[de]
            continue
        data.setdefault(key.strip(), value.strip())
    if data.get("Type", "Application") != "Application":
        return None
    if data.get("NoDisplay", "").lower() == "true" or data.get("Hidden", "").lower() == "true":
        return None
    if not data.get("Exec") or not data.get("Name"):
        return None
    split = lambda s: tuple(x for x in s.split(";") if x)  # noqa: E731
    return AppEntry(
        desktop_id=path.name,
        name=data["Name"],
        exec=data["Exec"],
        wm_class=data.get("StartupWMClass", ""),
        generic=data.get("GenericName", ""),
        keywords=split(data.get("Keywords", "")),
        categories=split(data.get("Categories", "")),
        path=str(path),
    )


class AppIndex:
    """Cached index of installed GUI applications with fuzzy name resolution."""

    def __init__(self, dirs: list[Path] | None = None, ttl: float = 300.0) -> None:
        self.dirs = list(dirs) if dirs is not None else default_dirs()
        self.ttl = float(ttl)
        self._entries: list[AppEntry] = []
        self._loaded_at = 0.0

    def refresh(self) -> list[AppEntry]:
        found: dict[str, AppEntry] = {}
        for directory in self.dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.desktop")):
                if path.name in found:        # earlier dirs (user) win
                    continue
                entry = parse_desktop_file(path)
                if entry:
                    found[path.name] = entry
        self._entries = list(found.values())
        self._loaded_at = time.monotonic()
        return self._entries

    def entries(self) -> list[AppEntry]:
        if not self._entries or time.monotonic() - self._loaded_at > self.ttl:
            self.refresh()
        return self._entries

    def names(self) -> list[str]:
        return sorted({e.name for e in self.entries()})

    def resolve(self, query: str) -> AppEntry | None:
        q = " ".join(_WORD.findall((query or "").lower()))
        if not q:
            return None
        q_tokens = set(q.split())
        best: tuple[float, AppEntry] | None = None
        for entry in self.entries():
            name = " ".join(_WORD.findall(entry.name.lower()))
            stem = " ".join(_WORD.findall(entry.stem.lower()))
            score = 0.0
            if q in (name, stem):
                score = 1.0
            elif q_tokens and q_tokens <= entry.tokens():
                score = 0.85 + 0.1 * len(q_tokens) / max(len(entry.tokens()), 1)
            elif name.startswith(q) or stem.startswith(q):
                score = 0.8
            else:
                ratio = max(difflib.SequenceMatcher(None, q, name).ratio(), difflib.SequenceMatcher(None, q, stem).ratio())
                if ratio >= 0.78:
                    score = 0.7 * ratio
            if score and (best is None or score > best[0]):
                best = (score, entry)
        return best[1] if best else None
