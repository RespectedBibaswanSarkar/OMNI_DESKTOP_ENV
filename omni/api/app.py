from __future__ import annotations
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from omni.config import settings
from omni.llm.client import LLMClient
from omni.local_models import LocalModelCenter
from omni.orchestrator.engine import Orchestrator
from omni.pipeline import OmniPipeline
from omni.routing.iars import IARS
from omni.storage.sqlite_store import SQLiteStore
from omni.system_stats import snapshot as system_snapshot

CACHE_DIR = Path(os.environ.get("OMNI_CACHE_DIR", Path.home() / ".cache" / "omni"))
SETTINGS_PATH = CACHE_DIR / "dashboard_settings.json"
STATE_PATH = CACHE_DIR / "hud" / "state.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "ollama_url": os.environ.get("OMNI_OLLAMA_URL", "http://127.0.0.1:11434"),
    "preferred_model": "",
    "auto_select": True,
    "max_gen_tokens": 512,
    "gen_timeout_s": 90,
    "safe_execution": True,
    "offline_mode": False,
}


def _load_settings() -> dict[str, Any]:
    stored: dict[str, Any] = {}
    try:
        stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    return merged


def _save_settings(values: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = SQLiteStore(Path("./omni_data.db"))
    app.state.orchestrator = Orchestrator(workspace_root=str(settings.workspace_root), store=app.state.store)
    app.state.settings = _load_settings()
    app.state.model_center = LocalModelCenter(url=app.state.settings["ollama_url"])
    offline = bool(app.state.settings.get("offline_mode")) or os.environ.get("OMNI_LLM_MOCK", "") in {"1", "true"}
    app.state.llm = LLMClient(timeout=float(app.state.settings.get("gen_timeout_s", 90)), mock=offline)
    app.state.pipeline = OmniPipeline(
        workspace_root=settings.ensure_workspace(),
        model_center=app.state.model_center,
        llm=app.state.llm,
        hud_path=STATE_PATH,
        offline=offline,
        gen_timeout=float(app.state.settings.get("gen_timeout_s", 90)),
        max_gen_tokens=int(app.state.settings.get("max_gen_tokens", 512)),
    )
    app.state.pipeline.start()
    thread = threading.Thread(target=app.state.model_center.refresh, name="omni-model-refresh", daemon=True)
    thread.start()
    try:
        yield
    finally:
        app.state.pipeline.stop()
        app.state.orchestrator.close() if hasattr(app.state.orchestrator, "close") else None


app = FastAPI(title="OMNI API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["*"], allow_headers=["*"])

static_dir = Path(__file__).resolve().parents[2] / "dashboard"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "mode": settings.mode, "workspace": str(settings.workspace_root)}


@app.get("/")
def root() -> Any:
    return {"message": "OMNI API", "dashboard": "/static/index.html", "docs": "/docs"}


# --------------------------------------------------------------- A. state
@app.get("/api/state")
def get_state() -> dict[str, Any]:
    pipe: OmniPipeline = app.state.pipeline
    mc: LocalModelCenter = app.state.model_center
    if mc.reachable is None and not mc.models:
        mc.refresh()
    recent = [t.as_dict(include_result=False) for t in pipe.history[-8:]][::-1]
    workload = pipe.workload()
    sysinfo = system_snapshot(settings.workspace_root)
    return {
        "settings": app.state.settings,
        "inference": {
            "reachable": mc.reachable,
            "error": mc.last_error,
            "url": mc.url,
            "model_count": len(mc.models),
            "loaded": [m.tag for m in mc.models.values() if m.loaded],
            "active": workload["inference_active"],
            "mock": pipe.llm.is_mock,
        },
        "active_model": mc.selected if not mc.auto_select else None,
        "system": sysinfo,
        "workload": workload,
        "recent_tasks": recent,
        "desktop_actions": pipe.desktop.describe(),
        "hud_state_file": str(pipe.hud_path),
    }


# ----------------------------------------------------------- B. tasks (AI)
@app.post("/api/tasks")
def submit_task(payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required")
    if not app.state.settings.get("safe_execution", True):
        raise HTTPException(status_code=403, detail="Safe execution mode is disabled")
    result = app.state.pipeline.submit(goal)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/tasks")
def list_tasks() -> list[dict[str, Any]]:
    return [t.as_dict(include_result=False) for t in app.state.pipeline.history[-50:]][::-1]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    record = app.state.pipeline.tasks.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return record.as_dict()


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, Any]:
    ok = app.state.pipeline.cancel(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "cancel_requested": True}


# ------------------------------------------------------- C. observatory
@app.get("/api/algorithms")
def algorithms() -> dict[str, Any]:
    return app.state.pipeline.observatory()


# ------------------------------------------------------- D. model center
@app.get("/api/models")
def get_models() -> dict[str, Any]:
    mc: LocalModelCenter = app.state.model_center
    return mc.status()


@app.post("/api/models/refresh")
def refresh_models() -> dict[str, Any]:
    return app.state.model_center.refresh()


@app.post("/api/models/select")
def select_model(payload: dict[str, Any]) -> dict[str, Any]:
    mc: LocalModelCenter = app.state.model_center
    model = payload.get("model")
    if model in (None, ""):
        mc.selected = None
        return {"selected": None, "auto": True}
    if model not in mc.models:
        raise HTTPException(status_code=404, detail=f"Unknown model: {model}")
    mc.selected = model
    mc.auto_select = bool(payload.get("auto", False))
    return {"selected": model, "auto": mc.auto_select}


# ----------------------------------------------------------- E. monitor
@app.get("/api/system")
def system_metrics() -> dict[str, Any]:
    return system_snapshot(settings.workspace_root)


# ----------------------------------------------------------- G. settings
@app.post("/api/settings")
def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    app.state.settings.update({k: payload[k] for k in DEFAULT_SETTINGS if k in payload})
    _save_settings(app.state.settings)
    mc: LocalModelCenter = app.state.model_center
    if payload.get("ollama_url"):
        mc.url = str(payload["ollama_url"]).rstrip("/")
        mc.models = {}
        mc.reachable = None
        threading.Thread(target=mc.refresh, daemon=True).start()
    if payload.get("preferred_model") is not None:
        mc.preferred = payload["preferred_model"] or None
    if payload.get("auto_select") is not None:
        mc.auto_select = bool(payload["auto_select"])
    offline = bool(payload.get("offline_mode", False))
    pipe: OmniPipeline = app.state.pipeline
    pipe.offline = offline
    pipe.llm.backend = "mock" if offline else "ollama"
    return {"settings": app.state.settings}


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return {"settings": app.state.settings}


# ------------------------------------------------- legacy orchestrator API
@app.post("/tasks")
def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required")
    task = app.state.orchestrator.create_task(goal)
    return {"task_id": task.task_id, "status": task.status, "plan": [step.model_dump() for step in task.plan.steps]}


@app.get("/tasks")
def legacy_tasks() -> list[dict[str, Any]]:
    return app.state.store.list_tasks()


@app.get("/tasks/{task_id}")
def legacy_task(task_id: str) -> dict[str, Any]:
    task = app.state.orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task.task_id, "status": task.status, "plan": [step.model_dump() for step in task.plan.steps], "final_report": task.final_report}
