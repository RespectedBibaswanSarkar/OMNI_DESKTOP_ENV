from __future__ import annotations

import asyncio

import pytest

from omni.events import EventBus, RoutingEventBridge


def test_event_bus_preserves_subscription_order() -> None:
    async def run() -> None:
        bus = EventBus()
        seen = []

        async def first(event):
            await asyncio.sleep(0)
            seen.append(("first", event.topic))

        def second(event):
            seen.append(("second", event.topic))

        await bus.subscribe("context.update", first)
        await bus.subscribe("context.update", second)
        event = await bus.publish("context.update", {"app": "editor"})

        assert event.payload == {"app": "editor"}
        assert seen == [
            ("first", "context.update"),
            ("second", "context.update"),
        ]

    asyncio.run(run())


def test_event_bus_rejects_unknown_topic() -> None:
    async def run() -> None:
        bus = EventBus()
        with pytest.raises(ValueError):
            await bus.publish("unknown.topic", {})
        with pytest.raises(ValueError):
            await bus.subscribe("unknown.topic", lambda event: None)

    asyncio.run(run())


def test_event_bus_unsubscribe() -> None:
    async def run() -> None:
        bus = EventBus()
        seen = []
        remove = await bus.subscribe("task.reward", lambda event: seen.append(event))
        assert await bus.subscriber_count("task.reward") == 1
        remove()
        await bus.publish("task.reward", {"value": 1.0})
        assert seen == []
        assert await bus.subscriber_count("task.reward") == 0

    asyncio.run(run())


def test_bridge_publishes_all_four_cycle_topics() -> None:
    class FakeRouting:
        def step(self):
            return {
                "context": {"app_class": "code"},
                "routing": {"agent": "dev-terminal"},
                "modules": {"resident": ["code-tools"]},
                "scheduler": {"action": "balanced"},
            }

    async def run() -> None:
        bus = EventBus()
        seen = []
        for topic in (
            "context.update",
            "intent.decision",
            "modules.plan",
            "sched.load",
        ):
            await bus.subscribe(topic, lambda event: seen.append(event.topic))

        bridge = RoutingEventBridge(FakeRouting(), bus)
        result = await bridge.cycle()

        assert result["routing"]["agent"] == "dev-terminal"
        assert seen == [
            "context.update",
            "intent.decision",
            "modules.plan",
            "sched.load",
        ]

    asyncio.run(run())


def test_reward_fans_out_to_iars_and_scheduler() -> None:
    class FakeIARS:
        def __init__(self):
            self.updates = []

        def observe(self, phi, agent_id, reward):
            self.updates.append((phi, agent_id, reward))

    class FakeScheduler:
        def __init__(self):
            self.updates = []

        def observe(self, features, action_id, reward):
            self.updates.append((features, action_id, reward))

    async def run() -> None:
        iars = FakeIARS()
        scheduler = FakeScheduler()
        bridge = RoutingEventBridge(object())
        await bridge.connect_iars(iars)
        await bridge.connect_scheduler(scheduler)

        await bridge.reward(
            value=0.75,
            agent_id="dev-terminal",
            action_id="balanced",
            metadata={
                "phi": [0.1, 0.2],
                "scheduler_features": "captured-features",
            },
        )

        assert iars.updates == [([0.1, 0.2], "dev-terminal", 0.75)]
        assert scheduler.updates == [
            ("captured-features", "balanced", 0.75)
        ]

    asyncio.run(run())
