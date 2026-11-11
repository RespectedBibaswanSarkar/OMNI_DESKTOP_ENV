from __future__ import annotations

import math
import re

from omni.routing.agents import AgentSpec

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_+.-]*")


class HeuristicVerifier:
    """Cheap local top-2 verifier: lexical affinity + model score, no LLM call."""

    def choose(self, context_text: str, candidates: list[AgentSpec], scores: dict[str, float]) -> str:
        context = set(_TOKEN.findall(context_text.lower()))
        best_id = candidates[0].id
        best_value = -math.inf
        for spec in candidates:
            words = set(_TOKEN.findall(" ".join((spec.description, *spec.tags)).lower()))
            overlap = len(context & words) / math.sqrt(max(len(context) * len(words), 1))
            value = overlap + 0.01 * scores.get(spec.id, 0.0)
            if value > best_value:
                best_value = value
                best_id = spec.id
        return best_id
