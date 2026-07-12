from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.agent_task import AgentTask, AgentTaskStatus
from app.models.audit_session import AuditCheckpoint, AuditModelStreamAttempt, AuditSession
from app.models.one_click_cve import (
    OneClickCveBatch,
    OneClickCveBatchProject,
    OneClickCveBatchStatus,
    OneClickCveProjectStatus,
)
from app.models.user import User
from app.services.agent.task_executor import request_agent_task_cancellation
from app.api.v1.endpoints.audit_sessions import AuditSessionResumeResponse, queue_runtime_session_resume
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
    target_count: int = Field(..., ge=1, le=10)
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
    can_resume: bool = False
    last_error_kind: str | None = None
    resume_session_id: str | None = None
    resume_status: str | None = None
    resume_attempt: int | None = None
    resume_max_attempts: int | None = None

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


async def _cancel_active_batch_agent_tasks(db: AsyncSession, batch_id: str) -> list[str]:
    project_result = await db.execute(
        select(OneClickCveBatchProject).where(
            OneClickCveBatchProject.batch_id == batch_id,
            OneClickCveBatchProject.status.in_(ACTIVE_ONE_CLICK_CVE_PROJECT_STATUSES),
        )
    )
    projects = list(project_result.scalars().all())
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
    for project in projects:
        project.status = OneClickCveProjectStatus.CANCELLED
        project.error_message = project.error_message or "Cancelled by one-click CVE batch cancellation"
        project.updated_at_local = completed_at
    for task in tasks:
        request_agent_task_cancellation(str(task.id))
        task.status = AgentTaskStatus.CANCELLED
        task.completed_at = completed_at
        task.error_message = task.error_message or "Cancelled by one-click CVE batch cancellation"
    return [str(task.id) for task in tasks]


