"""Tiny stdlib HTTP client for the OMNI daemon (omnid)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = os.environ.get("OMNI_API_URL", "http://127.0.0.1:8000")


class OmniUnavailable(RuntimeError):
    pass


class OmniClient:
    def __init__(self, base: str | None = None, timeout: float = 5.0) -> None:
        self.base = (base or DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise OmniUnavailable(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OmniUnavailable(f"omnid not reachable at {self.base}: {exc}") from exc

    def health(self) -> dict:
        return self._req("GET", "/health")

    def submit(self, goal: str) -> dict:
        return self._req("POST", "/api/tasks", {"goal": goal})

    def task(self, task_id: str) -> dict:
        return self._req("GET", f"/api/tasks/{task_id}")

    def cancel(self, task_id: str) -> dict:
        return self._req("POST", f"/api/tasks/{task_id}/cancel", {})

    def confirm(self, task_id: str, approve: bool) -> dict:
        return self._req("POST", f"/api/tasks/{task_id}/confirm", {"approve": approve})


TERMINAL = {"done", "failed", "cancelled", "error"}
