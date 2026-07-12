from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.agent_task import AgentFinding, AgentTask, AgentTaskStatus, FindingStatus
from app.models.audit_session import AuditCheckpoint, AuditSession, AuditSessionTurn
from app.models.one_click_cve import (
    OneClickCveBatch,
    OneClickCveBatchProject,
    OneClickCveBatchStatus,
    OneClickCveProjectStatus,
)
from app.services.finding_runtime.session_store import AuditSessionPersistenceError


RESUME_CANCEL_POLL_INTERVAL_SECONDS = 0.5


def _is_finalized_finding_result(runner_result: Any, final_payload: dict[str, Any] | None) -> bool:
    completion_mode = getattr(runner_result, "completion_mode", None)
    if completion_mode is None and isinstance(runner_result, dict):
        completion_mode = runner_result.get("completion_mode") or runner_result.get("runtime_completion_mode")
    completion_value = getattr(completion_mode, "value", completion_mode)
    if str(completion_value or "").strip().lower() == "finalize_tool":
        return True
    return bool(
        isinstance(final_payload, dict)
        and final_payload.get("is_final") is True
        and not final_payload.get("requires_retry")
    )


def _resume_metadata(session: AuditSession) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    runtime_state = dict(session.runtime_state_json or {})
    metadata = dict(runtime_state.get("metadata") or {})
    resume_job = dict(metadata.get("resume_job") or {})
    return runtime_state, metadata, resume_job


def _set_resume_metadata(session: AuditSession, **updates: Any) -> None:
    runtime_state, metadata, resume_job = _resume_metadata(session)
    resume_job.update(updates)
    metadata["resume_job"] = resume_job
    runtime_state["metadata"] = metadata
    session.runtime_state_json = runtime_state


async def _refresh_task_and_batch(db, *, session: AuditSession, final_payload: dict[str, Any] | None) -> None:
    if not session.task_id:
        return
    task = await db.get(AgentTask, session.task_id)
    if task is None:
        return

    if session.state == "completed":
        from app.api.v1.endpoints.agent_tasks import _apply_task_finding_metrics, _load_task_findings, _save_findings

        if isinstance(final_payload, dict):
            await _save_findings(db, task.id, list(final_payload.get("findings") or []), project_root=None)
        findings = await _load_task_findings(db, task.id)
        _apply_task_finding_metrics(task, findings)
        task.status = AgentTaskStatus.COMPLETED
        task.current_step = "审计续跑已完成"
        task.error_message = None
        task.completed_at = datetime.now(timezone.utc)
    elif session.state != "completed":
        task.status = AgentTaskStatus.FAILED
        latest_checkpoint = await db.scalar(
            select(AuditCheckpoint)
            .where(AuditCheckpoint.session_id == session.id)
            .order_by(AuditCheckpoint.created_at.desc())
            .limit(1)
        )
        checkpoint_payload = dict(latest_checkpoint.state_payload or {}) if latest_checkpoint is not None else {}
        if not task.error_message:
            task.error_message = str(
                checkpoint_payload.get("error")
                or checkpoint_payload.get("message")
                or "审计续跑未完成，可稍后继续"
            )
        task.completed_at = datetime.now(timezone.utc)

    batch_project = await db.scalar(
        select(OneClickCveBatchProject)
        .where(OneClickCveBatchProject.agent_task_id == session.task_id)
        .order_by(OneClickCveBatchProject.created_at.desc())
        .limit(1)
    )
    if batch_project is None:
        return

    findings_count = await db.scalar(
        select(func.count(AgentFinding.id)).where(
            AgentFinding.task_id == task.id,
            AgentFinding.status != FindingStatus.FALSE_POSITIVE,
        )
    )
    batch_project.findings_count = int(findings_count or 0)
    batch_project.status = (
        OneClickCveProjectStatus.COMPLETED if session.state == "completed" else OneClickCveProjectStatus.FAILED
    )
    batch_project.error_message = None if session.state == "completed" else task.error_message
    batch_project.updated_at_local = datetime.now(timezone.utc)
    batch = await db.get(OneClickCveBatch, batch_project.batch_id)
    if batch is not None:
        from app.services.one_click_cve.runner import _refresh_batch_summary, is_fatal_one_click_cve_error

        await _refresh_batch_summary(db, batch)
        if session.state != "completed":
            latest_checkpoint = await db.scalar(
                select(AuditCheckpoint)
                .where(AuditCheckpoint.session_id == session.id)
                .order_by(AuditCheckpoint.created_at.desc())
                .limit(1)
            )
            checkpoint_payload = dict(latest_checkpoint.state_payload or {}) if latest_checkpoint is not None else {}
            error_kind = str(checkpoint_payload.get("error_kind") or "").strip() or None
            error_message = str(checkpoint_payload.get("error") or task.error_message or "").strip()
            if is_fatal_one_click_cve_error(error_kind, error_message):
                batch.status = OneClickCveBatchStatus.FAILED
                batch.error_message = error_message or "共享模型或基础设施故障"
                batch.current_step = "检测到共享模型或基础设施故障，已停止整个一键 CVE"
                batch.completed_at = datetime.now(timezone.utc)


