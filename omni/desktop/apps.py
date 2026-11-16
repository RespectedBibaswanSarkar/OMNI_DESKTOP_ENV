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
    comment: str = ""
    path: str = ""           # full .desktop path (used with `gio launch`, which GNOME associates with the window)

    @property
    def stem(self) -> str:
        return self.desktop_id[:-8] if self.desktop_id.endswith(".desktop") else self.desktop_id

    def tokens(self) -> set[str]:
        text = " ".join([self.stem, self.name, self.generic, self.comment, *self.keywords])
        return set(_WORD.findall(text.lower()))

    def process_names(self) -> set[str]:
        """Names a running process of this app is likely to carry (never generic words from the display name)."""
        generic = {"org", "gnome", "desktop", "app", "com", "io", "net", "kde", "web", "browser", "editor", "text", "visual", "studio"}
        names = {self.stem.lower()} | {p for p in re.split(r"[_.\-]", self.stem.lower()) if len(p) >= 4 and p not in generic}
        if self.wm_class:
            names.add(self.wm_class.lower())
        try:
            exe = Path(self.command()[0]).name.lower()
            if exe and exe not in {"env", "sh", "bash", "flatpak", "snap", "gio"}:
                names.add(exe)
        except (ValueError, IndexError):
            pass
        return {n for n in names if len(n) >= 3 and n not in generic}

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
        comment=data.get("Comment", ""),
        path=str(path),
    )


FILLER = {"a", "an", "the", "my", "any", "some", "default", "one", "new", "please", "application", "app", "program", "software", "tool"}

# Standard freedesktop vocabulary (MIME / Categories), NOT app names: the *installed* default handler or
# best category match is chosen at run time, so it adapts to whatever the user has installed.
KIND_TABLE: dict[str, tuple[str | None, set[str]]] = {
    "text editor": ("text/plain", {"TextEditor"}),
    "editor": ("text/plain", {"TextEditor"}),
    "notepad": ("text/plain", {"TextEditor"}),
    "code editor": (None, {"IDE", "TextEditor", "Development"}),
    "ide": (None, {"IDE", "Development"}),
    "browser": ("x-scheme-handler/https", {"WebBrowser"}),
    "web browser": ("x-scheme-handler/https", {"WebBrowser"}),
    "file manager": ("inode/directory", {"FileManager"}),
    "files": ("inode/directory", {"FileManager"}),
    "terminal": (None, {"TerminalEmulator"}),
    "email": ("x-scheme-handler/mailto", {"Email"}),
    "mail": ("x-scheme-handler/mailto", {"Email"}),
    "music player": ("audio/mpeg", {"Audio", "Player"}),
    "audio player": ("audio/mpeg", {"Audio", "Player"}),
    "video player": ("video/mp4", {"Video", "Player"}),
    "media player": ("video/mp4", {"AudioVideo", "Player"}),
    "image viewer": ("image/png", {"Viewer", "Graphics"}),
    "pdf viewer": ("application/pdf", {"Viewer", "Office"}),
    "document viewer": ("application/pdf", {"Viewer", "Office"}),
    "calculator": (None, {"Calculator"}),
    "calendar": (None, {"Calendar"}),
    "spreadsheet": ("application/vnd.oasis.opendocument.spreadsheet", {"Spreadsheet"}),
    "presentation": ("application/vnd.oasis.opendocument.presentation", {"Presentation"}),
    "word processor": ("application/vnd.oasis.opendocument.text", {"WordProcessor"}),
    "settings": (None, {"Settings"}),
    "system monitor": (None, {"Monitor", "System"}),
}


def normalize_query(text: str) -> str:
    words = [w for w in _WORD.findall((text or "").lower()) if w not in FILLER]
    return " ".join(words)


class AppIndex:
    """Cached index of installed GUI applications with fuzzy name resolution."""

    def __init__(self, dirs: list[Path] | None = None, ttl: float = 300.0, mime_default=None, embedder=None) -> None:  # noqa: ANN001
        self.mime_default = mime_default          # callable(mime) -> desktop id | None   (gio mime)
        self.embedder = embedder                  # callable(list[str]) -> list[list[float]] | None
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

    def by_id(self, desktop_id: str) -> AppEntry | None:
        return next((e for e in self.entries() if e.desktop_id == desktop_id), None)

    def resolve(self, query: str) -> AppEntry | None:
        """name -> installed default for an app *type* -> category match -> semantic similarity."""
        q = normalize_query(query)
        if not q:
            return None
        return self._by_name(q, min_score=1.0) or self._by_kind(q) or self._by_name(q) or self._by_meaning(q)

    def _by_name(self, q: str, min_score: float = 0.0) -> AppEntry | None:
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
        return best[1] if best and best[0] >= min_score else None

    def _by_kind(self, q: str) -> AppEntry | None:
        qt = set(q.split())
        for kind, (mime, cats) in sorted(KIND_TABLE.items(), key=lambda kv: -len(kv[0])):
            kt = set(kind.split())
            if not (kt <= qt or (qt <= kt and len(qt) >= 1 and kind in {"browser", "editor", "terminal", "files", "calculator", "calendar", "email", "mail", "ide", "settings", "notepad"})):
                continue
            if mime and self.mime_default:
                entry = self.by_id(self.mime_default(mime) or "")
                if entry:
                    return entry
            scored = [(len(cats & set(e.categories)), -len(e.categories), e) for e in self.entries() if cats & set(e.categories)]
            if kind in {"code editor", "ide"}:      # prefer real code editors over generic TextEditor entries
                scored = [(sc + (2 if re.search(r"code|studio|ide|sublime|pycharm|vim|emacs|zed", e.name.lower()) else 0), tie, e) for sc, tie, e in scored]
            if scored:
                return max(scored, key=lambda t: (t[0], t[1]))[2]
        return None

    def _by_meaning(self, q: str) -> AppEntry | None:
        """Long tail: cosine similarity between the request and each app's name/generic name/comment/keywords."""
        entries = self.entries()
        if self.embedder and entries:
            docs = [f"{e.name}. {e.generic}. {e.comment}. {' '.join(e.keywords)}" for e in entries]
            try:
                vecs = self.embedder([q] + docs)
            except Exception:  # noqa: BLE001
                vecs = None
            if vecs and len(vecs) == len(docs) + 1:
                import numpy as np
                v = np.asarray(vecs, dtype=float)
                v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
                sims = v[1:] @ v[0]
                top = int(np.argmax(sims))
                if sims[top] >= 0.62:
                    return entries[top]
        qt = set(q.split())
        scored = []
        for e in entries:
            overlap = len(qt & e.tokens())
            if overlap:
                scored.append((overlap / len(qt | e.tokens()), e))
        if scored:
            best = max(scored, key=lambda t: t[0])
            if best[0] >= 0.2:
                return best[1]
        return None
