from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_state_path() -> Path:
    return Path(os.environ.get("OMNI_HUD_STATE", Path.home() / ".cache" / "omni" / "hud" / "state.json"))


def write_hud_state(path: str | Path | None, payload: dict[str, Any]) -> Path:
    target = Path(path) if path else default_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target
