from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

try:  # system keyring is optional; env vars always work
    import keyring  # type: ignore

    _HAS_KEYRING = True
except Exception:  # pragma: no cover - keyring not installed
    keyring = None  # type: ignore[assignment]
    _HAS_KEYRING = False

SERVICE_NAME = "omni-model-gateway"


@dataclass(frozen=True)
class ModelRecord:
    """A single normalised model entry from a provider's live model list."""

    model_id: str               # provider-prefixed id, e.g. "openrouter/meta-llama/llama-3.1-8b-instruct"
    provider: str
    context_length: int | None = None
    modalities: frozenset[str] = field(default_factory=lambda: frozenset({"text"}))
    tools: bool = False
    price_input_per_1k: float | None = None      # USD per 1k tokens
    price_output_per_1k: float | None = None     # USD per 1k tokens
    open_weights: str = "unknown"                # "yes" | "no" | "unknown" - never guessed
    latency_ms: float = 0.0                       # measured at runtime
    error_rate: float = 0.0                       # measured at runtime
    successes: int = 0
    failures: int = 0
    last_checked: float = 0.0
    source: str = ""                              # provider endpoint used
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["modalities"] = sorted(self.modalities)
        return d

    @property
    def cost_score(self) -> float:
        """Normalised cost proxy (input+output per 1k); missing prices treated as unknown/high."""
        if self.price_input_per_1k is None and self.price_output_per_1k is None:
            return 1.0
        inp = self.price_input_per_1k or 0.0
        out = self.price_output_per_1k or 0.0
        return inp + out

    @property
    def reliability(self) -> float:
        total = self.successes + self.failures
        if total == 0:
            return 0.5
        return self.successes / total

    def is_available(self) -> bool:
        return self.error_rate < 1.0 and self.reliability > 0.0


def _cache_dir() -> Path:
    return Path(os.environ.get("OMNI_CACHE_DIR", Path.home() / ".cache" / "omni")) / "registry"


def _record_cache_path(provider: str) -> Path:
    _cache_dir().mkdir(parents=True, exist_ok=True)
    return _cache_dir() / f"{provider}.json"


