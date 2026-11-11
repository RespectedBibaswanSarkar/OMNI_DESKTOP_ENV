from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omni.llm.embeddings import HashEmbedder
from omni.telemetry import JsonlLogger, mock_enabled


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    backend: str
    mock: bool
    cached: bool
    elapsed_s: float
    cache_key: str
    extra: dict[str, Any] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "backend": self.backend,
            "mock": self.mock,
            "cached": self.cached,
            "elapsed_s": round(self.elapsed_s, 4),
            "cache_key": self.cache_key,
            **self.extra,
        }


class LLMClient:
    """Single LLM entry point with an on-disk cache.

    Backends (selected by OMNI_LLM_BACKEND):
      mock    - deterministic, offline; always tagged mock=True
      ollama  - local OpenAI-ish Ollama server (OMNI_OLLAMA_URL)
      openai  - any OpenAI-compatible endpoint (OMNI_LLM_BASE_URL, OMNI_LLM_API_KEY)

    Every request is keyed by hash(backend+model+prompt+params) and cached under
    OMNI_CACHE_DIR (default ~/.cache/omni). Cache hits are flagged cached=True so
    benchmark runs can exclude them from paper data.
    """

    def __init__(
        self,
        backend: str | None = None,
        model: str | None = None,
        cache_dir: str | Path | None = None,
        mock: bool | None = None,
        logger: JsonlLogger | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.backend = (backend or os.environ.get("OMNI_LLM_BACKEND", "ollama")).lower()
        if mock or os.environ.get("OMNI_LLM_MOCK", "").lower() in {"1", "true", "yes"}:
            self.backend = "mock"
        self.model = model or os.environ.get("OMNI_LLM_MODEL", "")
        self.cache_dir = Path(cache_dir or os.environ.get("OMNI_CACHE_DIR", Path.home() / ".cache" / "omni")) / "llm"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.logger = logger or JsonlLogger(self.cache_dir.parent / "llm_calls.jsonl", mock=self.backend == "mock")

    @property
    def is_mock(self) -> bool:
        return self.backend == "mock" or mock_enabled()

    def _key(self, kind: str, payload: dict[str, Any]) -> str:
        blob = json.dumps({"kind": kind, "backend": self.backend, "model": self.model, **payload}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self, key: str, entry: dict[str, Any]) -> None:
        path = self._cache_path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entry, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def complete(self, prompt: str, params: dict[str, Any] | None = None, model: str | None = None) -> LLMResult:
        params = params or {}
        model_name = model or self.model or ("mock-model" if self.is_mock else "")
        key = self._key("complete", {"prompt": prompt, "params": params, "model": model_name})
        started = time.time()
        cached = self._read(key)
        if cached is not None:
            result = LLMResult(text=cached["text"], model=cached.get("model", model_name), backend=cached.get("backend", self.backend), mock=bool(cached.get("mock", False)), cached=True, elapsed_s=time.time() - started, cache_key=key)
            self.logger.log("llm_complete", **result.metadata())
            return result
        if self.is_mock:
            text = "[mock-llm] " + hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
            entry = {"text": text, "model": model_name, "backend": "mock", "mock": True}
        else:
            text = self._http_complete(prompt, params, model_name)
            entry = {"text": text, "model": model_name, "backend": self.backend, "mock": False}
        self._write(key, entry)
        result = LLMResult(text=text, model=model_name, backend=entry["backend"], mock=bool(entry["mock"]), cached=False, elapsed_s=time.time() - started, cache_key=key)
        self.logger.log("llm_complete", **result.metadata())
        return result

    def embed(self, texts: list[str], params: dict[str, Any] | None = None, model: str | None = None) -> list[list[float]]:
        params = params or {}
        model_name = model or self.model or ("mock-embed" if self.is_mock else "")
        key = self._key("embed", {"texts": texts, "params": params, "model": model_name})
        started = time.time()
        cached = self._read(key)
        if cached is not None:
            self.logger.log("llm_embed", model=model_name, backend=cached.get("backend", self.backend), mock=bool(cached.get("mock", False)), cached=True, elapsed_s=round(time.time() - started, 4), cache_key=key, count=len(texts))
            return cached["vectors"]
        if self.is_mock:
            embedder = HashEmbedder(int(params.get("dim", 64)))
            vectors = [embedder.embed(t) for t in texts]
            entry = {"vectors": vectors, "model": model_name, "backend": "mock", "mock": True}
        else:
            vectors = self._http_embed(texts, model_name)
            entry = {"vectors": vectors, "model": model_name, "backend": self.backend, "mock": False}
        self._write(key, entry)
        self.logger.log("llm_embed", model=model_name, backend=entry["backend"], mock=bool(entry["mock"]), cached=False, elapsed_s=round(time.time() - started, 4), cache_key=key, count=len(texts))
        return vectors

    def _http_complete(self, prompt: str, params: dict[str, Any], model: str) -> str:
        if not model:
            raise RuntimeError("OMNI_LLM_MODEL is required for a non-mock backend")
        if self.backend == "ollama":
            url = os.environ.get("OMNI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
            body = {"model": model, "prompt": prompt, "stream": False, "options": params}
            data = self._post(url, body)
            return str(data.get("response", ""))
        if self.backend in {"openai", "openai-compatible"}:
            base = os.environ.get("OMNI_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            body = {"model": model, "messages": [{"role": "user", "content": prompt}], **params}
            data = self._post(base + "/chat/completions", body, auth=True)
            return str(data["choices"][0]["message"]["content"])
        raise RuntimeError(f"Unknown LLM backend: {self.backend}")

    def _http_embed(self, texts: list[str], model: str) -> list[list[float]]:
        if self.backend == "ollama":
            url = os.environ.get("OMNI_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/embeddings"
            return [self._post(url, {"model": model, "prompt": text}).get("embedding", []) for text in texts]
        if self.backend in {"openai", "openai-compatible"}:
            base = os.environ.get("OMNI_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            data = self._post(base + "/embeddings", {"model": model, "input": texts}, auth=True)
            return [item["embedding"] for item in data["data"]]
        raise RuntimeError(f"Unknown embedding backend: {self.backend}")

    def _post(self, url: str, body: dict[str, Any], auth: bool = False) -> dict[str, Any]:
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        if auth and os.environ.get("OMNI_LLM_API_KEY"):
            request.add_header("Authorization", "Bearer " + os.environ["OMNI_LLM_API_KEY"])
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM backend {self.backend} HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM backend {self.backend} unreachable: {exc.reason}") from exc
