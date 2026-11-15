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
    "auto_approve_reversible": False,   # True: launch/focus/workspace/volume run without a prompt
    "apply_scheduler": False,           # True: KRLS actually writes cgroup weights (kill switch on exit)
    "notify_results": True,             # desktop notification when a task finishes
    "speak_results": False,             # speak the result aloud
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
        auto_approve_reversible=bool(app.state.settings.get("auto_approve_reversible", False)),
        apply_scheduler=bool(app.state.settings.get("apply_scheduler", False)),
        notify_results=bool(app.state.settings.get("notify_results", True)),
        speak_results=bool(app.state.settings.get("speak_results", False)),
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


@app.post("/api/tasks/{task_id}/confirm")
def confirm_task(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Approve or deny the desktop action a task is waiting on (HUD / native app buttons)."""
    ok = app.state.pipeline.resolve_confirmation(task_id, bool(payload.get("approve", False)))
    if not ok:
        raise HTTPException(status_code=409, detail="Task is not awaiting confirmation")
    return {"task_id": task_id, "approved": bool(payload.get("approve", False))}


# ---------------------------------------------------- desktop (direct, no LLM)
@app.get("/api/desktop/apps")
def desktop_apps() -> dict[str, Any]:
    names = app.state.pipeline.desktop.apps.names()
    return {"count": len(names), "apps": names}


@app.get("/api/desktop/windows")
def desktop_windows() -> dict[str, Any]:
    desk = app.state.pipeline.desktop
    return {"windows": desk.clients(), "backend": desk.backend.status()}


@app.get("/api/desktop/status")
def desktop_status() -> dict[str, Any]:
    """Is window control wired up? (GNOME: is the OMNI Bridge extension reachable?)"""
    desk = app.state.pipeline.desktop
    return {**desk.backend.status(), "apps_indexed": len(desk.apps.entries()),
            "learned_plans": len(app.state.pipeline.plans.entries), "routines": app.state.pipeline.plans.names()}


@app.post("/api/desktop/run")
def desktop_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Test one registry action directly, bypassing planning: isolates registry bugs from LLM bugs.

    Body: {"action": "workspace.switch", "args": {"workspace": 2}, "confirm": true}
    """
    desk = app.state.pipeline.desktop
    try:
        prepared = desk.prepare(str(payload.get("action", "")), payload.get("args") or {})
        if prepared.confirmation_required:
            if not payload.get("confirm"):
                return {"confirmation_required": True, "tier": prepared.tier}
            prepared = desk.confirm(prepared.confirmation_token)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    verified = desk.verify(prepared.action, payload.get("args") or {}) if prepared.success else None
    return {"success": prepared.success, "output": prepared.output, "tier": prepared.tier, "verified": verified}


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
    pipe.multimodel.offline = offline
    if "auto_approve_reversible" in payload:
        pipe.auto_approve_reversible = bool(payload["auto_approve_reversible"])
    for flag in ("notify_results", "speak_results"):
        if flag in payload:
            setattr(pipe, flag, bool(payload[flag]))
    if "apply_scheduler" in payload:
        was = pipe.apply_scheduler
        pipe.apply_scheduler = bool(payload["apply_scheduler"])
        if was and not pipe.apply_scheduler:
            pipe.actuator.restore()          # turning actuation off restores original cgroup weights
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