async def _generate_reports_for_resumed_findings(
    db,
    *,
    session: AuditSession,
    resume_token: str,
) -> dict[str, int] | None:
    """Run the same report-generation ReAct phase as a normal completed audit."""
    if not session.task_id:
        return {"generated": 0, "failed": 0, "skipped": 0}
    task = await db.get(AgentTask, session.task_id)
    if task is None:
        return {"generated": 0, "failed": 0, "skipped": 0}

    findings_count = await db.scalar(
        select(func.count(AgentFinding.id)).where(
            AgentFinding.task_id == task.id,
            AgentFinding.status != FindingStatus.FALSE_POSITIVE,
        )
    )
    if not findings_count:
        return {"generated": 0, "failed": 0, "skipped": 0}

    from app.api.v1.endpoints.agent_tasks import _auto_generate_managed_vulnerability_reports

    await db.flush()
    session.state = "running"
    task.status = AgentTaskStatus.RUNNING
    task.current_step = "Generating English, Chinese, and CVE reports"
    task.completed_at = None
    batch_project = await db.scalar(
        select(OneClickCveBatchProject)
        .where(OneClickCveBatchProject.agent_task_id == task.id)
        .order_by(OneClickCveBatchProject.created_at.desc())
        .limit(1)
    )
    if batch_project is not None:
        batch_project.status = OneClickCveProjectStatus.AUDITING
        batch_project.error_message = None
        batch_project.updated_at_local = datetime.now(timezone.utc)
    _set_resume_metadata(session, status="generating_reports", can_resume=False)
    await db.commit()

    report_task = asyncio.create_task(
        _auto_generate_managed_vulnerability_reports(
            db,
            task=task,
            workflow_config=None,
            findings=None,
            event_emitter=None,
        )
    )
    cancelled_by_user = asyncio.Event()
    cancel_watch_task = asyncio.create_task(
        _watch_resume_cancellation(
            session_id=session.id,
            task_id=task.id,
            resume_token=resume_token,
            run_task=report_task,
            cancelled_by_user=cancelled_by_user,
        )
    )
    try:
        return await report_task
    except asyncio.CancelledError:
        if cancelled_by_user.is_set():
            await _mark_resume_manual_cancelled(session.id, resume_token)
            return None
        raise
    finally:
        cancel_watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_watch_task


