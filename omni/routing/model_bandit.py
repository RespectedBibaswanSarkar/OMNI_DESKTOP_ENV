"""Factored Thompson sampling over (agent, model) pairs  — the multi-model half of IARS.

IARS first picks an agent ``a`` with a contextual Bayesian-linear bandit.  This
module then picks the *model* ``m`` that serves that agent:

    beta_{a,m} ~ N( mu_n , sigma_n^2 )                      (posterior sample)
    score(m | a) = beta_{a,m} - lam_lat * min(l_m / L, 1) - lam_mem * (size_m / S)
    m* = argmax_m score(m | a)

with a conjugate Normal posterior (known noise sigma_eps^2):

    tau_n  = 1/sigma_0^2 + n / sigma_eps^2
    mu_n   = ( mu_0 / sigma_0^2 + sum_i r_i / sigma_eps^2 ) / tau_n
    sigma_n^2 = 1 / tau_n

The prior mean mu_0 comes from the capability ranking (rank 0 -> 0.5, last -> 0),
so cold start equals the hand-written preference order and evidence then moves it.
"""
from __future__ import annotations

from typing import Any

import numpy as np


class ModelBandit:
    def __init__(
        self,
        prior_sd: float = 0.35,
        noise_sd: float = 0.5,
        lambda_latency: float = 0.15,
        lambda_size: float = 0.10,
        seed: int = 11,
    ) -> None:
        self.prior_sd = float(prior_sd)
        self.noise_sd = float(noise_sd)
        self.lambda_latency = float(lambda_latency)
        self.lambda_size = float(lambda_size)
        self.rng = np.random.default_rng(seed)
        # (agent, model) -> [n, sum_reward, mu0]
        self.stats: dict[tuple[str, str], list[float]] = {}

    @staticmethod
    def prior_mean(rank: int, count: int) -> float:
        return 0.5 * (1.0 - rank / max(count - 1, 1))

    def posterior(self, agent: str, model: str, mu0: float) -> tuple[float, float]:
        n, total, _ = self.stats.get((agent, model), [0, 0.0, mu0])
        tau0 = 1.0 / self.prior_sd ** 2
        tau_n = tau0 + n / self.noise_sd ** 2
        mean = (mu0 * tau0 + total / self.noise_sd ** 2) / tau_n
        return mean, tau_n ** -0.5

    def select(
        self,
        agent: str,
        ranked: list[str],
        latency_s: dict[str, float | None] | None = None,
        size_gb: dict[str, float] | None = None,
        latency_budget_s: float = 60.0,
        size_budget_gb: float = 4.0,
        top_k: int = 3,
    ) -> dict[str, Any]:
        """Choose among the top-k capability-ranked models. ``ranked`` is best-first."""
        pool = ranked[: max(1, top_k)]
        latency_s = latency_s or {}
        size_gb = size_gb or {}
        scores: dict[str, float] = {}
        detail: dict[str, dict[str, float]] = {}
        for rank, model in enumerate(pool):
            mu0 = self.prior_mean(rank, len(pool))
            mean, sd = self.posterior(agent, model, mu0)
            beta = float(self.rng.normal(mean, sd))
            lat = latency_s.get(model)
            pen_l = self.lambda_latency * min((lat or 0.0) / max(latency_budget_s, 1e-6), 1.0)
            pen_s = self.lambda_size * min(size_gb.get(model, 0.0) / max(size_budget_gb, 1e-6), 1.5)
            scores[model] = beta - pen_l - pen_s
            detail[model] = {"mu": round(mean, 4), "sd": round(sd, 4), "sample": round(beta, 4),
                             "lat_penalty": round(pen_l, 4), "size_penalty": round(pen_s, 4)}
        chosen = max(scores, key=scores.get)
        return {"model": chosen, "scores": {k: round(v, 4) for k, v in scores.items()}, "posterior": detail}

    def observe(self, agent: str, model: str, reward: float, mu0: float | None = None) -> None:
        entry = self.stats.setdefault((agent, model), [0, 0.0, 0.25 if mu0 is None else float(mu0)])
        entry[0] += 1
        entry[1] += float(reward)

    def as_dict(self) -> dict[str, Any]:
        return {
            f"{a}|{m}": {"n": int(v[0]), "mean_reward": round(v[1] / v[0], 4) if v[0] else None}
            for (a, m), v in sorted(self.stats.items())
        }
