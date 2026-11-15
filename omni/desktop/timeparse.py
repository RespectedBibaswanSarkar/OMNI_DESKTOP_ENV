"""Deterministic time-phrase extraction (no model needed)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DUR = re.compile(r"\bfor\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?)\b", re.I)
_IN = re.compile(r"\bin\s+(\d{1,4})\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", re.I)
_REL = re.compile(r"\b(today|tomorrow|tonight)\b", re.I)
_WD = re.compile(r"\b(?:next\s+|on\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I)
_C12 = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_C24 = re.compile(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b")


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    return re.sub(r"^(?:at|on|for|to|in)\s+|\s+(?:at|on|for|to|in)$", "", text, flags=re.I).strip()


def extract_when(text: str, now: datetime | None = None):
    """Return (datetime | None, duration_minutes | None, remaining_text)."""
    base = now or datetime.now()
    spans: list[tuple[int, int]] = []
    dur = None
    m = _DUR.search(text)
    if m:
        n = int(m.group(1))
        dur = n * 60 if m.group(2).lower().startswith("h") else n
        spans.append(m.span())
    m = _IN.search(text)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()[0]
        delta = {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
                 "h": timedelta(hours=n), "d": timedelta(days=n)}[u]
        spans.append(m.span())
        return base + delta, dur, _cut(text, spans)
    day = None
    tonight = False
    m = _REL.search(text)
    if m:
        word = m.group(1).lower()
        tonight = word == "tonight"
        day = base.date() + (timedelta(days=1) if word == "tomorrow" else timedelta(0))
        spans.append(m.span())
    else:
        m = _WD.search(text)
        if m:
            ahead = (_DAYS.index(m.group(1).lower()) - base.weekday()) % 7 or 7
            day = base.date() + timedelta(days=ahead)
            spans.append(m.span())
    hh = mm = None
    m = _C12.search(text)
    if m and 1 <= int(m.group(1)) <= 12:
        hh = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        mm = int(m.group(2) or 0)
        spans.append(m.span())
    else:
        m = _C24.search(text)
        if m:
            hh, mm = int(m.group(1)), int(m.group(2))
            spans.append(m.span())
    if day is None and hh is None:
        return None, dur, _cut(text, spans)
    if hh is None:
        hh, mm = (20, 0) if tonight else (9, 0)
    if day is None:
        cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        day = cand.date() if cand > base else cand.date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hh, mm), dur, _cut(text, spans)