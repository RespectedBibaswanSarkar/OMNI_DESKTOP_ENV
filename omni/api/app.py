from __future__ import annotations
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from omni.config import settings
from omni.orchestrator.engine import Orchestrator
from omni.planner.demo import DemoPlanner
from omni.storage.sqlite_store import SQLiteStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = SQLiteStore(Path("./omni_data.db"))
    app.state.orchestrator = Orchestrator(workspace_root=str(settings.workspace_root), store=app.state.store)
    yield


app = FastAPI(title="OMNI API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"], allow_methods=["*"], allow_headers=["*"])

static_dir = Path(__file__).resolve().parents[2] / "dashboard"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "mode": settings.mode, "workspace": str(settings.workspace_root)}


@app.get("/")
def root() -> Any:
    return {
        "message": "OMNI API",
        "dashboard": "/static/index.html",
        "docs": "/docs",
    }


@app.post("/tasks")
def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    goal = str(payload.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is required")
    task = app.state.orchestrator.create_task(goal)
    return {"task_id": task.task_id, "status": task.status, "plan": [step.model_dump() for step in task.plan.steps]}


@app.get("/tasks")
def list_tasks() -> list[dict[str, Any]]:
    return app.state.store.list_tasks()


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    task = app.state.orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task.task_id, "status": task.status, "plan": [step.model_dump() for step in task.plan.steps], "final_report": task.final_report}


@app.post("/tasks/{task_id}/approve")
def approve_task(task_id: str) -> dict[str, Any]:
    task = app.state.orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.approved = True
    task.status = "READY"
    return {"task_id": task_id, "status": "approved"}


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, Any]:
    task = app.state.orchestrator.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "CANCELLED"
    return {"task_id": task_id, "status": "CANCELLED"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