async def _mark_resume_failed(session_id: str, resume_token: str, *, error_kind: str, message: str) -> None:
    async with AsyncSessionLocal() as db:
        session = await db.get(AuditSession, session_id)
        if session is None:
            return
        _, _, resume_job = _resume_metadata(session)
        if str(resume_job.get("token") or "") != resume_token:
            return
        session.state = "failed"
        _set_resume_metadata(
            session,
            status="resumable_failed",
            error_kind=error_kind,
            error=message,
            completed_at=datetime.now(timezone.utc).isoformat(),
            can_resume=True,
        )
        turn_id = await db.scalar(
            select(AuditSessionTurn.id)
            .where(AuditSessionTurn.session_id == session_id)
            .order_by(AuditSessionTurn.sequence.desc())
            .limit(1)
        )
        db.add(
            AuditCheckpoint(
                session_id=session_id,
                turn_id=turn_id,
                checkpoint_type="auto",
                state_payload={
                    "checkpoint_kind": "resumable_failed",
                    "resumable": True,
                    "error_kind": error_kind,
                    "error": message,
                    "phase": "resume_job",
                },
            )
        )
        task = await db.get(AgentTask, session.task_id) if session.task_id else None
        if task is not None:
            task.status = AgentTaskStatus.FAILED
            task.error_message = message
            task.completed_at = datetime.now(timezone.utc)
        await _refresh_task_and_batch(db, session=session, final_payload=None)
        await db.commit()


async def _mark_resume_manual_cancelled(session_id: str, resume_token: str) -> None:
    """Persist a cancellable boundary for a resume job stopped by the user."""
    async with AsyncSessionLocal() as db:
        session = await db.get(AuditSession, session_id)
        if session is None:
            return
        _, _, resume_job = _resume_metadata(session)
        if str(resume_job.get("token") or "") != resume_token:
            return

        session.state = "failed"
        runtime_state, metadata, _ = _resume_metadata(session)
        metadata["manual_cancel"] = {
            "status": "stopped",
            "can_resume": True,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
        }
        runtime_state["metadata"] = metadata
        session.runtime_state_json = runtime_state
        _set_resume_metadata(
            session,
            status="manual_cancelled",
            error_kind="manual_cancelled",
            error="Audit manually cancelled by user",
            completed_at=datetime.now(timezone.utc).isoformat(),
            can_resume=True,
        )

        latest_checkpoint = await db.scalar(
            select(AuditCheckpoint)
            .where(AuditCheckpoint.session_id == session_id)
            .order_by(AuditCheckpoint.created_at.desc())
            .limit(1)
        )
        latest_payload = dict(latest_checkpoint.state_payload or {}) if latest_checkpoint is not None else {}
        if latest_payload.get("checkpoint_kind") != "manual_cancelled":
            turn_id = await db.scalar(
                select(AuditSessionTurn.id)
                .where(AuditSessionTurn.session_id == session_id)
                .order_by(AuditSessionTurn.sequence.desc())
                .limit(1)
            )
            db.add(
                AuditCheckpoint(
                    session_id=session_id,
                    turn_id=turn_id,
                    checkpoint_type="auto",
                    state_payload={
                        "checkpoint_kind": "manual_cancelled",
                        "resumable": True,
                        "error_kind": "manual_cancelled",
                        "error": "Audit manually cancelled by user",
                        "phase": "resume_job",
                    },
                )
            )

        task = await db.get(AgentTask, session.task_id) if session.task_id else None
        if task is not None:
            task.status = AgentTaskStatus.CANCELLED
            task.error_message = "Audit manually cancelled by user"
            task.completed_at = datetime.now(timezone.utc)
        batch_project = await db.scalar(
            select(OneClickCveBatchProject)
            .where(OneClickCveBatchProject.agent_task_id == session.task_id)
            .order_by(OneClickCveBatchProject.created_at.desc())
            .limit(1)
        ) if session.task_id else None
        if batch_project is not None:
            batch_project.status = OneClickCveProjectStatus.CANCELLED
            batch_project.error_message = "Audit manually cancelled by user"
            batch_project.updated_at_local = datetime.now(timezone.utc)
        await db.commit()


