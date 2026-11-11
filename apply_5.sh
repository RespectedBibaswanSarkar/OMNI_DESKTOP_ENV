#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

def patch(path, replacements):
    file = Path(path)
    source = file.read_text(encoding="utf-8")
    original = source

    for old, new in replacements:
        if old in source:
            source = source.replace(old, new, 1)
        elif new not in source:
            raise SystemExit(
                f"PATCH ABORTED: expected anchor missing in {path}:\n{old[:180]}"
            )

    if source != original:
        backup = file.with_name(file.name + ".bak")
        if not backup.exists():
            backup.write_text(original, encoding="utf-8")
        file.write_text(source, encoding="utf-8")
        print(f"Updated {path}")
    else:
        print(f"Already patched: {path}")


patch("omni/events/bus.py", [
    (
        "import asyncio\nimport inspect\n",
        "import asyncio\nimport inspect\nimport threading\n",
    ),
    (
        "        self._lock = asyncio.Lock()\n",
        "        self._lock = threading.RLock()\n",
    ),
    (
        "        async with self._lock:\n            self._handlers[topic].append(handler)\n",
        "        with self._lock:\n            self._handlers[topic].append(handler)\n",
    ),
    (
        "            async with self._lock:\n                handlers = self._handlers.get(topic, [])\n                if handler in handlers:\n                    handlers.remove(handler)\n",
        "            with self._lock:\n                handlers = self._handlers.get(topic, [])\n                if handler in handlers:\n                    handlers.remove(handler)\n",
    ),
    (
        "        async with self._lock:\n            handlers = tuple(self._handlers.get(topic, ()))\n",
        "        with self._lock:\n            handlers = tuple(self._handlers.get(topic, ()))\n",
    ),
    (
        "        async with self._lock:\n            return len(self._handlers.get(topic, ()))\n",
        "        with self._lock:\n            return len(self._handlers.get(topic, ()))\n",
    ),
])

patch("omni/orchestrator/engine.py", [
    (
        "from __future__ import annotations\nimport json\n",
        "from __future__ import annotations\nimport asyncio\nimport json\n",
    ),
    (
        "from omni.verification.engine import VerificationEngine\n",
        "from omni.verification.engine import VerificationEngine\nfrom omni.events.bus import EventBus\n",
    ),
    (
        "class Orchestrator:\n    def __init__(self, workspace_root: str | None = None, store: SQLiteStore | None = None):\n",
        "class Orchestrator:\n    def __init__(self, workspace_root: str | None = None, store: SQLiteStore | None = None, event_bus: EventBus | None = None):\n",
    ),
    (
        "        self._tasks: dict[str, TaskRecord] = {}\n\n    def create_task",
        "        self._tasks: dict[str, TaskRecord] = {}\n        self.events = event_bus or EventBus()\n\n    def _publish_event(self, topic: str, payload: dict[str, Any]) -> None:\n        \"\"\"Publish from this synchronous orchestrator using a short-lived loop.\n\n        EventBus protects its subscriber registry with a thread lock, so the\n        bus can safely be reused across these synchronous publication cycles.\n        \"\"\"\n        try:\n            asyncio.get_running_loop()\n        except RuntimeError:\n            asyncio.run(self.events.publish(topic, payload))\n            return\n        raise RuntimeError(\n            \"Synchronous Orchestrator cannot publish from an active event loop; \"\n            \"call it from a worker thread.\"\n        )\n\n    def create_task",
    ),
    (
        "        self.store.save_event(task_id, \"plan_created\", json.dumps({\"step_count\": len(plan.steps)}))\n        return task\n",
        "        self.store.save_event(task_id, \"plan_created\", json.dumps({\"step_count\": len(plan.steps)}))\n        self._publish_event(\"modules.plan\", {\n            \"task_id\": task_id,\n            \"goal\": goal,\n            \"step_count\": len(plan.steps),\n            \"steps\": [\n                {\"id\": step.id, \"name\": step.name, \"description\": step.description}\n                for step in plan.steps\n            ],\n        })\n        return task\n",
    ),
    (
        "        task = self._tasks[task_id]\n        task.status = \"RUNNING\"\n",
        "        task = self._tasks[task_id]\n        task.status = \"RUNNING\"\n        self._publish_event(\"intent.decision\", {\n            \"task_id\": task_id,\n            \"status\": task.status,\n            \"description\": task.description,\n        })\n",
    ),
    (
        "                task.status = \"WAITING_FOR_APPROVAL\"\n                break\n",
        "                task.status = \"WAITING_FOR_APPROVAL\"\n                self._publish_event(\"intent.decision\", {\n                    \"task_id\": task_id,\n                    \"step_id\": step.id,\n                    \"status\": task.status,\n                    \"reason\": \"user_approval_required\",\n                })\n                break\n",
    ),
    (
        "                    self.store.save_event(task_id, \"step_success\", json.dumps({\"step_id\": step.id, \"result\": step.result}))\n",
        "                    self.store.save_event(task_id, \"step_success\", json.dumps({\"step_id\": step.id, \"result\": step.result}))\n                    self._publish_event(\"task.reward\", {\n                        \"value\": 1.0,\n                        \"agent_id\": task_id,\n                        \"action_id\": step.id,\n                        \"metadata\": {\"status\": \"step_succeeded\", \"action\": step.name},\n                    })\n",
    ),
    (
        "                        self.store.save_event(task_id, \"step_retry\", json.dumps({\"step_id\": step.id, \"reason\": verification.failure_reason}))\n",
        "                        self.store.save_event(task_id, \"step_retry\", json.dumps({\"step_id\": step.id, \"reason\": verification.failure_reason}))\n                        self._publish_event(\"task.reward\", {\n                            \"value\": -0.25,\n                            \"agent_id\": task_id,\n                            \"action_id\": step.id,\n                            \"metadata\": {\"status\": \"retry_pending\", \"reason\": verification.failure_reason},\n                        })\n",
    ),
    (
        "                        self.store.save_event(task_id, \"step_failed\", json.dumps({\"step_id\": step.id, \"error\": verification.failure_reason}))\n                        break\n",
        "                        self.store.save_event(task_id, \"step_failed\", json.dumps({\"step_id\": step.id, \"error\": verification.failure_reason}))\n                        self._publish_event(\"task.reward\", {\n                            \"value\": -1.0,\n                            \"agent_id\": task_id,\n                            \"action_id\": step.id,\n                            \"metadata\": {\"status\": \"step_failed\", \"reason\": verification.failure_reason},\n                        })\n                        break\n",
    ),
    (
        "                self.store.save_event(task_id, \"step_exception\", json.dumps({\"step_id\": step.id, \"error\": str(exc)}))\n                break\n",
        "                self.store.save_event(task_id, \"step_exception\", json.dumps({\"step_id\": step.id, \"error\": str(exc)}))\n                self._publish_event(\"task.reward\", {\n                    \"value\": -1.0,\n                    \"agent_id\": task_id,\n                    \"action_id\": step.id,\n                    \"metadata\": {\"status\": \"step_exception\", \"error\": str(exc)},\n                })\n                break\n",
    ),
    (
        "        self.store.save_task(task_id, task.description, task.mode.value, task.workspace, task.status, task.final_report)\n        return task\n",
        "        self.store.save_task(task_id, task.description, task.mode.value, task.workspace, task.status, task.final_report)\n        self._publish_event(\"intent.decision\", {\n            \"task_id\": task_id,\n            \"status\": task.status,\n            \"final_report\": task.final_report,\n        })\n        return task\n",
    ),
])

print("Phase 5.1 patch complete.")
PY
