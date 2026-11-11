from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Settings:
    workspace_root: Path = field(default_factory=lambda: Path(os.environ.get("OMNI_WORKSPACE", "./workspace")).resolve())
    mode: str = os.environ.get("OMNI_MODE", "demo")
    host: str = os.environ.get("OMNI_HOST", "127.0.0.1")
    port: int = int(os.environ.get("OMNI_PORT", "8000"))
    demo_project_name: str = "omni_demo_project"
    model_timeout: float = 15.0
    action_timeout: float = 30.0
    max_retries: int = 2

    def ensure_workspace(self) -> Path:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        return self.workspace_root


settings = Settings()
