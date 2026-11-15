"""Adaptive plan memory: OMNI learns from verified successes instead of relying on hard-coded commands.

* every VERIFIED plan is remembered under its normalised goal;
* a later goal with high lexical similarity (and identical numbers) replays it with zero LLM latency;
* a replay that fails verification is forgotten, so stale plans cannot persist;
* "save this as X" turns the last verified plan into a named routine ("run X" replays it).
Replayed steps are re-validated by the action registry and still pass the confirmation gate.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

STOP = {"the", "a", "an", "please", "my", "me", "to", "for", "and", "then", "it", "on", "in", "of", "can", "you", "could", "would", "now", "just", "i", "want"}
TRIGGERS = {"run", "start", "do", "launch", "open", "activate", "begin", "routine", "mode"}


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP]


def key_of(text: str) -> str:
    return " ".join(tokens(text))


class PlanMemory:
    def __init__(self, path: str | Path | None = None, min_similarity: float = 0.75, max_entries: int = 300) -> None:
        self.path = Path(path) if path else None
        self.min_similarity = float(min_similarity)
        self.max_entries = int(max_entries)
        self.entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = data if isinstance(data, dict) else {}
            except (OSError, json.JSONDecodeError):
                self.entries = {}

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.entries, fh, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # --------------------------------------------------------------- queries
    def match(self, goal: str, validate: Callable[[list], list] | None = None) -> dict[str, Any] | None:
        gt = set(tokens(goal))
        if not gt:
            return None
        gdigits = {t for t in gt if t.isdigit()}
        best: tuple[float, str] | None = None
        with self._lock:
            for k, e in self.entries.items():
                kt = set(k.split())
                if not kt:
                    continue
                if e.get("named"):
                    sim = 1.0 if kt <= gt and (gt - kt) <= TRIGGERS else 0.0
                else:
                    if {t for t in kt if t.isdigit()} != gdigits:
                        continue
                    sim = len(gt & kt) / len(gt | kt)
                if sim >= self.min_similarity and (best is None or sim > best[0] or (sim == best[0] and e.get("uses", 0) > self.entries[best[1]].get("uses", 0))):
                    best = (sim, k)
            if best is None:
                return None
            entry = self.entries[best[1]]
        steps = entry["steps"]
        if validate is not None:
            checked = validate(steps)
            if len(checked) != len(steps):
                return None
            steps = checked
        return {"steps": steps, "similarity": round(best[0], 3), "goal": entry["goal"], "uses": entry.get("uses", 0), "key": best[1]}

    def names(self) -> list[str]:
        return sorted(e["goal"] for e in self.entries.values() if e.get("named"))

    # -------------------------------------------------------------- updates
    def remember(self, goal: str, steps: list[dict], key: str | None = None) -> None:
        k = key or key_of(goal)
        if not k or not steps:
            return
        with self._lock:
            old = self.entries.get(k, {})
            self.entries[k] = {"goal": old.get("goal", goal), "steps": steps, "uses": old.get("uses", 0) + 1,
                               "named": bool(old.get("named")), "created": old.get("created", time.time()), "last_used": time.time()}
            if len(self.entries) > self.max_entries:
                for stale in sorted((x for x in self.entries if not self.entries[x].get("named")),
                                    key=lambda x: self.entries[x]["last_used"])[: len(self.entries) - self.max_entries]:
                    del self.entries[stale]
            self._save()

    def save_named(self, name: str, steps: list[dict]) -> bool:
        k = key_of(name)
        if not k or not steps:
            return False
        with self._lock:
            self.entries[k] = {"goal": name.strip(), "steps": steps, "uses": 0, "named": True, "created": time.time(), "last_used": time.time()}
            self._save()
        return True

    def penalize(self, key: str) -> None:
        with self._lock:
            entry = self.entries.get(key)
            if entry and not entry.get("named"):
                del self.entries[key]
                self._save()

    def forget(self, name: str) -> bool:
        with self._lock:
            removed = self.entries.pop(key_of(name), None) is not None
            if removed:
                self._save()
            return removed
