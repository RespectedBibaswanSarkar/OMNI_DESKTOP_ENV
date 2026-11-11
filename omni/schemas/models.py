from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ActionPermission(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    CONSEQUENTIAL = "consequential"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class TaskMode(str, Enum):
    DEMO = "demo"
    LOCAL_MODEL = "local_model"


class VerificationResult(BaseModel):
    expected_condition: str
    observed_condition: str
    passed: bool
    evidence: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    failure_reason: str | None = None


class TaskAction(BaseModel):
    id: str
    name: str
    description: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    expected_postconditions: list[str] = Field(default_factory=list)
    timeout: float = 30.0
    retry_limit: int = 1
    max_retries_used: int = 0
    permission: ActionPermission = ActionPermission.SAFE
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    verification: VerificationResult | None = None
    user_approval_required: bool = False
    approved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Action name is required.")
        return v


class TaskPlan(BaseModel):
    task_id: str
    description: str
    mode: TaskMode = TaskMode.DEMO
    steps: list[TaskAction]


class TaskRecord(BaseModel):
    task_id: str
    description: str
    mode: TaskMode = TaskMode.DEMO
    workspace: str
    status: str = "PENDING"
    plan: TaskPlan | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    final_report: str | None = None


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, provider: str | None = None):
        super().__init__(message)
        self.provider = provider


class ActionValidationError(ValueError):
    pass
