from __future__ import annotations

from omni.gateway import DEFAULT_CAPABILITY, ModelGateway, ROLES
from omni.model_registry import ModelRegistry, ModelRecord, get_api_key, provider_headers


def _record(**overrides):
    base = dict(
        model_id="prov/model-a",
        provider="prov",
        context_length=4096,
        modalities=frozenset({"text"}),
        tools=True,
        price_input_per_1k=0.5,
        price_output_per_1k=1.5,
        open_weights="yes",
        latency_ms=100.0,
        error_rate=0.1,
        successes=9,
        failures=1,
        last_checked=1000.0,
        source="prov",
    )
    base.update(overrides)
    return ModelRecord(**base)


def test_record_cost_and_reliability():
    r = _record(successes=9, failures=1)
    assert r.cost_score == 2.0
    assert abs(r.reliability - 0.9) < 1e-9
    assert r.is_available()


def test_registry_select_filters_and_scores():
    reg = ModelRegistry()
    reg.records = {
        "p/fast": ModelRecord(
            model_id="p/fast", provider="p", modalities=frozenset({"text"}),
            price_input_per_1k=0.1, price_output_per_1k=0.1,
            successes=10, failures=0, last_checked=1000.0,
        ),
        "p/slow": ModelRecord(
            model_id="p/slow", provider="p", modalities=frozenset({"text"}),
            price_input_per_1k=10.0, price_output_per_1k=10.0,
            successes=5, failures=5, last_checked=1000.0,
        ),
    }
    pick = reg.select("fast_chat", modality="text", tools=False)
    assert pick is not None
    assert pick.model_id == "p/fast"


def test_registry_select_tools_filter():
    reg = ModelRegistry()
    reg.records = {
        "p/vision": ModelRecord(model_id="p/vision", provider="p", modalities=frozenset({"vision"}), tools=True),
        "p/text": ModelRecord(model_id="p/text", provider="p", modalities=frozenset({"text"}), tools=True),
    }
    pick = reg.select("planner", modality="text", tools=True)
    assert pick is not None
    assert pick.model_id == "p/text"


def test_registry_open_weights_never_guessed():
    reg = ModelRegistry()
    reg.records = {
        "p/a": ModelRecord(model_id="p/a", provider="p", modalities=frozenset({"text"}), open_weights="unknown"),
        "p/b": ModelRecord(model_id="p/b", provider="p", modalities=frozenset({"text"}), open_weights="yes"),
    }
    pick = reg.select("fast_chat", modality="text", prefer_open_weights=True)
    assert pick.model_id == "p/b"


def test_gateway_local_fallback_without_network(monkeypatch):
    monkeypatch.setenv("OMNI_LLM_BACKEND", "mock")
    gw = ModelGateway(registry=ModelRegistry(), dry_run=False)
    result = gw.complete([{"role": "user", "content": "hello"}], role="fast_chat")
    assert result.mock is True
    assert result.text
    assert result.role == "fast_chat"


def test_all_roles_have_capability_hints():
    for role in ROLES:
        assert role in DEFAULT_CAPABILITY
        assert "modality" in DEFAULT_CAPABILITY[role]


def test_headers_do_not_leak_key_when_none():
    headers = provider_headers("openrouter", None)
    assert "Authorization" not in headers

    headers = provider_headers("openrouter", "sk-secret")
    assert headers["Authorization"] == "Bearer sk-secret"


def test_get_api_key_env_only(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_api_key("openrouter") is None
