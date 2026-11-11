from __future__ import annotations

from typing import Any

from omni.events.bus import EventBus


class RoutingEventBridge:
    """Publishes existing RoutingService results without replacing its algorithms.

    RoutingService remains responsible for collecting context, IARS selection,
    AMPL planning, and optional KRLS scheduling. This bridge exposes those
    results as stable asynchronous events for other OMNI components.
    """

    def __init__(self, routing_service: Any, bus: EventBus | None = None) -> None:
        self.routing_service = routing_service
        self.bus = bus or EventBus()
        self.last_cycle: dict[str, Any] | None = None

    async def cycle(self) -> dict[str, Any]:
        result = self.routing_service.step()
        context = result.get("context", {})
        if not isinstance(context, dict):
            context = {"value": context}

        routing = result.get("routing", {})
        modules = result.get("modules", {})
        scheduler = result.get("scheduler", {})

        await self.bus.publish("context.update", context)
        await self.bus.publish("intent.decision", routing)
        await self.bus.publish("modules.plan", modules)
        await self.bus.publish("sched.load", scheduler)

        self.last_cycle = result
        return result

    async def reward(
        self,
        *,
        value: float,
        agent_id: str | None = None,
        action_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast an observed outcome; learning consumers subscribe here."""
        await self.bus.publish("task.reward", {
            "value": float(value),
            "agent_id": agent_id,
            "action_id": action_id,
            "metadata": metadata or {},
        })

    async def connect_iars(self, iars: Any) -> None:
        """Update the existing IARS model when a reward carries its feature vector."""
        async def update(event: Any) -> None:
            data = event.payload
            phi = data.get("metadata", {}).get("phi")
            agent_id = data.get("agent_id")
            if phi is not None and agent_id:
                iars.observe(phi, agent_id, float(data["value"]))

        await self.bus.subscribe("task.reward", update)

    async def connect_scheduler(self, scheduler: Any) -> None:
        """Update the existing KRLS policy from an observed scheduler outcome."""
        async def update(event: Any) -> None:
            data = event.payload
            metadata = data.get("metadata", {})
            features = metadata.get("scheduler_features")
            action_id = data.get("action_id")
            if features is not None and action_id:
                scheduler.observe(features, action_id, float(data["value"]))

        await self.bus.subscribe("task.reward", update)
