from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from omni.context.features import FEATURE_DIM, prior_vector
from omni.llm.embeddings import HashEmbedder
from omni.routing.agents import DEFAULT_AGENTS, AgentSpec
from omni.routing.verifier import HeuristicVerifier


class BayesianLinearModel:
    """Ridge Bayesian linear model with a Sherman-Morrison precision update.

    A_inv is the posterior covariance. b is the precision-weighted reward vector,
    so the posterior mean is A_inv @ b. Sampling uses a Cholesky factor of A_inv.
    """

    def __init__(self, dim: int, ridge: float = 1.0, noise: float = 1.0, prior_mean: np.ndarray | None = None) -> None:
        self.dim = dim
        self.ridge = float(ridge)
        self.noise = float(noise)
        self.A_inv = np.eye(dim, dtype=np.float64) / self.ridge
        self.b = np.zeros(dim, dtype=np.float64)
        if prior_mean is not None:
            prior = np.asarray(prior_mean, dtype=np.float64).reshape(dim)
            self.b = self.ridge * prior
        self.n_updates = 0

    @property
    def mean(self) -> np.ndarray:
        return self.A_inv @ self.b

    @property
    def covariance(self) -> np.ndarray:
        return self.A_inv

    def sample(self, rng: np.random.Generator) -> np.ndarray:
        mean = self.mean
        try:
            chol = np.linalg.cholesky(self.A_inv + 1e-12 * np.eye(self.dim))
        except np.linalg.LinAlgError:
            chol = np.diag(np.sqrt(np.maximum(np.diag(self.A_inv), 1e-12)))
        return mean + chol @ rng.standard_normal(self.dim)

    def update(self, phi: np.ndarray, reward: float, weight: float = 1.0) -> None:
        x = np.asarray(phi, dtype=np.float64).reshape(self.dim)
        w = float(weight) / self.noise
        a_inv_x = self.A_inv @ x
        denom = 1.0 + w * float(x @ a_inv_x)
        if denom <= 0:
            return
        self.A_inv -= w * np.outer(a_inv_x, a_inv_x) / denom
        self.b += w * float(reward) * x
        self.n_updates += 1


@dataclass(frozen=True)
class RoutingDecision:
    agent_id: str
    agent_name: str
    confidence: float
    scores: dict[str, float]
    explored: bool
    candidates: tuple[str, ...]
    reason: str
    verification: str
    timestamp: float = field(default_factory=time.time)
    context_app: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent_id,
            "name": self.agent_name,
            "confidence": round(self.confidence, 4),
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "explored": self.explored,
            "candidates": list(self.candidates),
            "reason": self.reason,
            "verification": self.verification,
            "timestamp": self.timestamp,
            "context_app": self.context_app,
        }


