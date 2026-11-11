from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from omni.context.lexicon import LEXICON_INDEX

_APP_BUCKETS = 32
_TEXT_BUCKETS = len(LEXICON_INDEX)
_ROLE_BUCKETS = 16
_ACTION_BUCKETS = 8
FEATURE_DIM = 7 + _APP_BUCKETS + _TEXT_BUCKETS + _ROLE_BUCKETS + _ACTION_BUCKETS + 3
TEXT_START = 7 + _APP_BUCKETS
_WORD = re.compile(r"[a-z0-9]+")


def _bucket(text: str, buckets: int, salt: str = "") -> tuple[int, float]:
    digest = hashlib.blake2b((salt + str(text)).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest[:4], "little") % buckets, (1.0 if digest[4] & 1 else -1.0)


def text_embedding(text: str) -> np.ndarray:
    """Collision-free lexical embedding over the curated capability vocabulary."""
    vec = np.zeros(_TEXT_BUCKETS, dtype=np.float64)
    for token in _WORD.findall((text or "").lower()):
        idx = LEXICON_INDEX.get(token)
        if idx is not None:
            vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    if norm:
        vec /= norm
    return vec


def prior_vector(text: str, dim: int = FEATURE_DIM) -> np.ndarray:
    """Embed an agent capability description into the shared context-text segment."""
    phi = np.zeros(dim, dtype=np.float64)
    phi[TEXT_START : TEXT_START + _TEXT_BUCKETS] = text_embedding(text)
    return phi


@dataclass(frozen=True)
class ContextSnapshot:
    timestamp: float
    app_class: str = ""
    title: str = ""
    workspace_id: int = 0
    workspace_name: str = ""
    client_count: int = 0
    monitor_count: int = 0
    focused_age_s: float = 0.0
    clipboard_types: tuple[str, ...] = ()
    atspi_roles: dict[str, int] = field(default_factory=dict)
    atspi_available: bool = False
    screenshot: dict[str, float] = field(default_factory=dict)
    recent_events: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "app_class": self.app_class,
            "title": self.title,
            "workspace_id": self.workspace_id,
            "workspace_name": self.workspace_name,
            "client_count": self.client_count,
            "monitor_count": self.monitor_count,
            "focused_age_s": round(self.focused_age_s, 3),
            "clipboard_types": list(self.clipboard_types),
            "atspi_available": self.atspi_available,
            "atspi_role_count": len(self.atspi_roles),
            "screenshot_available": bool(self.screenshot),
        }

    def privacy_summary(self) -> dict[str, Any]:
        """Aggregates only: never clipboard contents, pixels, or keystrokes."""
        return {
            "raw_keys_stored": False,
            "clipboard_content_stored": False,
            "screenshot_stored": False,
            "app_class": self.app_class,
            "clipboard_type_count": len(self.clipboard_types),
            "event_count": len(self.recent_events),
        }


def build_features(snapshot: ContextSnapshot, action_counts: dict[str, int] | None = None, now: float | None = None) -> np.ndarray:
    """Map a context snapshot to the fixed IARS feature vector phi(z)."""
    phi = np.zeros(FEATURE_DIM, dtype=np.float64)
    phi[0] = 1.0
    phi[1] = math.log1p(max(snapshot.focused_age_s, 0.0))
    phi[2] = math.log1p(max(snapshot.client_count, 0))
    phi[3] = float(snapshot.workspace_id % 10) / 10.0
    types = {t.lower() for t in snapshot.clipboard_types}
    phi[4] = 1.0 if any("text" in t or "utf8" in t or "string" in t for t in types) else 0.0
    phi[5] = 1.0 if any("image" in t or "png" in t for t in types) else 0.0
    phi[6] = 1.0 if any("file" in t or "uri" in t for t in types) else 0.0

    base = 7
    idx, _ = _bucket(snapshot.app_class or "unknown", _APP_BUCKETS, "app:")
    phi[base + idx] = 1.0
    base += _APP_BUCKETS

    # Only app class + title: ubiquitous AT-SPI role names ("application",
    # "frame") would otherwise leak into every context and skew agent priors.
    text = " ".join([snapshot.app_class or "", snapshot.title or ""])
    phi[base : base + _TEXT_BUCKETS] = text_embedding(text)
    base += _TEXT_BUCKETS

    role_vec = np.zeros(_ROLE_BUCKETS, dtype=np.float64)
    for role, count in snapshot.atspi_roles.items():
        idx, sign = _bucket(role, _ROLE_BUCKETS, "role:")
        role_vec[idx] += sign * float(count)
    norm = np.linalg.norm(role_vec)
    if norm:
        role_vec /= norm
    phi[base : base + _ROLE_BUCKETS] = role_vec
    base += _ROLE_BUCKETS

    for action, count in (action_counts or {}).items():
        idx, sign = _bucket(action, _ACTION_BUCKETS, "action:")
        phi[base + idx] += sign * math.log1p(max(count, 0))
    base += _ACTION_BUCKETS

    phi[base] = float(snapshot.screenshot.get("brightness", 0.0))
    phi[base + 1] = float(snapshot.screenshot.get("edge_density", 0.0))
    phi[base + 2] = 1.0 if snapshot.screenshot else 0.0
    assert base + 3 == FEATURE_DIM
    return phi


def synthetic_snapshot(app_class: str = "code", title: str = "", **kwargs: Any) -> ContextSnapshot:
    return ContextSnapshot(timestamp=kwargs.pop("timestamp", time.time()), app_class=app_class, title=title, **kwargs)
