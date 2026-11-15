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


class LLMVerifier(HeuristicVerifier):
    """Top-2 tie-break by a small router model; falls back to the lexical verifier.

    Used only on IARS's low-confidence branch (confidence < tau), so the extra
    model call is paid rarely.  ``ask`` is any callable(prompt) -> str.
    """

    def __init__(self, ask) -> None:  # noqa: ANN001
        self.ask = ask
        self.last_used_llm = False

    def choose(self, context_text: str, candidates: list[AgentSpec], scores: dict[str, float]) -> str:
        self.last_used_llm = False
        fallback = super().choose(context_text, candidates, scores)
        try:
            ids = [c.id for c in candidates]
            options = "\n".join(f"- {c.id}: {c.description}" for c in candidates)
            prompt = (
                "Pick the single best agent for this user request.\n"
                f"Request: {context_text[:400]}\nAgents:\n{options}\n"
                'Answer ONLY JSON: {"agent": "<one of ' + "|".join(ids) + '>"}'
            )
            raw = self.ask(prompt) or ""
            match = re.search(r"\{.*?\}", raw, re.DOTALL)
            if match:
                import json as _json
                agent = str(_json.loads(match.group(0)).get("agent", ""))
                if agent in ids:
                    self.last_used_llm = True
                    return agent
        except Exception:  # noqa: BLE001 - never let the verifier break routing
            pass
        return fallback