class IARS:
    """Implicit intent routing with per-agent Thompson sampling."""

    def __init__(
        self,
        agents: Iterable[AgentSpec] = DEFAULT_AGENTS,
        dim: int = FEATURE_DIM,
        tau: float = 0.55,
        lambda_cost: float = 0.10,
        lambda_load: float = 0.20,
        ridge: float = 64.0,
        noise: float = 0.1,
        seed: int = 7,
        embedder: HashEmbedder | None = None,
        verifier: HeuristicVerifier | None = None,
        prior_vectors: dict[str, np.ndarray] | None = None,
        confidence_temperature: float = 0.35,
        prior_strength: float = 8.0,
    ) -> None:
        self.agents = tuple(agents)
        if not self.agents:
            raise ValueError("IARS needs at least one agent")
        self.dim = int(dim)
        self.tau = float(tau)
        self.lambda_cost = float(lambda_cost)
        self.lambda_load = float(lambda_load)
        self.confidence_temperature = float(confidence_temperature)
        self.prior_strength = float(prior_strength)
        self.embedder = embedder
        self.verifier = verifier or HeuristicVerifier()
        self.rng = np.random.default_rng(seed)
        self.models: dict[str, BayesianLinearModel] = {}
        for spec in self.agents:
            if prior_vectors and spec.id in prior_vectors:
                prior = np.asarray(prior_vectors[spec.id], dtype=np.float64)
                if prior.shape != (self.dim,):
                    raise ValueError(f"prior for {spec.id} must have shape ({self.dim},)")
            elif self.embedder is not None:
                prior = np.asarray(self.embedder.embed(" ".join((spec.description, *spec.tags))), dtype=np.float64)
            else:
                prior = prior_vector(" ".join((spec.description, *spec.tags)), self.dim) * self.prior_strength
            self.models[spec.id] = BayesianLinearModel(dim=self.dim, ridge=ridge, noise=noise, prior_mean=prior)

    def agent(self, agent_id: str) -> AgentSpec:
        for spec in self.agents:
            if spec.id == agent_id:
                return spec
        raise KeyError(agent_id)

    def scores(self, phi: np.ndarray, load_by_agent: dict[str, float] | None = None) -> dict[str, float]:
        x = np.asarray(phi, dtype=np.float64).reshape(self.dim)
        loads = load_by_agent or {}
        out: dict[str, float] = {}
        for spec in self.agents:
            theta = self.models[spec.id].sample(self.rng)
            out[spec.id] = float(theta @ x) - self.lambda_cost * spec.cost - self.lambda_load * float(loads.get(spec.id, 0.0))
        return out

    def _confidence(self, scores: dict[str, float]) -> tuple[str, float]:
        ids = list(scores)
        values = np.array([scores[i] for i in ids], dtype=np.float64)
        temp = max(self.confidence_temperature, 1e-6)
        shifted = (values - values.max()) / temp
        probs = np.exp(shifted)
        probs /= probs.sum()
        top = int(np.argmax(probs))
        return ids[top], float(probs[top])

    def decide(self, phi: np.ndarray, context_text: str = "", load_by_agent: dict[str, float] | None = None, tau: float | None = None) -> RoutingDecision:
        scores = self.scores(phi, load_by_agent)
        ranked = sorted(scores, key=scores.get, reverse=True)
        top_id, confidence = self._confidence(scores)
        assert top_id == ranked[0]
        threshold = self.tau if tau is None else float(tau)
        chosen = top_id
        explored = False
        candidates: tuple[str, ...] = (top_id,)
        verification = "single"
        if confidence < threshold and len(ranked) > 1:
            candidates = (ranked[0], ranked[1])
            chosen = self.verifier.choose(context_text, [self.agent(candidates[0]), self.agent(candidates[1])], scores)
            explored = True
            verification = "heuristic-lexical"
            reason = f"confidence {confidence:.2f} < tau {threshold:.2f}; ran top-2; verifier chose {chosen}"
        else:
            reason = f"highest sampled utility (confidence {confidence:.2f} >= tau {threshold:.2f})"
        spec = self.agent(chosen)
        return RoutingDecision(
            agent_id=chosen,
            agent_name=spec.name,
            confidence=confidence,
            scores=scores,
            explored=explored,
            candidates=candidates,
            reason=reason,
            verification=verification,
            context_app="",
        )

    def observe(self, phi: np.ndarray, agent_id: str, reward: float, weight: float = 1.0) -> None:
        self.models[agent_id].update(phi, reward, weight=weight)

    def observe_many(self, phi: np.ndarray, outcomes: dict[str, float], weight: float = 1.0) -> None:
        for agent_id, reward in outcomes.items():
            self.observe(phi, agent_id, reward, weight=weight)

    @staticmethod
    def implicit_reward(
        accepted: bool,
        reverted: bool = False,
        undo_count: int = 0,
        edit_distance: float = 0.0,
        max_edit_distance: float = 1.0,
        latency_s: float = 0.0,
        latency_budget_s: float = 30.0,
        beta_undo: float = 0.5,
        beta_edit: float = 0.5,
        beta_latency: float = 0.2,
    ) -> float:
        """Implicit reward from observable post-action signals (no user labels)."""
        accepted_flag = 1.0 if accepted and not reverted else 0.0
        edit_norm = min(max(float(edit_distance) / max(float(max_edit_distance), 1e-9), 0.0), 1.0)
        latency_norm = min(max(float(latency_s) / max(float(latency_budget_s), 1e-9), 0.0), 1.0)
        return accepted_flag - beta_undo * float(max(undo_count, 0)) - beta_edit * edit_norm - beta_latency * latency_norm
