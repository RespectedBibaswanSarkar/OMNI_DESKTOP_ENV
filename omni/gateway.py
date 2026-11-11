from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from omni.model_registry import ModelRegistry, get_api_key, provider_headers
from omni.telemetry import JsonlLogger, mock_enabled

ROLES = ("planner", "fast_chat", "vision", "speech_to_text", "text_to_speech", "embeddings", "code")
DEFAULT_CAPABILITY = {
    "planner": {"modality": "text", "tools": True, "prefer_open_weights": False},
    "fast_chat": {"modality": "text", "tools": False, "prefer_open_weights": False},
    "vision": {"modality": "vision", "tools": False, "prefer_open_weights": False},
    "speech_to_text": {"modality": "audio", "tools": False, "prefer_open_weights": False},
    "text_to_speech": {"modality": "audio", "tools": False, "prefer_open_weights": False},
    "embeddings": {"modality": "text", "tools": False, "prefer_open_weights": False},
    "code": {"modality": "text", "tools": False, "prefer_open_weights": False},
}

# Base URLs for OpenAI-compatible chat/completions per provider (used when the
# provider exposes a chat endpoint; the model registry list endpoint is separate).
CHAT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "groq": "https://api.groq.com/openai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "huggingface": "https://router.huggingface.co/v1",
}


@dataclass(frozen=True)
class GatewayResult:
    text: str
    model: str
    provider: str
    role: str
    cached: bool
    elapsed_s: float
    mock: bool
    fallback: bool = False
    attempts: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "role": self.role,
            "cached": self.cached,
            "elapsed_s": round(self.elapsed_s, 4),
            "mock": self.mock,
            "fallback": self.fallback,
            "attempts": self.attempts,
            **self.extra,
        }


