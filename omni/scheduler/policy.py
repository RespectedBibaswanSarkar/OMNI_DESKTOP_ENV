from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omni.scheduler.context import FEATURE_NAMES, ScheduleFeatures
from omni.scheduler.krls import KRLS


@dataclass(frozen=True)
class ScheduleAction:
    id: str
    label: str
    cpu_weight: int
    io_weight: int
    nice: int
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "cpu_weight": self.cpu_weight,
            "io_weight": self.io_weight,
            "nice": self.nice,
        }


ACTIONS: tuple[ScheduleAction, ...] = (
    ScheduleAction("boost", "Interactive boost", 300, 300, 0, "Favour the focused interactive app."),
    ScheduleAction("balanced", "Balanced", 100, 100, 0, "Default CPU/IO share."),
    ScheduleAction("throttle", "Background throttle", 35, 40, 0, "Keep background work from starving the foreground."),
)
ACTIONS_BY_ID: dict[str, ScheduleAction] = {action.id: action for action in ACTIONS}


def heuristic_scores(features: ScheduleFeatures) -> dict[str, float]:
    """Design-prior scores used until the KRLS models have observations.

    STUB: the live service has no reward signal yet, so it reports
    ``policy="heuristic-prior"`` until Phase 4 bench/actuation feeds outcomes.
    """
    x = features
    boost = 0.55 + 0.9 * x.interactive - 0.55 * x.load_norm - 0.7 * x.mem_pressure - 0.15 * x.client_norm
    balanced = 0.35 + 0.2 * x.interactive - 0.25 * abs(x.load_norm - 0.35) - 0.2 * x.mem_pressure
    throttle = 0.20 + 0.7 * (1.0 - x.interactive) + 0.5 * x.load_norm + 0.35 * x.mem_pressure
    return {"boost": boost, "balanced": balanced, "throttle": throttle}


@dataclass(frozen=True)
class SchedulerDecision:
    action: str
    label: str
    policy: str
    scores: dict[str, float]
    features: dict[str, float]
    reason: str
    cpu_weight: int
    io_weight: int
    nice: int
    updates: int

    def as_dict(self, applied: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "label": self.label,
            "policy": self.policy,
            "scores": {key: round(value, 4) for key, value in self.scores.items()},
            "features": self.features,
            "reason": self.reason,
            "cpu_weight": self.cpu_weight,
            "io_weight": self.io_weight,
            "nice": self.nice,
            "updates": self.updates,
        }
        if applied is not None:
            payload["applied"] = applied
        return payload


class KRLSPolicy:
    """Contextual scheduler: one KRLS reward model per candidate action."""

    def __init__(
        self,
        gamma: float = 1.5,
        ridge: float = 0.05,
        budget: int = 48,
        ald_threshold: float = 0.02,
        actions: tuple[ScheduleAction, ...] = ACTIONS,
    ) -> None:
        self.actions = tuple(actions)
        self.models = {
            action.id: KRLS(
                dim=len(FEATURE_NAMES),
                gamma=gamma,
                ridge=ridge,
                budget=budget,
                ald_threshold=ald_threshold,
            )
            for action in self.actions
        }

    @property
    def updates(self) -> int:
        return sum(model.n_updates for model in self.models.values())

    def scores(self, features: ScheduleFeatures) -> dict[str, float]:
        x = features.as_array()
        return {action.id: float(self.models[action.id].predict(x)) for action in self.actions}

    def decide(self, features: ScheduleFeatures) -> SchedulerDecision:
        if self.updates == 0:
            scores = heuristic_scores(features)
            policy = "heuristic-prior"
        else:
            scores = self.scores(features)
            policy = "krls"
        action_id = max(scores, key=scores.get)
        action = ACTIONS_BY_ID[action_id]
        return SchedulerDecision(
            action=action.id,
            label=action.label,
            policy=policy,
            scores=scores,
            features=features.as_dict(),
            reason=f"{action.description} (predicted reward {scores[action_id]:.3f})",
            cpu_weight=action.cpu_weight,
            io_weight=action.io_weight,
            nice=action.nice,
            updates=self.updates,
        )

    def update(self, features: ScheduleFeatures, action_id: str, reward: float) -> None:
        if action_id not in self.models:
            raise KeyError(action_id)
        self.models[action_id].update(features.as_array(), float(reward))

    def state(self) -> dict[str, Any]:
        return {
            "updates": self.updates,
            "models": {action.id: self.models[action.id].state() for action in self.actions},
        }