async def _wait_for_manual_cancel_checkpoints(db: AsyncSession, task_ids: list[str]) -> None:
    if not task_ids:
        return
    for _ in range(6):
        await asyncio.sleep(0.5)
        sessions = list(
            (
                await db.execute(
                    select(AuditSession)
                    .where(AuditSession.task_id.in_(task_ids), AuditSession.runtime_stack == "runtime")
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
        )
        if not sessions:
            return
        ready = True
        for session in sessions:
            checkpoint = await db.scalar(
                select(AuditCheckpoint)
                .where(AuditCheckpoint.session_id == session.id)
                .order_by(AuditCheckpoint.created_at.desc())
                .limit(1)
            )
            payload = dict(checkpoint.state_payload or {}) if checkpoint is not None else {}
            if session.state != "failed" or payload.get("checkpoint_kind") != "manual_cancelled":
                ready = False
                break
        if ready:
            return


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
    await _decorate_batch_resume_state(db, batch)
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
    batches = list(result.scalars().all())
    for batch in batches:
        await _decorate_batch_resume_state(db, batch)
    return [_batch_response(batch) for batch in batches]


@router.get("/batches/{batch_id}", response_model=OneClickCveBatchResponse)
async def get_one_click_cve_batch(
    batch_id: str,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> OneClickCveBatchResponse:
    batch = await _get_owned_batch(batch_id, db, current_user)
    await _decorate_batch_resume_state(db, batch)
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
    cancelled_task_ids = await _cancel_active_batch_agent_tasks(db, batch.id)
    batch.status = OneClickCveBatchStatus.CANCELLED
    batch.completed_at = datetime.now(timezone.utc)
    batch.current_step = "用户已取消"
    await db.commit()
    await _wait_for_manual_cancel_checkpoints(db, cancelled_task_ids)
    await db.refresh(batch)
    await _decorate_batch_resume_state(db, batch)
    return _batch_response(batch)


@router.post(
    "/batches/{batch_id}/projects/{batch_project_id}/resume",
    response_model=AuditSessionResumeResponse,
    status_code=202,
)
async def resume_one_click_cve_project(
    batch_id: str,
    batch_project_id: str,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> AuditSessionResumeResponse:
    batch = await _get_owned_batch(batch_id, db, current_user)
    project = await db.scalar(
        select(OneClickCveBatchProject).where(
            OneClickCveBatchProject.id == batch_project_id,
            OneClickCveBatchProject.batch_id == batch_id,
        )
    )
    if project is None:
        raise HTTPException(status_code=404, detail="One-click CVE project not found")
    if not project.agent_task_id:
        raise HTTPException(status_code=409, detail="Project has no audit task to resume")
    session = await db.scalar(
        select(AuditSession)
        .where(AuditSession.task_id == project.agent_task_id, AuditSession.runtime_stack == "runtime")
        .order_by(AuditSession.created_at.desc())
        .limit(1)
    )
    if session is None:
        raise HTTPException(status_code=409, detail="No persisted audit session is available to resume")
    # Persist the project transition in the same transaction that claims the
    # session, before the durable job becomes visible to a worker. Otherwise a
    # very fast worker can finish and then be overwritten back to AUDITING.
    project.status = OneClickCveProjectStatus.AUDITING
    project.error_message = None
    project.updated_at_local = datetime.now(timezone.utc)
    try:
        session, queued = await queue_runtime_session_resume(
            session_id=session.id,
            current_user_id=current_user.id,
            db=db,
        )
    except HTTPException as exc:
        if exc.status_code == 503:
            project.status = OneClickCveProjectStatus.FAILED
            project.error_message = "继续审计队列不可用，已停止整个一键 CVE"
            project.updated_at_local = datetime.now(timezone.utc)
            batch.status = OneClickCveBatchStatus.FAILED
            batch.error_message = project.error_message
            batch.current_step = project.error_message
            batch.completed_at = datetime.now(timezone.utc)
            await db.commit()
        raise
    if not queued:
        return AuditSessionResumeResponse(session_id=session.id, status="running", message="Audit session is already running")
    return AuditSessionResumeResponse(session_id=session.id, status="running", message="Audit session resume job queued")


async def _decorate_batch_resume_state(db: AsyncSession, batch: OneClickCveBatch) -> None:
    projects = list(batch.projects) if "projects" in getattr(batch, "__dict__", {}) else []
    task_ids = [str(project.agent_task_id) for project in projects if project.agent_task_id]
    if not task_ids:
        return
    sessions = list(
        (
            await db.execute(
                select(AuditSession)
                .where(AuditSession.task_id.in_(task_ids), AuditSession.runtime_stack == "runtime")
                .order_by(AuditSession.created_at.desc())
            )
        ).scalars().all()
    )
    latest_by_task: dict[str, AuditSession] = {}
    for session in sessions:
        latest_by_task.setdefault(str(session.task_id), session)
    session_ids = [session.id for session in latest_by_task.values()]
    checkpoints = list(
        (
            await db.execute(
                select(AuditCheckpoint)
                .where(AuditCheckpoint.session_id.in_(session_ids))
                .order_by(AuditCheckpoint.created_at.desc())
            )
        ).scalars().all()
    ) if session_ids else []
    latest_checkpoint: dict[str, AuditCheckpoint] = {}
    for checkpoint in checkpoints:
        latest_checkpoint.setdefault(checkpoint.session_id, checkpoint)
    attempts = list(
        (
            await db.execute(
                select(AuditModelStreamAttempt)
                .where(AuditModelStreamAttempt.session_id.in_(session_ids))
                .order_by(AuditModelStreamAttempt.started_at.desc())
            )
        ).scalars().all()
    ) if session_ids else []
    latest_attempt: dict[str, AuditModelStreamAttempt] = {}
    for attempt in attempts:
        latest_attempt.setdefault(attempt.session_id, attempt)
    for project in projects:
        session = latest_by_task.get(str(project.agent_task_id))
        if session is None:
            continue
        checkpoint = latest_checkpoint.get(session.id)
        checkpoint_payload = dict(checkpoint.state_payload or {}) if checkpoint is not None else {}
        metadata = dict((session.runtime_state_json or {}).get("metadata") or {})
        resume_job = dict(metadata.get("resume_job") or {})
        project.resume_session_id = session.id
        project.resume_status = str(resume_job.get("status") or "") or None
        project.last_error_kind = str(resume_job.get("error_kind") or checkpoint_payload.get("error_kind") or "") or None
        project.resume_attempt = None
        project.resume_max_attempts = None
        attempt = latest_attempt.get(session.id)
        if resume_job.get("status") == "running" and attempt is not None:
            started_at = str(resume_job.get("started_at") or "").strip()
            belongs_to_resume = not started_at
            if started_at and attempt.started_at is not None:
                try:
                    belongs_to_resume = attempt.started_at >= datetime.fromisoformat(started_at)
                except (TypeError, ValueError):
                    belongs_to_resume = False
            if belongs_to_resume:
                project.resume_attempt = int(attempt.attempt_number)
                project.resume_max_attempts = 6
        project.can_resume = session.state == "failed" and bool(
            resume_job.get("can_resume")
            or checkpoint_payload.get("resumable")
            or checkpoint_payload.get("checkpoint_kind") == "resumable_failed"
        )
