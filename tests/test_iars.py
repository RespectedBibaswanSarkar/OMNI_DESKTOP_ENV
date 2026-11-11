from __future__ import annotations

import numpy as np

from omni.context.features import FEATURE_DIM
from omni.routing.agents import AgentSpec
from omni.routing.iars import BayesianLinearModel, IARS


def test_sherman_morrison_matches_direct_ridge() -> None:
    dim = 5
    prior = np.array([0.1, -0.2, 0.3, 0.0, 0.5])
    model = BayesianLinearModel(dim=dim, ridge=1.5, noise=0.7, prior_mean=prior)
    rng = np.random.default_rng(3)
    observations = [(rng.standard_normal(dim), float(rng.standard_normal())) for _ in range(4)]
    for phi, reward in observations:
        model.update(phi, reward)
    precision = 1.5 * np.eye(dim)
    b = 1.5 * prior
    for phi, reward in observations:
        precision += (phi @ phi) / 0.7 * np.eye(dim) * 0.0 + np.outer(phi, phi) / 0.7
        b += reward * phi / 0.7
    direct = np.linalg.solve(precision, b)
    assert np.allclose(model.mean, direct, atol=1e-10)


def test_sampling_is_seeded() -> None:
    a = BayesianLinearModel(4, prior_mean=np.ones(4))
    b = BayesianLinearModel(4, prior_mean=np.ones(4))
    assert np.allclose(a.sample(np.random.default_rng(1)), b.sample(np.random.default_rng(1)))
    assert not np.allclose(a.sample(np.random.default_rng(1)), a.sample(np.random.default_rng(2)))


def test_implicit_reward_formula() -> None:
    reward = IARS.implicit_reward(accepted=True, undo_count=1, edit_distance=0.5, max_edit_distance=1.0, latency_s=15.0, latency_budget_s=30.0)
    assert abs(reward - (1.0 - 0.5 - 0.25 - 0.1)) < 1e-12
    assert IARS.implicit_reward(accepted=True, reverted=True) == 0.0


def _agent(agent_id: str) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, description=agent_id, tags=(agent_id,), cost=0.0)


def test_prior_chooses_without_observations_and_update_raises_score() -> None:
    agents = [_agent("a"), _agent("b")]
    prior = np.zeros(FEATURE_DIM)
    prior[7] = 2.0
    router = IARS(agents=agents, prior_vectors={"a": prior, "b": np.zeros(FEATURE_DIM)}, tau=0.0, seed=1)
    phi = np.zeros(FEATURE_DIM)
    phi[7] = 1.0
    decision = router.decide(phi)
    assert decision.agent_id == "a"
    assert decision.explored is False
    before = float(router.models["b"].mean @ phi)
    router.observe(phi, "b", reward=1.0)
    after = float(router.models["b"].mean @ phi)
    assert after > before


def test_low_confidence_runs_top_two_verifier() -> None:
    agents = [_agent("a"), _agent("b")]
    router = IARS(agents=agents, tau=1.01, seed=1)
    decision = router.decide(np.ones(FEATURE_DIM), context_text="terminal python code")
    assert decision.explored is True
    assert len(decision.candidates) == 2
    assert decision.agent_id in decision.candidates
    assert decision.verification == "heuristic-lexical"
    assert 0.0 <= decision.confidence <= 1.0


def test_decide_serialises_scores() -> None:
    router = IARS(agents=[_agent("a")], tau=0.0)
    payload = router.decide(np.ones(FEATURE_DIM)).as_dict()
    assert payload["agent"] == "a"
    assert "a" in payload["scores"]
