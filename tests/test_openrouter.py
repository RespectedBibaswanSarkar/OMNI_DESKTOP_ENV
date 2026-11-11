from __future__ import annotations

import json

from omni.openrouter import OpenRouterGateway


def make_model(model_id, prompt, completion, context=8192):
    return {
        "id": model_id,
        "context_length": context,
        "pricing": {
            "prompt": str(prompt),
            "completion": str(completion),
        },
    }


def test_free_model_detection():
    assert OpenRouterGateway._is_free(
        make_model("provider/free", 0, 0)
    )
    assert not OpenRouterGateway._is_free(
        make_model("provider/paid", 0.1, 0.2)
    )


def test_free_models_are_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OMNI_PREFER_FREE", "true")

    gateway = OpenRouterGateway(api_key="test")

    gateway.models = lambda force=False: [
        make_model("provider/paid", 0.1, 0.2),
        make_model("provider/free", 0, 0),
    ]

    candidates = gateway.candidates()

    assert candidates[0]["id"] == "provider/free"


def test_paid_fallback_when_no_free_model(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OMNI_PREFER_FREE", "true")

    gateway = OpenRouterGateway(api_key="test")

    gateway.models = lambda force=False: [
        make_model("provider/paid", 0.1, 0.2),
    ]

    assert gateway.candidates()[0]["id"] == "provider/paid"


def test_free_preference_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("OMNI_PREFER_FREE", "false")

    gateway = OpenRouterGateway(api_key="test")

    gateway.models = lambda force=False: [
        make_model("provider/paid", 0.1, 0.2),
        make_model("provider/free", 0, 0),
    ]

    assert gateway.candidates()[0]["id"] == "provider/paid"


def test_model_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNI_CACHE_DIR", str(tmp_path))

    gateway = OpenRouterGateway(api_key="test")
    gateway.cache_path.parent.mkdir(parents=True)

    records = [make_model("provider/free", 0, 0)]

    gateway.cache_path.write_text(
        json.dumps({
            "fetched_at": __import__("time").time(),
            "models": records,
        }),
        encoding="utf-8",
    )

    assert gateway.models() == records


def test_gateway_status_does_not_expose_api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-value")

    status = OpenRouterGateway().status()

    assert status["configured"] is True
    assert "secret-value" not in str(status)
