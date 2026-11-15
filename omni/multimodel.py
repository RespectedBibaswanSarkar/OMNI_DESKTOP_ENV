"""Role-based multi-model runtime on top of the local Ollama registry.

Roles (default model for the user's installed set):
  router   qwen3:1.7b          intent domain + IARS top-2 tie-break (cheap, fast)
  planner  qwen3:4b            JSON action plans (structured output)
  coder    qwen2.5-coder:3b    file/code bodies for write_file steps
  vision   gemma3:4b           screenshot grounding (images -> text)
  critic   phi4-mini           semantic post-condition check when no deterministic one exists
  embedder nomic-embed-text    semantic app/agent matching

Every role degrades gracefully: if the model is missing or the call fails the
caller gets ``None`` and the deterministic path continues.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from typing import Any

from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter

CODE_EXT = {".py", ".js", ".ts", ".sh", ".c", ".cpp", ".rs", ".go", ".java", ".html", ".css", ".qml", ".toml", ".yaml", ".yml"}
INTENT_SCHEMA = {
    "type": "object",
    "properties": {"domain": {"type": "string", "enum": ["desktop", "files", "code", "web", "planning", "chat"]},
                   "confidence": {"type": "number"}},
    "required": ["domain"],
}
CRITIC_SCHEMA = {"type": "object", "properties": {"satisfied": {"type": "boolean"}, "reason": {"type": "string"}},
                 "required": ["satisfied"]}


def _json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class MultiModelRuntime:
    def __init__(self, center: LocalModelCenter, llm: LLMClient, offline: bool = False) -> None:
        self.center = center
        self.llm = llm
        self.offline = offline

    # ------------------------------------------------------------ plumbing
    @property
    def live(self) -> bool:
        return not (self.offline or self.llm.is_mock)

    def model_for(self, role: str) -> str | None:
        if not self.center.models:
            self.center.refresh()
        sel = self.center.select(role, preferred=None)
        tag = sel.get("model")
        # `select` falls back to "anything text-capable"; vision must be a vision model
        if role == "vision" and tag and tag.split(":")[0] not in {"gemma3", "llava", "llama3.2-vision", "qwen2.5vl", "minicpm-v"}:
            return None
        return tag

    def _ask(self, role: str, prompt: str, *, fmt: Any = None, tokens: int = 128, images: list[str] | None = None) -> str | None:
        if not self.live:
            return None
        model = self.model_for(role)
        if not model:
            return None
        try:
            res = self.llm.complete(
                prompt, params={"num_predict": tokens, "temperature": 0.1}, model=model,
                fmt=fmt, think=False, images=images, use_cache=False,
            )
            self.center.record_result(model, res.elapsed_s, True)
            return res.text
        except Exception:  # noqa: BLE001
            self.center.record_result(model, 0.0, False)
            return None

    # --------------------------------------------------------------- roles
    def ask_router(self, prompt: str) -> str | None:
        return self._ask("router", prompt, fmt="json", tokens=64)

    def classify_domain(self, goal: str) -> dict | None:
        raw = self._ask("router", f'Classify this request. Request: {goal[:300]}\nJSON only.', fmt=INTENT_SCHEMA, tokens=48)
        return _json(raw or "")

    def refine_code(self, goal: str, path: str, draft: str) -> str | None:
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in CODE_EXT:
            return None
        raw = self._ask(
            "code",
            f"Write the complete contents of the file `{path}` for this goal: {goal[:300]}\n"
            f"A weaker draft was:\n{draft[:600]}\nReturn ONLY the file contents, no markdown fences.",
            tokens=700,
        )
        if not raw:
            return None
        raw = re.sub(r"^```[\w+-]*\n|\n```\s*$", "", raw.strip())
        return raw[:1500] if raw.strip() else None

    def describe_screen(self, goal: str) -> str | None:
        """Vision role: low-res screenshot -> short text grounding (Wayland grim)."""
        if not shutil.which("grim"):
            return None
        try:
            png = subprocess.run(["grim", "-s", "0.5", "-t", "png", "-"], capture_output=True, timeout=5, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        if not png:
            return None
        image = base64.b64encode(png).decode("ascii")
        return self._ask("vision", f"Describe the visible windows and any UI elements relevant to: {goal[:200]}. Max 60 words.",
                         tokens=120, images=[image])

    def critique(self, goal: str, observation: str) -> dict | None:
        raw = self._ask(
            "critic",
            f"Goal: {goal[:300]}\nObserved desktop state after acting: {observation[:600]}\n"
            'Is the goal satisfied? JSON {"satisfied": bool, "reason": str}',
            fmt=CRITIC_SCHEMA, tokens=96,
        )
        return _json(raw or "")

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.live or not self.center.models:
            return None
        tag = next((t for t in self.center.models if t.startswith("nomic-embed-text")), None)
        if tag is None:
            return None
        try:
            return self.llm.embed(texts, model=tag)
        except Exception:  # noqa: BLE001
            return None