class ModelGateway:
    """Single entry point for every hosted-model call.

    Roles are routed independently to the best available model from the live
    registry (capability, cost, measured latency). On error / rate limit /
    timeout the call falls back to the next best model for the same role.
    API keys come from env vars or the system keyring and are never logged.
    All non-mock calls are logged to JSONL (text is not stored unless
    OMNI_DEBUG_FRAMES-style opt-in; prompts are hashed).
    """

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        logger: JsonlLogger | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        dry_run: bool = False,
    ) -> None:
        self.registry = registry or ModelRegistry()
        self.timeout = timeout
        self.max_retries = max_retries
        self.dry_run = dry_run
        self.mock = mock_enabled() or os.environ.get("OMNI_LLM_MOCK", "").lower() in {"1", "true", "yes"}
        cache_root = os.environ.get("OMNI_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "omni")
        self.logger = logger or JsonlLogger(os.path.join(cache_root, "gateway.jsonl"), mock=self.mock)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "omni-model-gateway/1.0"})

    # ----- role resolution -----
    def role_model(self, role: str) -> tuple[str, dict[str, Any] | None]:
        """Return (model_id, capability hint) for a role, or (None, None)."""
        hint = DEFAULT_CAPABILITY.get(role, {"modality": "text", "tools": False, "prefer_open_weights": False})
        forced = os.environ.get(f"OMNI_MODEL_{role.upper()}")
        if forced:
            return forced, hint
        self.registry.refresh()
        rec = self.registry.select(role, **hint)
        if rec is None:
            # last resort: any text model
            rec = self.registry.select("fast_chat", modality="text", tools=False)
        return (rec.model_id if rec else None), hint

    def provider_for(self, model_id: str) -> str:
        return model_id.split("/", 1)[0] if "/" in model_id else "openai-compatible"

    # ----- http -----
    def _post_chat(self, provider: str, model_id: str, messages: list[dict[str, str]], params: dict[str, Any]) -> str:
        base = CHAT_BASE_URLS.get(provider)
        if not base:
            base = os.environ.get("OMNI_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        key = get_api_key(provider)
        headers = provider_headers(provider, key)
        # short model id for the body (strip provider prefix when the API expects it)
        short = model_id.split("/", 1)[-1] if "/" in model_id else model_id
        body = {"model": short, "messages": messages, **params}
        url = base + "/chat/completions"
        resp = self._session.post(url, headers=headers, data=json.dumps(body), timeout=self.timeout)
        if resp.status_code in {401, 403}:
            raise RuntimeError(f"{provider}: authentication failed (status {resp.status_code})")
        if resp.status_code == 429:
            raise RuntimeError(f"{provider}: rate limited (status 429)")
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    def _attempt(self, role: str, messages: list[dict[str, str]], params: dict[str, Any], tried: set[str]) -> GatewayResult | None:
        model_id, hint = self.role_model(role)
        if not model_id or model_id in tried:
            return None
        tried.add(model_id)
        provider = self.provider_for(model_id)
        started = time.time()
        try:
            if self.dry_run:
                text = f"[dry-run {role}] would call {model_id}"
                ok = True
            else:
                text = self._post_chat(provider, model_id, messages, params)
                ok = bool(text)
            elapsed = time.time() - started
            self.registry.record_outcome(model_id, ok, elapsed * 1000.0)
            return GatewayResult(
                text=text,
                model=model_id,
                provider=provider,
                role=role,
                cached=False,
                elapsed_s=elapsed,
                mock=False,
                fallback=bool(tried) and len(tried) > 1,
                attempts=len(tried),
                extra={"hint": hint},
            )
        except Exception as exc:  # provider error / rate limit / timeout -> fallback
            elapsed = time.time() - started
            self.registry.record_outcome(model_id, False, elapsed * 1000.0)
            self.logger.log(
                "gateway_attempt",
                role=role,
                model=model_id,
                provider=provider,
                ok=False,
                error=type(exc).__name__,
                elapsed_s=round(elapsed, 4),
                mock=self.mock,
            )
            return None

    def complete(
        self,
        messages: list[dict[str, str]],
        role: str = "fast_chat",
        params: dict[str, Any] | None = None,
    ) -> GatewayResult:
        """Chat completion routed to the best model for ``role`` with fallback."""
        if role not in ROLES:
            role = "fast_chat"
        params = dict(params or {})
        if self.mock:
            started = time.time()
            text = f"[mock-llm {role}] " + __import__("hashlib").sha256(
                json.dumps({"messages": messages, "params": params}, sort_keys=True).encode()
            ).hexdigest()[:24]
            result = GatewayResult(
                text=text, model="mock-model", provider="mock", role=role, cached=False,
                elapsed_s=time.time() - started, mock=True, fallback=False, attempts=1,
            )
            self.logger.log("gateway_complete", **result.metadata())
            return result

        tried: set[str] = set()
        last_error = ""
        for _ in range(max(1, self.max_retries + len(self.registry.records))):
            result = self._attempt(role, messages, params, tried)
            if result is not None:
                self.logger.log("gateway_complete", **result.metadata())
                return result
            # try next model for the role
            model_id, _ = self.role_model(role)
            if not model_id or model_id in tried:
                break
        # nothing available -> deterministic local fallback so the system never deadlocks
        text = f"[local-fallback {role}] no hosted model reachable; {last_error or 'no providers configured'}"
        return GatewayResult(
            text=text,
            model="local-fallback",
            provider="local",
            role=role,
            cached=False,
            elapsed_s=0.0,
            mock=True,
            fallback=True,
            attempts=len(tried),
            extra={"error": last_error},
        )

    def embed(self, texts: list[str], role: str = "embeddings") -> list[list[float]]:
        """Embeddings via the best embedding model, with a local hash fallback."""
        model_id, hint = self.role_model(role)
        provider = self.provider_for(model_id) if model_id else "local"
        key = get_api_key(provider) if model_id else None
        if model_id and key and provider in CHAT_BASE_URLS:
            base = CHAT_BASE_URLS[provider].rstrip("/")
            short = model_id.split("/", 1)[-1]
            try:
                headers = provider_headers(provider, key)
                body = {"model": short, "input": texts}
                resp = self._session.post(base + "/embeddings", headers=headers, data=json.dumps(body), timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                return [item["embedding"] for item in data["data"]]
            except Exception:
                pass
        # local deterministic fallback
        import hashlib

        dim = 64
        return [
            [float(int.from_bytes(hashlib.sha256((t + str(i)).encode()).digest()[:4], "big")) / 2**32 for i in range(dim)]
            for t in texts
        ]
