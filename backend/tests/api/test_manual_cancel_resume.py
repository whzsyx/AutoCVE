from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.endpoints.agent_tasks import _mark_latest_runtime_session_manual_cancelled
from app.db.base import Base
from app.models.agent_task import AgentTask, AgentTaskStatus
from app.models.audit_session import AuditCheckpoint, AuditSession, AuditSessionTurn
from app.models.one_click_cve import OneClickCveBatch, OneClickCveBatchProject
from app.models.project import Project
from app.models.user import User


@pytest.mark.asyncio
async def test_manual_cancel_closes_runtime_and_one_click_project_at_resumable_boundary():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(id="user-1", email="owner@example.com", hashed_password="x", is_active=True)
        project = Project(
            id="project-1",
            name="demo/repo",
            owner_id="user-1",
            source_type="repository",
            repository_url="https://example.test/demo/repo",
        )
        task = AgentTask(
            id="task-1",
            project_id="project-1",
            created_by="user-1",
            name="Audit",
            version_label="v1",
            status=AgentTaskStatus.CANCELLED,
        )
        batch = OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, status="cancelled")
        batch_project = OneClickCveBatchProject(
            id="batch-project-1",
            batch_id="batch-1",
            project_id="project-1",
            agent_task_id="task-1",
            github_full_name="demo/repo",
            repository_url="https://example.test/demo/repo",
            status="auditing",
        )
        session = AuditSession(
            id="session-1",
            project_id="project-1",
            task_id="task-1",
            runtime_stack="runtime",
            state="running",
            runtime_state_json={},
        )
        turn = AuditSessionTurn(id="turn-1", session_id="session-1", sequence=1, status="open")
        db.add_all([user, project, task, batch, batch_project, session, turn])
        await db.commit()

        await _mark_latest_runtime_session_manual_cancelled(db, "task-1")
        await db.commit()

        await db.refresh(session)
        await db.refresh(batch_project)
        checkpoint = await db.scalar(
            select(AuditCheckpoint)
            .where(AuditCheckpoint.session_id == "session-1")
            .order_by(AuditCheckpoint.created_at.desc())
            .limit(1)
        )

    await engine.dispose()

    assert session.state == "failed"
    assert session.runtime_state_json["metadata"]["manual_cancel"]["can_resume"] is True
    assert batch_project.status == "cancelled"
    assert checkpoint is not None
    assert checkpoint.state_payload["checkpoint_kind"] == "manual_cancelled"
    assert checkpoint.state_payload["resumable"] is True
    assert checkpoint.state_payload["error_kind"] == "manual_cancelled"
