from __future__ import annotations

import json

import numpy as np

from omni.llm.client import LLMClient
from omni.llm.embeddings import HashEmbedder


def test_hash_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashEmbedder(dim=32)
    a = embedder.embed("edit a markdown document")
    b = embedder.embed("edit a markdown document")
    assert a == b
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-9
    assert a != embedder.embed("browser tabs")


def test_mock_llm_is_cached_and_tagged(tmp_path) -> None:
    client = LLMClient(backend="mock", cache_dir=tmp_path)
    first = client.complete("hello", params={"temperature": 0.0})
    second = client.complete("hello", params={"temperature": 0.0})
    assert first.mock is True and second.mock is True
    assert first.cached is False and second.cached is True
    assert first.text == second.text
    assert first.cache_key == second.cache_key
    assert len(list((tmp_path / "llm").glob("*.json"))) == 1
    lines = (tmp_path / "llm_calls.jsonl").read_text().splitlines()
    assert lines and all(json.loads(line)["mock"] is True for line in lines)
    vectors = client.embed(["a", "b"])
    assert len(vectors) == 2 and len(vectors[0]) == 64
