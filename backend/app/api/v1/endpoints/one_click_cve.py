from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.agent_task import AgentTask, AgentTaskStatus
from app.models.one_click_cve import (
    OneClickCveBatch,
    OneClickCveBatchProject,
    OneClickCveBatchStatus,
    OneClickCveProjectStatus,
)
from app.models.user import User
from app.services.agent.task_executor import request_agent_task_cancellation
from app.services.one_click_cve.runner import run_one_click_cve_batch
from app.services.one_click_cve.task_queue import (
    enqueue_one_click_cve_batch,
    should_use_one_click_cve_worker_queue,
)

router = APIRouter()

ONE_CLICK_CVE_MODEL_PREFLIGHT_STEP = "\u6b63\u5728\u6d4b\u8bd5\u6a21\u578b\u8fde\u901a\u6027"
ONE_CLICK_CVE_QUEUED_STEP = "\u7b49\u5f85\u4e00\u952e CVE worker \u6267\u884c"
ACTIVE_ONE_CLICK_CVE_AGENT_TASK_STATUSES = {
    AgentTaskStatus.PENDING,
    AgentTaskStatus.INITIALIZING,
    AgentTaskStatus.RUNNING,
    AgentTaskStatus.PLANNING,
    AgentTaskStatus.INDEXING,
    AgentTaskStatus.ANALYZING,
    AgentTaskStatus.VERIFYING,
    AgentTaskStatus.REPORTING,
}
ACTIVE_ONE_CLICK_CVE_PROJECT_STATUSES = {
    OneClickCveProjectStatus.IMPORTING,
    OneClickCveProjectStatus.AUDITING,
}


class OneClickCveBatchCreate(BaseModel):
    target_count: int = Field(..., ge=1, le=20)
    prefer_security_advisory: bool = True


class OneClickCveProjectResponse(BaseModel):
    id: str
    batch_id: str
    project_id: str | None = None
    agent_task_id: str | None = None
    github_full_name: str
    repository_url: str
    description: str | None = None
    language: str | None = None
    stars: int
    pushed_at: datetime | None = None
    updated_at: datetime | None = None
    default_branch: str | None = None
    version_label: str | None = None
    version_source: str | None = None
    has_security_advisory: bool
    advisory_count: int
    has_security_policy: bool
    has_private_vulnerability_reporting: bool = False
    score: float
    status: str
    findings_count: int
    error_message: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class OneClickCveBatchResponse(BaseModel):
    id: str
    user_id: str
    requested_count: int
    found_count: int
    status: str
    current_step: str | None = None
    error_message: str | None = None
    summary_json: dict[str, Any] | None = None
    prefer_security_advisory: bool = True
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    projects: list[OneClickCveProjectResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


async def _get_owned_batch(batch_id: str, db: AsyncSession, current_user: User) -> OneClickCveBatch:
    result = await db.execute(
        select(OneClickCveBatch)
        .options(selectinload(OneClickCveBatch.projects))
        .where(OneClickCveBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="One-click CVE batch not found")
    if batch.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this batch")
    return batch


async def _cancel_active_batch_agent_tasks(db: AsyncSession, batch_id: str) -> None:
    result = await db.execute(
        select(AgentTask)
        .join(OneClickCveBatchProject, OneClickCveBatchProject.agent_task_id == AgentTask.id)
        .where(
            OneClickCveBatchProject.batch_id == batch_id,
            OneClickCveBatchProject.status.in_(ACTIVE_ONE_CLICK_CVE_PROJECT_STATUSES),
            AgentTask.status.in_(ACTIVE_ONE_CLICK_CVE_AGENT_TASK_STATUSES),
        )
    )
    tasks = list(result.scalars().all())
    completed_at = datetime.now(timezone.utc)
    for task in tasks:
        request_agent_task_cancellation(str(task.id))
        task.status = AgentTaskStatus.CANCELLED
        task.completed_at = completed_at
        task.error_message = task.error_message or "Cancelled by one-click CVE batch cancellation"


def _batch_response(batch: OneClickCveBatch) -> OneClickCveBatchResponse:
    projects = list(batch.projects) if "projects" in getattr(batch, "__dict__", {}) else []
    summary = batch.summary_json if isinstance(batch.summary_json, dict) else {}
    return OneClickCveBatchResponse(
        id=batch.id,
        user_id=batch.user_id,
        requested_count=batch.requested_count,
        found_count=batch.found_count,
        status=batch.status,
        current_step=batch.current_step,
        error_message=batch.error_message,
        summary_json=batch.summary_json,
        prefer_security_advisory=bool(summary.get("prefer_security_advisory", True)),
        started_at=batch.started_at,
        completed_at=batch.completed_at,
        created_at=batch.created_at,
        projects=[OneClickCveProjectResponse.model_validate(project) for project in projects],
    )


@router.post("/batches", response_model=OneClickCveBatchResponse)
async def create_one_click_cve_batch(
    payload: OneClickCveBatchCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> OneClickCveBatchResponse:
    use_worker_queue = should_use_one_click_cve_worker_queue()
    batch = OneClickCveBatch(
        user_id=current_user.id,
        requested_count=payload.target_count,
        found_count=0,
        status=OneClickCveBatchStatus.PENDING,
        current_step=ONE_CLICK_CVE_QUEUED_STEP if use_worker_queue else ONE_CLICK_CVE_MODEL_PREFLIGHT_STEP,
        summary_json={"prefer_security_advisory": payload.prefer_security_advisory},
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    if use_worker_queue:
        await enqueue_one_click_cve_batch(batch.id)
    else:
        background_tasks.add_task(run_one_click_cve_batch, batch.id)
    return _batch_response(batch)


@router.get("/batches", response_model=list[OneClickCveBatchResponse])
async def list_one_click_cve_batches(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    limit: int = Query(30, ge=1, le=100),
) -> list[OneClickCveBatchResponse]:
    result = await db.execute(
        select(OneClickCveBatch)
        .options(selectinload(OneClickCveBatch.projects))
        .where(OneClickCveBatch.user_id == current_user.id)
        .order_by(OneClickCveBatch.created_at.desc())
        .limit(limit)
    )
    return [_batch_response(batch) for batch in result.scalars().all()]


@router.get("/batches/{batch_id}", response_model=OneClickCveBatchResponse)
async def get_one_click_cve_batch(
    batch_id: str,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> OneClickCveBatchResponse:
    batch = await _get_owned_batch(batch_id, db, current_user)
    return _batch_response(batch)


@router.post("/batches/{batch_id}/cancel", response_model=OneClickCveBatchResponse)
async def cancel_one_click_cve_batch(
    batch_id: str,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> OneClickCveBatchResponse:
    batch = await _get_owned_batch(batch_id, db, current_user)
    if batch.status in {OneClickCveBatchStatus.COMPLETED, OneClickCveBatchStatus.FAILED, OneClickCveBatchStatus.EXHAUSTED}:
        return _batch_response(batch)
    await _cancel_active_batch_agent_tasks(db, batch.id)
    batch.status = OneClickCveBatchStatus.CANCELLED
    batch.completed_at = datetime.now(timezone.utc)
    batch.current_step = "用户已取消"
    await db.commit()
    await db.refresh(batch)
    return _batch_response(batch)
