import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints import audit_sessions as audit_sessions_endpoint
from app.db.base import Base
from app.models.agent_task import AgentFinding, AgentTask
from app.models.audit_session import AuditCheckpoint, AuditSession
from app.models.one_click_cve import OneClickCveBatch, OneClickCveBatchProject
from app.models.project import Project
from app.models.user import User
from app.services.finding_runtime.models import RuntimeCompletionMode
from app.services.finding_runtime import resume_job


@pytest.mark.asyncio
async def test_resume_job_persists_final_findings_and_refreshes_one_click_summary(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(User(id="user-1", email="owner@example.com", hashed_password="x", is_active=True))
        db.add(Project(id="project-1", name="demo", owner_id="user-1", source_type="repository"))
        db.add(
            AgentTask(
                id="task-1",
                project_id="project-1",
                version_label="main",
                created_by="user-1",
                status="running",
            )
        )
        db.add(OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, found_count=0, status="completed"))
        db.add(
            OneClickCveBatchProject(
                id="batch-project-1",
                batch_id="batch-1",
                project_id="project-1",
                agent_task_id="task-1",
                github_full_name="owner/demo",
                repository_url="https://github.com/owner/demo",
                status="auditing",
            )
        )
        db.add(
            AuditSession(
                id="session-1",
                project_id="project-1",
                task_id="task-1",
                runtime_stack="runtime",
                state="running",
                runtime_state_json={
                    "metadata": {
                        "resume_job": {"token": "token-1", "status": "queued"},
                    }
                },
            )
        )
        await db.commit()

    final_payload = {
        "findings": [
            {
                "vulnerability_type": "ssrf",
                "severity": "high",
                "title": "SSRF in URL fetcher",
                "description": "User-controlled URL reaches an outbound HTTP client.",
                "file_path": "src/fetch.py",
                "line_start": 10,
                "line_end": 12,
                "code_snippet": "client.get(url)",
                "source": "request url",
                "sink": "HTTP client",
                "suggestion": "Validate destinations.",
                "confidence": 0.9,
                "verdict": "candidate",
                "poc": {"description": "Request loopback URL", "steps": [{"step": 1, "action": "send request"}]},
                "impact": "Internal service access",
                "verification_notes": "Data flow verified",
            }
        ],
        "summary": "one finding",
    }

    async def fake_continue_runtime_session(*, session_id, content, db):
        del content
        session = await db.get(AuditSession, session_id)
        session.state = "completed"
        await db.commit()
        return {
            "runner_result": SimpleNamespace(
                final_payload=final_payload,
                completion_mode=RuntimeCompletionMode.FINALIZE_TOOL,
            )
        }

    monkeypatch.setattr(resume_job, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(audit_sessions_endpoint, "continue_runtime_session", fake_continue_runtime_session)
    report_calls: list[str] = []

    async def fake_auto_generate_reports(db, *, task, workflow_config, findings=None, event_emitter=None):
        del workflow_config, findings, event_emitter
        report_calls.append(task.id)
        session = await db.get(AuditSession, "session-1")
        session.state = "completed"
        await db.commit()
        return {"generated": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr(
        "app.api.v1.endpoints.agent_tasks._auto_generate_managed_vulnerability_reports",
        fake_auto_generate_reports,
    )

    await resume_job.run_audit_session_resume_job("session-1", "token-1")

    async with session_factory() as db:
        task = await db.get(AgentTask, "task-1")
        session = await db.get(AuditSession, "session-1")
        batch = await db.get(OneClickCveBatch, "batch-1")
        batch_project = await db.get(OneClickCveBatchProject, "batch-project-1")
        findings = list((await db.execute(select(AgentFinding).where(AgentFinding.task_id == "task-1"))).scalars().all())

    await engine.dispose()

    assert len(findings) == 1
    assert task.status == "completed"
    assert task.findings_count == 1
    assert batch_project.status == "completed"
    assert batch_project.findings_count == 1
    assert batch.found_count == 1
    assert session.runtime_state_json["metadata"]["resume_job"]["status"] == "completed"
    assert session.runtime_state_json["metadata"]["resume_job"]["report_generation"] == {
        "generated": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert report_calls == ["task-1"]


@pytest.mark.asyncio
async def test_resume_job_marks_natural_end_without_finalize_as_resumable_failure(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(User(id="user-1", email="owner@example.com", hashed_password="x", is_active=True))
        db.add(Project(id="project-1", name="demo", owner_id="user-1", source_type="repository"))
        db.add(AgentTask(id="task-1", project_id="project-1", version_label="main", created_by="user-1", status="running"))
        db.add(OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, found_count=0, status="cancelled"))
        db.add(
            OneClickCveBatchProject(
                id="batch-project-1",
                batch_id="batch-1",
                project_id="project-1",
                agent_task_id="task-1",
                github_full_name="owner/demo",
                repository_url="https://github.com/owner/demo",
                status="auditing",
            )
        )
        db.add(
            AuditSession(
                id="session-1",
                project_id="project-1",
                task_id="task-1",
                runtime_stack="runtime",
                state="running",
                runtime_state_json={"metadata": {"resume_job": {"token": "token-1", "status": "queued"}}},
            )
        )
        await db.commit()

    async def fake_continue_runtime_session(*, session_id, content, db):
        del content
        session = await db.get(AuditSession, session_id)
        session.state = "completed"
        await db.commit()
        return {"runner_result": SimpleNamespace(final_payload=None, completion_mode=RuntimeCompletionMode.INCOMPLETE)}

    monkeypatch.setattr(resume_job, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(audit_sessions_endpoint, "continue_runtime_session", fake_continue_runtime_session)

    await resume_job.run_audit_session_resume_job("session-1", "token-1")

    async with session_factory() as db:
        session = await db.get(AuditSession, "session-1")
        task = await db.get(AgentTask, "task-1")
        project = await db.get(OneClickCveBatchProject, "batch-project-1")

    await engine.dispose()

    assert session.state == "failed"
    assert task.status == "failed"
    assert project.status == "failed"
    assert session.runtime_state_json["metadata"]["resume_job"]["status"] == "resumable_failed"
    assert session.runtime_state_json["metadata"]["resume_job"]["error_kind"] == "incomplete_runtime"


@pytest.mark.asyncio
async def test_resume_job_can_be_cancelled_again_and_remains_resumable(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(User(id="user-1", email="owner@example.com", hashed_password="x", is_active=True))
        db.add(Project(id="project-1", name="demo", owner_id="user-1", source_type="repository"))
        db.add(AgentTask(id="task-1", project_id="project-1", version_label="main", created_by="user-1", status="running"))
        db.add(OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, found_count=0, status="cancelled"))
        db.add(
            OneClickCveBatchProject(
                id="batch-project-1",
                batch_id="batch-1",
                project_id="project-1",
                agent_task_id="task-1",
                github_full_name="owner/demo",
                repository_url="https://github.com/owner/demo",
                status="auditing",
            )
        )
        db.add(
            AuditSession(
                id="session-1",
                project_id="project-1",
                task_id="task-1",
                runtime_stack="runtime",
                state="running",
                runtime_state_json={"metadata": {"resume_job": {"token": "token-1", "status": "queued"}}},
            )
        )
        await db.commit()

    entered_continuation = asyncio.Event()

    async def fake_continue_runtime_session(*, session_id, content, db):
        del session_id, content, db
        entered_continuation.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(resume_job, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(resume_job, "RESUME_CANCEL_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(audit_sessions_endpoint, "continue_runtime_session", fake_continue_runtime_session)

    job = asyncio.create_task(resume_job.run_audit_session_resume_job("session-1", "token-1"))
    await asyncio.wait_for(entered_continuation.wait(), timeout=1)
    async with session_factory() as db:
        task = await db.get(AgentTask, "task-1")
        task.status = "cancelled"
        await db.commit()
    await asyncio.wait_for(job, timeout=2)

    async with session_factory() as db:
        session = await db.get(AuditSession, "session-1")
        task = await db.get(AgentTask, "task-1")
        project = await db.get(OneClickCveBatchProject, "batch-project-1")
        checkpoints = list(
            (await db.execute(select(AuditCheckpoint).where(AuditCheckpoint.session_id == "session-1"))).scalars().all()
        )

    await engine.dispose()

    assert session.state == "failed"
    assert task.status == "cancelled"
    assert project.status == "cancelled"
    assert session.runtime_state_json["metadata"]["resume_job"]["status"] == "manual_cancelled"
    assert session.runtime_state_json["metadata"]["resume_job"]["can_resume"] is True
    assert any(checkpoint.state_payload.get("checkpoint_kind") == "manual_cancelled" for checkpoint in checkpoints)
