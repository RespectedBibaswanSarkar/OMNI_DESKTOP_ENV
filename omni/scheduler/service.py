from __future__ import annotations

from typing import Any

from omni.context.features import ContextSnapshot
from omni.modules.memory import measure_memory
from omni.scheduler.actuator import CgroupV2Actuator
from omni.scheduler.context import ScheduleFeatures, collect_features
from omni.scheduler.policy import ACTIONS_BY_ID, KRLSPolicy
from omni.telemetry import JsonlLogger


class SchedulerService:
    """Context features -> KRLS/heuristic decision -> optional cgroup actuation."""

    def __init__(
        self,
        policy: KRLSPolicy | None = None,
        actuator: CgroupV2Actuator | None = None,
        apply: bool = False,
        logger: JsonlLogger | None = None,
    ) -> None:
        self.policy = policy or KRLSPolicy()
        self.actuator = actuator or CgroupV2Actuator()
        self.apply = bool(apply)
        self.logger = logger

    def step(self, snapshot: ContextSnapshot, apply: bool | None = None, pid: int | None = None) -> dict[str, Any]:
        memory = measure_memory()
        features = collect_features(snapshot, memory)
        decision = self.policy.decide(features)
        action = ACTIONS_BY_ID[decision.action]
        actuate = self.apply if apply is None else bool(apply)
        applied = self.actuator.apply(action, pid=pid, dry_run=not actuate)
        payload = decision.as_dict(applied=applied)
        payload["actuator"] = self.actuator.status()
        payload["memory"] = memory.as_dict()
        if self.logger:
            self.logger.log(
                "scheduler_decision",
                action=decision.action,
                policy=decision.policy,
                scores={key: round(value, 4) for key, value in decision.scores.items()},
                features=decision.features,
                applied=applied,
                mock=self.logger.mock,
            )
        return payload

    def observe(self, features: ScheduleFeatures, action_id: str, reward: float) -> None:
        self.policy.update(features, action_id, reward)

    def restore(self) -> dict[str, Any]:
        return self.actuator.restore()
