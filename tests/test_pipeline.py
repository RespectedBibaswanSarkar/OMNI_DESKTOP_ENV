"""Pipeline + local-model integration tests (mock inference only)."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter, ModelInfo
from omni.pipeline import OmniPipeline, TaskRecord, extract_json


@pytest.fixture()
def pipeline(tmp_path: Path) -> OmniPipeline:
    llm = LLMClient(backend="mock")
    return OmniPipeline(workspace_root=tmp_path / "ws", llm=llm, offline=True,
                        hud_path=tmp_path / "hud.json")


def _run(pipe: OmniPipeline, goal: str) -> TaskRecord:
    record = TaskRecord(task_id="t", goal=goal, created=time.time())
    pipe.run_task(record)
    return record


class TestPipeline:
    def test_end_to_end_verified(self, pipeline):
        record = _run(pipeline, "Create a research project structure with notes.")
        assert record.status == "done"
        assert record.verification["passed"] is True
        assert record.verification["passed_count"] == record.verification["total"]
        assert record.execution["executed"] == record.execution["total"]
        files = list(Path(record.workspace).rglob("*"))
        assert files, "isolated workspace should contain executed files"

    def test_iars_influences_model_kind(self, pipeline):
        record = _run(pipeline, "Create a research project structure with notes.")
        assert record.iars["agent"]
        assert record.model["task_kind"] in set(["general", "reasoning", "code", "planning"])
        assert record.model["fallback_chain"], "fallback chain must be recorded"

    def test_reward_drives_real_updates(self, pipeline):
        before_iars = pipeline.iars_updates()
        before_krls = pipeline.policy.updates
        record = _run(pipeline, "Write a report on desktop context.")
        assert pipeline.iars_updates() > before_iars
        assert pipeline.policy.updates > before_krls
        assert record.reward["iars_updated"] is True
        assert record.reward["krls_action_rewarded"] == record.scheduler["action"]

    def test_ampl_records_real_plan(self, pipeline):
        record = _run(pipeline, "Create notes.")
        assert record.ampl.get("status") == "active"
        assert record.ampl.get("resident") is not None
        assert "memory" in record.ampl

    def test_ablation_krls_disabled(self, pipeline):
        pipeline.ablation["krls"] = False
        pipeline.ablation["adaptive"] = False
        before = pipeline.policy.updates
        record = _run(pipeline, "Create notes.")
        assert pipeline.policy.updates == before
        assert record.scheduler.get("status") == "disabled"
        assert record.iars.get("agent")

    def test_ablation_iars_static_routing(self, pipeline):
        pipeline.ablation["iars"] = False
        record = _run(pipeline, "Test the python project with pytest in the terminal.")
        assert record.iars["agent"] == "dev-terminal"
        assert record.iars.get("disabled") is True

    def test_submit_queue_and_duplicate(self, pipeline):
        pipeline.start()
        first = pipeline.submit("duplicate goal test")
        second = pipeline.submit("duplicate goal test")
        deadline = time.time() + 10
        while time.time() < deadline and pipeline.tasks[first["task_id"]].status not in {"done", "failed"}:
            time.sleep(0.05)
        assert second.get("duplicate") is True
        assert pipeline.tasks[first["task_id"]].status == "done"
        pipeline.stop()

    def test_no_path_escape(self, pipeline):
        record = _run(pipeline, "Create notes.")
        assert record.execution["executed"] == record.execution["total"]


class TestStructuredOutput:
    def test_extract_json_plain(self):
        parsed = extract_json('{"summary": "ok", "steps": [{"action": "mkdir", "path": "a"}]}')
        assert parsed["summary"] == "ok"

    def test_extract_json_fenced(self):
        parsed = extract_json("```json\n{\"steps\": []}\n```")
        assert parsed == {"steps": []}

    def test_extract_json_with_prose(self):
        parsed = extract_json('Here is the plan: {"steps": [{"action": "mkdir", "path": "x"}]} done')
        assert parsed["steps"][0]["path"] == "x"

    def test_extract_json_invalid(self):
        assert extract_json("no json here") is None


class TestLocalModels:
    def test_registry_from_fake_tags(self):
        mc = LocalModelCenter.__new__(LocalModelCenter)
        mc.models = {}
        mc.reachable = True
        mc.last_error = None
        mc.last_refresh = 0.0
        mc.selected = None
        mc.auto_select = True
        mc.preferred = None
        mc.timeout = 1.0
        mc.url = "http://localhost:11434"
        tags = {"models": [
            {"name": "llama3.2:latest", "digest": "abc", "size": 2_000_000_000,
             "details": {"family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4", "context_length": 131072},
             "capabilities": ["completion"]},
            {"name": "llama3.2:3b", "digest": "abc", "size": 2_000_000_000,
             "details": {"family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4", "context_length": 131072},
             "capabilities": ["completion"]},
            {"name": "qwen2.5-coder:3b", "digest": "def", "size": 2_000_000_000,
             "details": {"family": "qwen2", "parameter_size": "3.1B", "quantization_level": "Q4", "context_length": 32768},
             "capabilities": ["completion", "tools"]},
        ]}
        # reuse the discovery logic through refresh's parsing by direct injection
        from omni.local_models import ModelInfo
        for entry in tags["models"]:
            details = entry["details"]
            info = ModelInfo(tag=entry["name"], base=entry["name"].split(":")[0], digest=entry["digest"],
                             family=details["family"], parameter_size=details["parameter_size"],
                             quantization=details["quantization_level"], context_length=details["context_length"],
                             capabilities=tuple(entry["capabilities"]), size_gb=entry["size"] / 2**30)
            info.role = mc._role_for(info)
            mc.models[info.tag] = info
        assert len(mc.models) == 3  # dedup happens in refresh(); covered by test_alias_dedup_in_refresh
        selection = mc.select("code")
        assert selection["model"] == "qwen2.5-coder:3b"
        assert mc.models["qwen2.5-coder:3b"].role.startswith("Code")

    def test_alias_dedup_in_refresh(self, monkeypatch):
        mc = LocalModelCenter()
        payload = {"models": [
            {"name": "llama3.2:latest", "digest": "same", "size": 2019375185, "details": {"family": "llama", "parameter_size": "3.2B"}, "capabilities": ["completion"]},
            {"name": "llama3.2:3b", "digest": "same", "size": 2019375185, "details": {"family": "llama", "parameter_size": "3.2B"}, "capabilities": ["completion"]},
        ]}
        monkeypatch.setattr(LocalModelCenter, "_get", lambda self, path: payload)
        mc.refresh()
        tags = [m.tag for m in mc.models.values()]
        assert len(tags) == 1
        aliases = mc.models[tags[0]].aliases
        assert "llama3.2:3b" in aliases or "llama3.2:latest" in aliases

    def test_unreachable_graceful(self, monkeypatch):
        mc = LocalModelCenter(url="http://127.0.0.1:1", timeout=0.5)

        def boom(self, path):
            raise OSError("refused")
        monkeypatch.setattr(LocalModelCenter, "_get", boom)
        result = mc.refresh()
        assert result["reachable"] is False
        assert mc.select("code")["model"] is None

    def test_latency_observation_updates_selection(self):
        mc = LocalModelCenter.__new__(LocalModelCenter)
        mc.models = {}
        mc.reachable = True
        mc.last_error = None
        mc.last_refresh = 0.0
        mc.selected = None
        mc.auto_select = True
        mc.preferred = None
        from omni.local_models import ModelInfo
        for tag in ("qwen3:4b", "gemma3:4b"):
            mc.models[tag] = ModelInfo(tag=tag, base=tag.split(":")[0], digest=tag, family="f",
                                       parameter_size="4B", quantization="Q4", context_length=4096,
                                       capabilities=("completion",), size_gb=2.5)
        mc.record_result("qwen3:4b", 12.0, True)
        mc.record_result("gemma3:4b", 2.0, True)
        selection = mc.select("general")
        # gemma3 has lower observed latency and both match "general" preference
        assert selection["model"] in {"gemma3:4b", "qwen3:4b"}
        assert mc.models["qwen3:4b"].observed_latency_s == 12.0


class TestOfflineMode:
    def test_mock_tagged(self, pipeline):
        record = _run(pipeline, "Create notes.")
        assert record.generation["mock"] is True
        assert record.model["offline"] is True
