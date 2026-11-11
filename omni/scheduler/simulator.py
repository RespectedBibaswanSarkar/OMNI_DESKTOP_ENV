from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from omni.scheduler.context import ScheduleFeatures
from omni.scheduler.policy import ACTIONS, KRLSPolicy

SIMULATED_NOTE = "SIMULATED: synthetic reward model for policy tests, not a measurement."


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    interactive: tuple[float, float]
    load: tuple[float, float]
    memory: tuple[float, float]
    clients: tuple[float, float]
    focus_age: tuple[float, float] = (0.55, 0.12)


SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario("S1", "interactive foreground vs background work", (0.90, 0.03), (0.55, 0.12), (0.15, 0.05), (0.50, 0.10)),
    "S2": Scenario("S2", "memory pressure with background batch", (0.15, 0.05), (0.80, 0.10), (0.75, 0.08), (0.80, 0.10)),
    "S3": Scenario("S3", "idle / low-load mixed use", (0.50, 0.05), (0.20, 0.08), (0.20, 0.05), (0.20, 0.10)),
}


def _clip(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def sample_state(rng: np.random.Generator, scenario: Scenario) -> ScheduleFeatures:
    def draw(pair: tuple[float, float]) -> float:
        return _clip(rng.normal(pair[0], pair[1]))

    return ScheduleFeatures(
        load_norm=draw(scenario.load),
        cpu_pressure=draw((scenario.load[0] * 0.6, 0.08)),
        mem_pressure=draw(scenario.memory),
        client_norm=draw(scenario.clients),
        interactive=draw(scenario.interactive),
        focus_age_norm=draw(scenario.focus_age),
    )


def true_rewards(features: ScheduleFeatures) -> dict[str, float]:
    """Synthetic reward model. Explicitly simulated, never reported as data."""
    x = features
    boost = 1.0 * x.interactive - 0.5 * x.load_norm - 0.8 * x.mem_pressure + 0.2
    balanced = 0.45 - 0.2 * abs(x.load_norm - 0.35) - 0.2 * x.mem_pressure + 0.1 * x.interactive
    throttle = 0.8 * (1.0 - x.interactive) + 0.5 * x.load_norm + 0.3 * x.mem_pressure - 0.5 * x.interactive
    return {"boost": boost, "balanced": balanced, "throttle": throttle}


def simulate(
    scenario_id: str,
    steps: int = 400,
    test_steps: int = 120,
    seed: int = 0,
    epsilon_start: float = 0.35,
    epsilon_end: float = 0.05,
    noise: float = 0.02,
) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_id]
    rng = np.random.default_rng(seed)
    policy = KRLSPolicy()
    action_counts: dict[str, int] = {action.id: 0 for action in ACTIONS}

    for step in range(max(steps, 1)):
        features = sample_state(rng, scenario)
        rewards = true_rewards(features)
        epsilon = epsilon_start + (epsilon_end - epsilon_start) * (step / max(steps - 1, 1))
        if rng.random() < epsilon:
            action_id = str(rng.choice([action.id for action in ACTIONS]))
        else:
            action_id = policy.decide(features).action
        reward = rewards[action_id] + float(rng.normal(0.0, noise))
        policy.update(features, action_id, reward)
        action_counts[action_id] += 1

    learned: list[float] = []
    baseline: list[float] = []
    oracle: list[float] = []
    correct = 0
    chosen_counts: dict[str, int] = {action.id: 0 for action in ACTIONS}
    for _ in range(max(test_steps, 1)):
        features = sample_state(rng, scenario)
        rewards = true_rewards(features)
        decision = policy.decide(features)
        learned.append(rewards[decision.action])
        baseline.append(rewards["balanced"])
        best = max(rewards, key=rewards.get)
        oracle.append(rewards[best])
        correct += int(decision.action == best)
        chosen_counts[decision.action] += 1

    tests = max(test_steps, 1)
    return {
        "scenario": scenario_id,
        "name": scenario.name,
        "simulated": True,
        "steps": steps,
        "test_steps": test_steps,
        "seed": seed,
        "updates": policy.updates,
        "train_actions": action_counts,
        "eval_actions": chosen_counts,
        "mean_reward": round(float(np.mean(learned)), 4),
        "balanced_reward": round(float(np.mean(baseline)), 4),
        "oracle_reward": round(float(np.mean(oracle)), 4),
        "improvement_over_balanced": round(float(np.mean(learned) - np.mean(baseline)), 4),
        "oracle_accuracy": round(correct / tests, 4),
        "note": SIMULATED_NOTE,
    }


def simulate_all(steps: int = 400, test_steps: int = 120, seed: int = 0) -> dict[str, Any]:
    return {
        "simulated": True,
        "note": SIMULATED_NOTE,
        "scenarios": {sid: simulate(sid, steps=steps, test_steps=test_steps, seed=seed + index) for index, sid in enumerate(SCENARIOS)},
    }
