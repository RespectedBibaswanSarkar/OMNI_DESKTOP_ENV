from __future__ import annotations
import sqlite3
from pathlib import Path


class SQLiteStore:
    def __init__(self, db_path: str | Path = "./omni_data.db"):
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, description TEXT, mode TEXT, workspace TEXT, status TEXT, final_report TEXT, created_at TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, event_type TEXT, payload TEXT, created_at TEXT)")
        conn.commit()
        conn.close()

    def save_task(self, task_id: str, description: str, mode: str, workspace: str, status: str, final_report: str | None = None) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO tasks (id, description, mode, workspace, status, final_report, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now')) ON CONFLICT(id) DO UPDATE SET description=excluded.description, mode=excluded.mode, workspace=excluded.workspace, status=excluded.status, final_report=excluded.final_report, updated_at=datetime('now')", (task_id, description, mode, workspace, status, final_report))
        conn.commit()
        conn.close()

    def save_event(self, task_id: str, event_type: str, payload: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO events (task_id, event_type, payload, created_at) VALUES (?, ?, ?, datetime('now'))", (task_id, event_type, payload))
        conn.commit()
        conn.close()

    def list_tasks(self) -> list[dict]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT id, description, mode, workspace, status, final_report, created_at, updated_at FROM tasks ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(zip(["id", "description", "mode", "workspace", "status", "final_report", "created_at", "updated_at"], row)) for row in rows]