def provider_headers(provider: str, api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "omni-model-gateway/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def get_api_key(provider: str) -> str | None:
    """Resolve a provider API key from env or the system keyring. Never logged."""
    env_map = {
        "openrouter": "OPENROUTER_API_KEY",
        "huggingface": "HF_TOKEN",
        "together": "TOGETHER_API_KEY",
        "groq": "GROQ_API_KEY",
        "fireworks": "FIREWORKS_API_KEY",
        "cerebras": "CEREBRAS_API_KEY",
    }
    env_var = env_map.get(provider)
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
    if _HAS_KEYRING:
        try:
            value = keyring.get_password(SERVICE_NAME, provider)
            if value:
                return value
        except Exception:
            pass
    return None


class ModelRegistry:
    """Fetches each provider's live model list and normalises it into one registry.

    Model lists are cached on disk with the provider's ETag and a TTL (default
    6 hours) so startup is fast and rate limits on list endpoints are avoided.
    No static model list lives in code.
    """

    def __init__(
        self,
        providers: dict[str, dict[str, Any]] | None = None,
        ttl_hours: float = 6.0,
        cache_dir: Path | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.ttl_hours = float(ttl_hours)
        self.timeout = timeout
        self.providers: dict[str, dict[str, Any]] = providers or {
            "openrouter": {
                "list_url": "https://openrouter.ai/api/v1/models",
                "key_env": "OPENROUTER_API_KEY",
            },
            "huggingface": {
                "list_url": "https://api.endpoints.huggingface.cloud/v1/endpoint",
                "key_env": "HF_TOKEN",
            },
            "together": {
                "list_url": "https://api.together.xyz/v1/models",
                "key_env": "TOGETHER_API_KEY",
            },
            "groq": {
                "list_url": "https://api.groq.com/openai/v1/models",
                "key_env": "GROQ_API_KEY",
            },
            "fireworks": {
                "list_url": "https://api.fireworks.ai/inference/v1/models",
                "key_env": "FIREWORKS_API_KEY",
            },
            "cerebras": {
                "list_url": "https://api.cerebras.ai/v1/models",
                "key_env": "CEREBRAS_API_KEY",
            },
        }
        self.records: dict[str, ModelRecord] = {}
        self._load_cache()

    # ----- persistence -----
    def _load_cache(self) -> None:
        for provider in self.providers:
            path = _record_cache_path(provider)
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                for entry in payload.get("models", []):
                    rec = ModelRecord(**entry)
                    self.records[rec.model_id] = rec
            except (OSError, json.JSONDecodeError, TypeError):
                continue

    def _save_cache(self, provider: str, models: list[ModelRecord], etag: str | None) -> None:
        path = _record_cache_path(provider)
        payload = {
            "provider": provider,
            "etag": etag,
            "fetched_at": time.time(),
            "models": [m.as_dict() for m in models],
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    # ----- fetching -----
    def _normalise(self, provider: str, raw: list[dict[str, Any]]) -> list[ModelRecord]:
        out: list[ModelRecord] = []
        for item in raw:
            mid = str(item.get("id") or item.get("name") or item.get("model_id") or "")
            if not mid:
                continue
            modalities = set(item.get("modalities") or item.get("modalities", []) or {"text"})
            if "text" not in modalities:
                modalities.add("text")
            # licence metadata: only mark open-weights if the provider explicitly says so.
            licence = str(item.get("licence") or item.get("license") or item.get("open_weights") or "").lower()
            if licence in {"yes", "true", "open", "apache", "mit", "llama", "mixtral"}:
                open_weights = "yes"
            elif licence in {"no", "false", "closed", "proprietary"}:
                open_weights = "no"
            else:
                open_weights = "unknown"
            out.append(
                ModelRecord(
                    model_id=f"{provider}/{mid}",
                    provider=provider,
                    context_length=self._int(item.get("context_length") or item.get("context_window")),
                    modalities=frozenset(modalities),
                    tools=bool(item.get("tools") or item.get("tool_calling") or item.get("function_calling")),
                    price_input_per_1k=self._money(item.get("price_input_per_1k") or item.get("pricing", {}).get("input")),
                    price_output_per_1k=self._money(item.get("price_output_per_1k") or item.get("pricing", {}).get("output")),
                    open_weights=open_weights,
                    last_checked=time.time(),
                    source=provider,
                    raw=item,
                )
            )
        return out

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _money(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def fetch_provider(self, provider: str, force: bool = False) -> list[ModelRecord]:
        config = self.providers.get(provider)
        if not config:
            return []
        now = time.time()
        cached = self.records  # may hold stale entries for this provider
        # TTL check using the newest record timestamp for this provider.
        provider_records = [r for r in self.records.values() if r.provider == provider]
        if not force and provider_records and (now - max(r.last_checked for r in provider_records)) < self.ttl_hours * 3600:
            return provider_records
        api_key = get_api_key(provider)
        list_url = config.get("list_url")
        if not list_url or not api_key:
            return provider_records  # no key/endpoint: keep stale records, if any
        headers = provider_headers(provider, api_key)
        try:
            etag_path = _cache_dir() / f"{provider}.etag"
            etag = etag_path.read_text(encoding="utf-8").strip() if etag_path.exists() else None
            if etag:
                headers["If-None-Match"] = etag
            response = requests.get(list_url, headers=headers, timeout=self.timeout)
            if response.status_code == 304:
                return provider_records
            response.raise_for_status()
            if response.headers.get("ETag"):
                etag_path.write_text(response.headers["ETag"], encoding="utf-8")
            data = response.json()
        except Exception:
            return provider_records
        items = data if isinstance(data, list) else data.get("data") or data.get("models") or []
        records = self._normalise(provider, items)
        for rec in records:
            self.records[rec.model_id] = rec
        self._save_cache(provider, records, response.headers.get("ETag") if hasattr(response, "headers") else None)
        return records

    def refresh(self, force: bool = False) -> dict[str, int]:
        counts: dict[str, int] = {}
        for provider in self.providers:
            try:
                records = self.fetch_provider(provider, force=force)
                counts[provider] = len(records)
            except Exception:
                counts[provider] = 0
        return counts

    # ----- selection -----
    def candidates(
        self,
        modality: str = "text",
        tools: bool = False,
        max_cost: float | None = None,
        require_open_weights: bool | None = None,
    ) -> list[ModelRecord]:
        out: list[ModelRecord] = []
        for rec in self.records.values():
            if modality not in rec.modalities:
                continue
            if tools and not rec.tools:
                continue
            if max_cost is not None and rec.cost_score > max_cost:
                continue
            if require_open_weights is True and rec.open_weights != "yes":
                continue
            if require_open_weights is False and rec.open_weights == "yes":
                continue
            out.append(rec)
        return out

    def select(
        self,
        role: str,
        modality: str = "text",
        tools: bool = False,
        max_cost: float | None = None,
        prefer_open_weights: bool = False,
    ) -> ModelRecord | None:
        """Pick the best model for a role: low latency + high reliability + low cost."""
        cands = self.candidates(modality=modality, tools=tools, max_cost=max_cost)
        if not cands:
            return None
        # role hints (env override OMNI_MODEL_<ROLE> takes precedence)
        env_key = f"OMNI_MODEL_{role.upper()}"
        forced = os.environ.get(env_key)
        if forced:
            for rec in cands:
                if rec.model_id == forced or rec.model_id.split("/", 1)[-1] == forced:
                    return rec
        if prefer_open_weights:
            cands = [c for c in cands if c.open_weights == "yes"] or cands
        # score: reliability high, latency low, cost low
        now = time.time()

        def score(rec: ModelRecord) -> float:
            age_penalty = min((now - rec.last_checked) / 3600.0, 5.0)  # stale records penalised
            return (
                2.0 * rec.reliability
                - 0.001 * rec.latency_ms
                - 0.5 * rec.cost_score
                - 0.1 * age_penalty
            )

        return max(cands, key=score)

    def record_outcome(self, model_id: str, ok: bool, latency_ms: float) -> None:
        rec = self.records.get(model_id)
        if rec is None:
            return
        if ok:
            rec.__class__  # dataclass is frozen; rebuild
        # frozen dataclass -> replace
        object.__setattr__(rec, "successes", rec.successes + (1 if ok else 0))
        object.__setattr__(rec, "failures", rec.failures + (0 if ok else 1))
        total = rec.successes + rec.failures
        object.__setattr__(rec, "error_rate", rec.failures / total)
        alpha = 0.3
        object.__setattr__(
            rec,
            "latency_ms",
            (1 - alpha) * rec.latency_ms + alpha * float(latency_ms),
        )