async def _watch_resume_cancellation(
    *,
    session_id: str,
    task_id: str | None,
    resume_token: str,
    run_task: asyncio.Task,
    cancelled_by_user: asyncio.Event,
) -> None:
    """Watch durable task state because the resume job runs outside the agent worker."""
    if not task_id:
        return
    while not run_task.done():
        await asyncio.sleep(RESUME_CANCEL_POLL_INTERVAL_SECONDS)
        async with AsyncSessionLocal() as db:
            session = await db.get(AuditSession, session_id)
            task = await db.get(AgentTask, task_id)
        if session is None or task is None:
            return
        _, _, resume_job = _resume_metadata(session)
        if str(resume_job.get("token") or "") != resume_token:
            return
        if task.status == AgentTaskStatus.CANCELLED:
            cancelled_by_user.set()
            if not run_task.done():
                run_task.cancel()
            return


async def run_audit_session_resume_job(session_id: str, resume_token: str) -> None:
    async with AsyncSessionLocal() as db:
        session = await db.get(AuditSession, session_id)
        if session is None:
            return
        _, _, resume_job = _resume_metadata(session)
        if str(resume_job.get("token") or "") != resume_token or resume_job.get("status") not in {"queued", "running"}:
            return
        _set_resume_metadata(
            session,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            can_resume=False,
        )
        await db.commit()

        continuation_task: asyncio.Task | None = None
        cancel_watch_task: asyncio.Task | None = None
        cancelled_by_user = asyncio.Event()
        try:
            from app.api.v1.endpoints.audit_sessions import continue_runtime_session

            continuation_task = asyncio.create_task(
                continue_runtime_session(session_id=session_id, content="", db=db)
            )
            cancel_watch_task = asyncio.create_task(
                _watch_resume_cancellation(
                    session_id=session_id,
                    task_id=session.task_id,
                    resume_token=resume_token,
                    run_task=continuation_task,
                    cancelled_by_user=cancelled_by_user,
                )
            )
            async with asyncio.timeout(settings.AUDIT_SESSION_RESUME_TIMEOUT_SECONDS):
                continuation = await continuation_task
        except asyncio.CancelledError:
            if cancelled_by_user.is_set():
                await _mark_resume_manual_cancelled(session_id, resume_token)
                return
            raise
        except TimeoutError:
            await _mark_resume_failed(
                session_id,
                resume_token,
                error_kind="agent_timeout",
                message=f"Agent 总时间超时：本次继续审计超过 {settings.AUDIT_SESSION_RESUME_TIMEOUT_SECONDS // 60} 分钟。",
            )
            return
        except Exception as exc:
            error_kind = "persistence_error" if isinstance(exc, AuditSessionPersistenceError) else "resume_job_error"
            await _mark_resume_failed(
                session_id,
                resume_token,
                error_kind=error_kind,
                message=f"继续审计后台任务失败：{exc}",
            )
            return

        finally:
            if cancel_watch_task is not None:
                cancel_watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancel_watch_task
            if continuation_task is not None and not continuation_task.done():
                continuation_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await continuation_task

        await db.refresh(session)
        _, _, current_job = _resume_metadata(session)
        if str(current_job.get("token") or "") != resume_token:
            return
        runner_result = (continuation or {}).get("runner_result") if isinstance(continuation, dict) else None
        final_payload = getattr(runner_result, "final_payload", None)
        if final_payload is None and isinstance(runner_result, dict):
            final_payload = runner_result.get("final_payload")
        if final_payload is None and isinstance(continuation, dict):
            final_payload = continuation.get("final_payload")
        if not _is_finalized_finding_result(runner_result, final_payload):
            await _mark_resume_failed(
                session_id,
                resume_token,
                error_kind="incomplete_runtime",
                message="Finding 未完成：恢复审计没有调用 FinalizeFinding 提交结构化结果。可继续同一审计会话。",
            )
            return
        await _refresh_task_and_batch(db, session=session, final_payload=final_payload)
        report_stats = await _generate_reports_for_resumed_findings(
            db,
            session=session,
            resume_token=resume_token,
        )
        if report_stats is None:
            return
        await db.refresh(session)
        await _refresh_task_and_batch(db, session=session, final_payload=None)
        _set_resume_metadata(
            session,
            status="completed" if session.state == "completed" else "resumable_failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            can_resume=session.state != "completed",
            report_generation=report_stats,
        )
        await db.commit()
