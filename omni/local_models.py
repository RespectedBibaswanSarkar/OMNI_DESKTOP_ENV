"""Local model infrastructure: Ollama discovery, registry, capability-aware selection.

Only the local Ollama service (OMNI_OLLAMA_URL, default http://127.0.0.1:11434)
is used. No cloud API keys. Models are discovered live from /api/tags; alias
tags pointing at the same underlying model (identical digest, or the same base
name) are deduplicated with their underlying identity recorded.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_OLLAMA_URL = os.environ.get("OMNI_OLLAMA_URL", "http://127.0.0.1:11434")

# Suggested responsibility per model family. These are design-time preferences
# used for capability matching, not claims of measured superiority.
ROLE_PREFERENCES: tuple[tuple[str, str, str], ...] = (
    # (base-name prefix, task kind, role label)
    ("qwen3:1.7b", "intent", "Fast intent classification and lightweight tasks"),
    ("qwen3:4b", "reasoning", "General reasoning, planning, orchestration"),
    ("qwen3:4b", "planning", "General reasoning, planning, orchestration"),
    ("phi4-mini", "reasoning", "Reasoning and analytical tasks"),
    ("qwen2.5-coder", "code", "Code generation and code analysis"),
    ("deepseek-r1", "reasoning-compare", "Lightweight reasoning comparison"),
    ("gemma3:4b", "general", "General tasks and image-capable workflows"),
    ("llama3.2:3b", "general-alt", "General-purpose alternative"),
    ("nomic-embed-text", "embedding", "Embeddings and semantic retrieval"),
)

# task kind -> preference order of base-name prefixes (first available wins).
KIND_PREFERENCE: dict[str, tuple[str, ...]] = {
    "intent": ("qwen3:1.7b", "llama3.2:3b", "qwen3:4b", "gemma3:4b", "phi4-mini"),
    "reasoning": ("qwen3:4b", "phi4-mini", "gemma3:4b", "llama3.2:3b", "qwen3:1.7b"),
    "planning": ("qwen3:4b", "phi4-mini", "gemma3:4b", "llama3.2:3b"),
    "code": ("qwen2.5-coder:3b", "qwen3:4b", "llama3.2:3b", "qwen3:1.7b"),
    "embedding": ("nomic-embed-text:latest",),
    "reasoning-compare": ("deepseek-r1:1.5b", "phi4-mini", "qwen3:4b"),
    "general": ("gemma3:4b", "llama3.2:3b", "qwen3:4b", "qwen3:1.7b", "phi4-mini"),
}


@dataclass
class ModelInfo:
    tag: str                     # e.g. "qwen3:4b"
    base: str                    # e.g. "qwen3"
    digest: str
    family: str
    parameter_size: str
    quantization: str
    context_length: int | None
    capabilities: tuple[str, ...]
    size_gb: float
    aliases: tuple[str, ...] = ()
    role: str = ""
    observed_latency_s: float | None = None
    calls: int = 0
    loaded: bool = False

    @property
    def model_id(self) -> str:
        return f"ollama/{self.tag}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "base": self.base,
            "model_id": self.model_id,
            "digest": self.digest[:12],
            "family": self.family,
            "parameters": self.parameter_size,
            "quantization": self.quantization,
            "context_length": self.context_length,
            "capabilities": list(self.capabilities),
            "size_gb": round(self.size_gb, 2),
            "aliases": list(self.aliases),
            "role": self.role,
            "observed_latency_s": self.observed_latency_s,
            "calls": self.calls,
            "loaded": self.loaded,
        }


class LocalModelCenter:
    """Discovers installed Ollama models and matches tasks to capabilities."""

    def __init__(self, url: str | None = None, timeout: float = 5.0) -> None:
        self.url = (url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.timeout = float(timeout)
        self.models: dict[str, ModelInfo] = {}
        self.reachable: bool | None = None
        self.last_error: str | None = None
        self.last_refresh: float = 0.0
        self.selected: str | None = None       # manual override
        self.auto_select: bool = True
        self.preferred: str | None = None

    # ------------------------------------------------------------------ HTTP
    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(self.url + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def check(self) -> bool:
        try:
            self._get("/api/version")
            return True
        except Exception as exc:  # noqa: BLE001 - connectivity is the signal
            self.reachable = False
            self.last_error = f"Ollama unreachable: {exc}"
            return False

    # ------------------------------------------------------------- discovery
    def refresh(self) -> dict[str, Any]:
        self.last_refresh = time.time()
        try:
            data = self._get("/api/tags")
        except Exception as exc:  # noqa: BLE001
            self.reachable = False
            self.last_error = f"Ollama unreachable: {exc}"
            return {"reachable": False, "error": self.last_error, "models": []}
        self.reachable = True
        self.last_error = None
        raw = data.get("models", [])
        # Deduplicate: same digest -> alias of the first entry; same base name
        # with different quantisations stays distinct but cross-referenced.
        by_digest: dict[str, ModelInfo] = {}
        by_base: dict[str, ModelInfo] = {}
        models: dict[str, ModelInfo] = {}
        for entry in raw:
            tag = str(entry.get("name", entry.get("model", "")))
            if not tag:
                continue
            details = entry.get("details", {}) or {}
            base = tag.split(":")[0]
            info = ModelInfo(
                tag=tag,
                base=base,
                digest=str(entry.get("digest", "")),
                family=str(details.get("family", "unknown")),
                parameter_size=str(details.get("parameter_size", "?")),
                quantization=str(details.get("quantization_level", "?")),
                context_length=(details.get("context_length") if isinstance(details.get("context_length"), int) else None),
                capabilities=tuple(entry.get("capabilities", [])) or ("completion",),
                size_gb=float(entry.get("size", 0)) / 2**30,
            )
            info.role = self._role_for(info)
            twin = by_digest.get(info.digest)
            if twin is not None and twin.tag != info.tag:
                # identical weights under a different tag: true alias
                twin.aliases = tuple(sorted(set(twin.aliases) | {info.tag}))
                continue
            by_digest.setdefault(info.digest, info)
            by_base.setdefault(base, info)
            models[info.tag] = info
        self.models = models
        # Loaded models from /api/ps (best effort)
        try:
            ps = self._get("/api/ps")
            loaded = {m.get("name", m.get("model", "")) for m in ps.get("models", [])}
            for info in self.models.values():
                info.loaded = info.tag in loaded or any(a in loaded for a in info.aliases)
        except Exception:  # noqa: BLE001
            pass
        return {"reachable": True, "models": [m.as_dict() for m in self._ordered()]}

    def _ordered(self) -> list[ModelInfo]:
        return sorted(self.models.values(), key=lambda m: (m.tag not in {"qwen3:4b", "qwen3:1.7b"}, m.size_gb))

    def _role_for(self, info: ModelInfo) -> str:
        tags = {info.tag, *info.aliases}
        for prefix, _kind, role in ROLE_PREFERENCES:
            if any(t == prefix or t.startswith(prefix) for t in tags):
                return role
        return "General local model"

    # ------------------------------------------------------------- selection
    def _fallback_chain(self, tag: str) -> list[str]:
        base = tag.split(":")[0]
        for kind, order in KIND_PREFERENCE.items():
            if base in order:
                rest = [t for t in order if t != base]
                chain = [tag]
                for other in rest:
                    for info in self._ordered():
                        if info.base == other.split(":")[0]:
                            chain.append(info.tag)
                return chain
        return [tag] + [info.tag for info in self._ordered() if info.tag != tag]

    def select(self, task_kind: str, preferred: str | None = None) -> dict[str, Any]:
        """Capability-aware selection with observed-latency tie-breaking."""
        if not self.models:
            self.refresh()
        if not self.models:
            return {"model": None, "reason": "No local models discovered (Ollama unavailable)", "chain": []}
        manual = preferred or (self.selected if self.selected else None)
        chain: list[str] = []
        if manual and manual in self.models:
            chain = [manual]
            reason = f"manual selection: {manual}"
        else:
            base_pref = KIND_PREFERENCE.get(task_kind or "general", KIND_PREFERENCE["general"])
            if self.preferred and self.preferred in self.models:
                chain.append(self.preferred)
            def matches(want: str) -> list[str]:
                if want in self.models:
                    return [want]
                base = want.split(":")[0]
                return [info.tag for info in self._ordered() if info.base == base]

            candidates = [self.models[t] for want in base_pref for t in matches(want) if t in self.models]
            # Observed latency breaks ties among equal-preference models and
            # orders the remainder of the registry as last-resort fallback.
            extras = [i for i in self._ordered() if i not in candidates and "embedding" not in i.capabilities]
            extras.sort(key=lambda i: i.observed_latency_s if i.observed_latency_s is not None else 999.0)
            chain.extend(info.tag for info in candidates)
            chain.extend(info.tag for info in extras)
            reason = f"capability match for task kind '{task_kind}' (preference: {' > '.join(base_pref)})"
        chain = [tag for i, tag in enumerate(chain) if tag in self.models and tag not in chain[:i]]
        for tag in chain:
            info = self.models[tag]
            if "embedding" in info.capabilities and task_kind != "embedding":
                continue
            return {"model": tag, "reason": reason, "chain": chain}
        return {"model": None, "reason": "No text-capable model available", "chain": chain}

    def record_result(self, tag: str, elapsed_s: float, ok: bool) -> None:
        info = self.models.get(tag)
        if info is None:
            return
        info.calls += 1
        if ok:
            prev = info.observed_latency_s
            info.observed_latency_s = elapsed_s if prev is None else round(0.5 * prev + 0.5 * elapsed_s, 3)

    def status(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "reachable": self.reachable,
            "error": self.last_error,
            "last_refresh": self.last_refresh,
            "selected": self.selected,
            "auto_select": self.auto_select,
            "preferred": self.preferred,
            "models": [m.as_dict() for m in self._ordered()],
        }
