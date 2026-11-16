"""Deterministic fast-path intent parser for common desktop commands.

Zero model latency, zero hallucination for the well-formed cases; anything it
cannot fully parse returns ``None`` and the multi-model planner takes over.
Every emitted step is a registry action, so the registry still validates it.
"""
from __future__ import annotations

import re

from omni.desktop.apps import normalize_query

_SPLIT = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b)\s+", re.I)
_URL = re.compile(r"^(?:go to|open|visit|browse to)\s+(https?://\S+|(?:www\.)?[\w-]+(?:\.[\w-]+)+\S*)$", re.I)
_WORKSPACE = re.compile(r"^(?:go to|switch to|goto|move to|show)?\s*workspace\s+(\d{1,2})$", re.I)
_MOVE = re.compile(r"^move\s+(?P<t>.+?)\s+to\s+workspace\s+(?P<ws>\d{1,2})$", re.I)
_LAUNCH = re.compile(r"^(?:please\s+)?(?:open|launch|start|run)\s+(?P<app>.+?)(?:\s+(?:app|application))?$", re.I)
_FOCUS = re.compile(r"^(?:focus|switch to|go to|show)\s+(?P<t>.+?)(?:\s+window)?$", re.I)
_CLOSE = re.compile(r"^close\s+(?:the\s+)?(?P<t>.+?)(?:\s+window)?$", re.I)
_VOLUME = re.compile(r"^(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?$", re.I)
_BRIGHT = re.compile(r"^(?:set\s+)?(?:the\s+)?brightness\s+(?:to\s+)?(\d{1,3})\s*%?$", re.I)
_WIFI = re.compile(r"^(?:turn|switch)\s+(on|off)\s+wi-?fi$|^wi-?fi\s+(on|off)$", re.I)
_MEDIA = re.compile(r"^(play|pause|play/pause|resume|next|skip|previous|prev)(?:\s+(?:the\s+)?(?:track|song|music|media))?$", re.I)
_TYPE = re.compile(r"^type\s+[\"“'](?P<text>.+)[\"”']$", re.I)
_SAVE = re.compile(r"^save\s+(?:this|that|it|the last (?:plan|task))?\s*as\s+(?:a\s+|an\s+|my\s+)?(?:routine\s+)?(?:called\s+|named\s+)?(?P<n>[\w][\w \-]{0,39})$", re.I)
_KEY = re.compile(r"^press\s+(?P<key>[\w+]+)$", re.I)

_MEDIA_MAP = {"play": "play-pause", "pause": "play-pause", "play/pause": "play-pause", "resume": "play-pause",
              "next": "next", "skip": "next", "previous": "previous", "prev": "previous"}


def _clause(text: str) -> dict | None:
    t = text.strip().rstrip(".!?")
    if not t:
        return None
    if m := _SAVE.match(t):
        return {"action": "routine.save", "args": {"name": m.group("n").strip()}}
    if m := _TYPE.match(t):
        return {"action": "text.type", "args": {"text": m.group("text")}}
    if m := _KEY.match(t):
        return {"action": "key.press", "args": {"key": m.group("key")}}
    if m := _MOVE.match(t):
        return {"action": "window.move", "args": {"target": m.group("t"), "workspace": int(m.group("ws"))}}
    if m := _WORKSPACE.match(t):
        return {"action": "workspace.switch", "args": {"workspace": int(m.group(1))}}
    if m := _VOLUME.match(t):
        return {"action": "volume.set", "args": {"percent": min(int(m.group(1)), 100)}}
    if t.lower() in {"mute", "mute volume"}:
        return {"action": "volume.set", "args": {"percent": 0}}
    if m := _BRIGHT.match(t):
        return {"action": "brightness.set", "args": {"percent": min(int(m.group(1)), 100)}}
    if m := _WIFI.match(t):
        return {"action": "wifi.set", "args": {"enabled": (m.group(1) or m.group(2)).lower() == "on"}}
    if m := _MEDIA.match(t):
        return {"action": "media.control", "args": {"command": _MEDIA_MAP[m.group(1).lower()]}}
    if m := _CLOSE.match(t):
        return {"action": "window.close", "args": {"target": m.group("t")}}
    if m := _URL.match(t):
        url = m.group(1)
        return {"action": "url.open", "args": {"url": url if url.lower().startswith("http") else f"https://{url}"}}
    if m := _LAUNCH.match(t):
        return {"action": "app.launch", "args": {"app": normalize_query(m.group("app")) or m.group("app")}}
    if m := _FOCUS.match(t):
        return {"action": "window.focus", "args": {"target": m.group("t")}}
    return None


def parse_desktop_intent(goal: str) -> list[dict] | None:
    """Return registry steps for ``goal`` or ``None`` when any clause is unparseable."""
    goal = (goal or "").strip()
    if not goal:
        return None
    # a quoted string may contain separators; protect it from splitting
    if _TYPE.match(goal) or _SAVE.match(goal):
        clauses = [goal]
    else:
        clauses = [c for c in _SPLIT.split(goal) if c.strip()]
    steps = []
    for clause in clauses:
        step = _clause(clause)
        if step is None:
            return None
        steps.append(step)
    return steps[:6] or None
