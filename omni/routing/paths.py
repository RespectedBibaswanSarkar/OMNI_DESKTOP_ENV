"""IARS-Path: Thompson sampling over application paths with a cost penalty."""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np

BBC = {"world": "https://www.bbc.com/news/world", "technology": "https://www.bbc.com/news/technology",
       "science": "https://www.bbc.com/news/science_and_environment", "business": "https://www.bbc.com/news/business",
       "india": "https://www.bbc.com/news/world/asia/india"}


@dataclass(frozen=True)
class PathOption:
    id: str
    desc: str
    build: Callable[[dict], dict]
    est_steps: float = 1.0
    est_gui: float = 0.0
    needs: tuple = ()


def _gcal(a: dict) -> dict:
    try:
        start = datetime.strptime(a["start"], "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=int(a.get("minutes", 60)))
        url = ("https://calendar.google.com/calendar/render?action=TEMPLATE&text=" + urllib.parse.quote_plus(a["title"])
               + "&dates=" + start.strftime("%Y%m%dT%H%M%S") + "/" + end.strftime("%Y%m%dT%H%M%S"))
    except (KeyError, ValueError):
        url = "https://calendar.google.com"
    return {"action": "url.open", "args": {"url": url}}


CATALOG: dict[str, list[PathOption]] = {
    "news.fetch": [
        PathOption("inline-rss", "fetch RSS headlines directly, no GUI", lambda a: {"action": "news.fetch", "args": a}, 1, 0),
        PathOption("browser-bbc", "open the BBC news page in the browser",
                   lambda a: {"action": "url.open", "args": {"url": BBC.get(a.get("topic", "world"), BBC["world"])}}, 1, 1, ("xdg-open",)),
    ],
    "pdf.open": [
        PathOption("default-viewer", "open in the default PDF viewer", lambda a: {"action": "pdf.open", "args": {**a, "viewer": "default"}}, 1, 1),
        PathOption("browser-viewer", "open the PDF inside Firefox", lambda a: {"action": "pdf.open", "args": {**a, "viewer": "browser"}}, 1, 1, ("firefox",)),
    ],
    "calendar.add": [
        PathOption("ics-gnome-calendar", "write an .ics file and import into GNOME Calendar", lambda a: {"action": "calendar.add", "args": a}, 2, 1),
        PathOption("google-calendar-web", "open a prefilled Google Calendar event page (needs login)", _gcal, 1, 2, ("xdg-open",)),
    ],
    "system.status": [
        PathOption("inline-psutil", "read CPU/RAM/battery directly", lambda a: {"action": "system.status", "args": a}, 1, 0),
        PathOption("system-monitor", "launch the GNOME System Monitor window",
                   lambda a: {"action": "app.launch", "args": {"app": "System Monitor"}}, 1, 2),
    ],
    "web.search": [
        PathOption("duckduckgo", "search with DuckDuckGo", lambda a: {"action": "web.search", "args": {**a, "engine": "duckduckgo"}}, 1, 1),
        PathOption("google", "search with Google", lambda a: {"action": "web.search", "args": {**a, "engine": "google"}}, 1, 1),
    ],
}


class PathBandit:
    def __init__(self, lam: float = 0.35, steps_w: float = 0.05, gui_w: float = 0.10, conf_w: float = 0.08,
                 t_ref: float = 5.0, tie_margin: float = 0.03, seed: int = 21) -> None:
        self.lam, self.steps_w, self.gui_w, self.conf_w = lam, steps_w, gui_w, conf_w
        self.t_ref, self.tie_margin = t_ref, tie_margin
        self.rng = np.random.default_rng(seed)
        self.stats: dict[str, dict[str, Any]] = {}

    def _st(self, key: str) -> dict[str, Any]:
        return self.stats.setdefault(key, {"a": 1.0, "b": 1.0, "n": 0, "cost": None})

    def choose(self, step: dict, feasible: Callable[[dict, PathOption], bool], tiebreak=None):  # noqa: ANN001
        base = step["action"]
        options = CATALOG.get(base)
        if not options:
            return step, None
        cands = []
        for opt in options:
            cand = opt.build(dict(step.get("args", {})))
            if feasible(cand, opt):
                cands.append((opt, cand))
        if not cands:
            return step, None
        if len(cands) == 1:
            return cands[0][1], None
        scores: dict[str, float] = {}
        for opt, _ in cands:
            st = self._st(f"{base}|{opt.id}")
            theta = float(self.rng.beta(st["a"], st["b"]))
            cost = st["cost"] if st["cost"] is not None else self.steps_w * opt.est_steps + self.gui_w * opt.est_gui
            scores[opt.id] = theta - self.lam * cost
        ranked = sorted(scores, key=scores.get, reverse=True)
        pick = ranked[0]
        if tiebreak and scores[ranked[0]] - scores[ranked[1]] < self.tie_margin:
            alt = tiebreak([o for o, _ in cands if o.id in ranked[:2]])
            if alt in ranked[:2]:
                pick = alt
        chosen = next(c for o, c in cands if o.id == pick)
        return chosen, {"action": base, "path": pick, "scores": {k: round(v, 4) for k, v in scores.items()},
                        "candidates": [o.id for o, _ in cands]}

    def observe(self, info: dict, success: bool, elapsed_s: float, confirmations: int = 0) -> None:
        st = self._st(f"{info['action']}|{info['path']}")
        st["n"] += 1
        st["a"] += 1.0 if success else 0.0
        st["b"] += 0.0 if success else 1.0
        c = 0.5 * min(elapsed_s / self.t_ref, 2.0) + self.conf_w * confirmations
        st["cost"] = c if st["cost"] is None else 0.7 * st["cost"] + 0.3 * c

    def as_dict(self) -> dict[str, Any]:
        return {k: {"n": v["n"], "p_success": round(v["a"] / (v["a"] + v["b"]), 4),
                    "cost": None if v["cost"] is None else round(v["cost"], 4)} for k, v in sorted(self.stats.items())}