from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models.agent_task import AgentFinding, AgentTask, AgentTaskStatus
from app.models.managed_vulnerability import ManagedVulnerability
from app.models.one_click_cve import OneClickCveBatch, OneClickCveBatchProject
from app.models.project import Project
from app.models.user import User
from app.services.one_click_cve import runner as one_click_runner
from app.services.one_click_cve.discovery import GitHubRepositoryCandidate
from app.services.one_click_cve.runner import _audit_candidate, run_one_click_cve_batch


MODEL_PREFLIGHT_STEP = "\u6b63\u5728\u6d4b\u8bd5\u6a21\u578b\u8fde\u901a\u6027"


@pytest.mark.asyncio
async def test_run_one_click_cve_batch_fails_in_background_when_llm_preflight_fails(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        batch = OneClickCveBatch(
            id="batch-1",
            user_id="user-1",
            requested_count=1,
            found_count=0,
            status="pending",
            current_step=MODEL_PREFLIGHT_STEP,
        )
        db.add_all([user, batch])
        await db.commit()

    async def fail_preflight(db, batch):
        raise RuntimeError("bad model")

    class FakeDiscoveryService:
        async def discover_candidates(self, *args, **kwargs):
            return []

    monkeypatch.setattr(one_click_runner, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(one_click_runner, "_preflight_one_click_cve_llm", fail_preflight, raising=False)
    monkeypatch.setattr(one_click_runner, "GitHubCveDiscoveryService", FakeDiscoveryService)

    await run_one_click_cve_batch("batch-1")

    async with session_factory() as db:
        batch = await db.get(OneClickCveBatch, "batch-1")

    await engine.dispose()

    assert batch.status == "failed"
    assert "bad model" in batch.error_message


@pytest.mark.asyncio
async def test_run_one_click_cve_batch_respects_cancel_after_llm_preflight(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        batch = OneClickCveBatch(
            id="batch-1",
            user_id="user-1",
            requested_count=1,
            found_count=0,
            status="pending",
            current_step=MODEL_PREFLIGHT_STEP,
        )
        db.add_all([user, batch])
        await db.commit()

    async def cancel_during_preflight(db, user_id):
        batch = await db.get(OneClickCveBatch, "batch-1")
        batch.status = "cancelled"
        batch.current_step = "用户已取消"
        await db.commit()

    class FailIfDiscoveryStarts:
        async def discover_candidates(self, *args, **kwargs):
            raise AssertionError("discovery must not start after batch cancellation")

    monkeypatch.setattr(one_click_runner, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(one_click_runner, "_preflight_one_click_cve_llm", cancel_during_preflight)
    monkeypatch.setattr(one_click_runner, "GitHubCveDiscoveryService", FailIfDiscoveryStarts)

    await run_one_click_cve_batch("batch-1")

    async with session_factory() as db:
        batch = await db.get(OneClickCveBatch, "batch-1")

    await engine.dispose()

    assert batch.status == "cancelled"


@pytest.mark.asyncio
async def test_audit_candidate_skips_repository_version_already_in_vulnerability_management():
    candidate = GitHubRepositoryCandidate(
        full_name="payloadcms/payload",
        repository_url="https://github.com/payloadcms/payload",
        description="Headless CMS",
        language="TypeScript",
        stars=42800,
        pushed_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 5, tzinfo=timezone.utc),
        default_branch="main",
        version_label="v3.85.0",
        version_source="latest_release",
        has_security_advisory=True,
        advisory_count=10,
        has_security_policy=True,
        score=1100,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        project = Project(
            id="project-1",
            name="payloadcms/payload",
            owner_id="user-1",
            source_type="repository",
            repository_url="https://github.com/payloadcms/payload",
        )
        task = AgentTask(
            id="task-1",
            project_id="project-1",
            created_by="user-1",
            name="Existing audit",
            version_label="v3.85.0",
            repository_url_snapshot="https://github.com/payloadcms/payload",
            status="completed",
        )
        finding = AgentFinding(
            id="finding-1",
            task_id="task-1",
            vulnerability_type="ssrf",
            severity="high",
            title="Existing finding",
        )
        managed = ManagedVulnerability(
            id="managed-1",
            project_id="project-1",
            task_id="task-1",
            finding_id="finding-1",
            project_name="payloadcms/payload",
            version_label="v3.85.0",
            repository_url_snapshot="https://github.com/payloadcms/payload",
            vulnerability_name="Existing finding",
            vulnerability_type="ssrf",
            severity="high",
        )
        batch = OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, found_count=0, status="running")
        db.add_all([user, project, task, finding, managed, batch])
        await db.commit()

        await _audit_candidate(db, batch, candidate)

        project_result = await db.execute(select(OneClickCveBatchProject))
        batch_project = project_result.scalar_one()
        task_count_result = await db.execute(select(func.count(AgentTask.id)))

    await engine.dispose()

    assert batch_project.status == "skipped"
    assert batch_project.agent_task_id is None
    assert batch_project.project_id is None
    assert batch_project.metadata_json["version_label"] == "v3.85.0"
    assert "已存在相同项目链接和版本" in batch_project.error_message
    assert task_count_result.scalar_one() == 1


@pytest.mark.asyncio
async def test_audit_candidate_records_failure_when_import_raises(monkeypatch):
    candidate = GitHubRepositoryCandidate(
        full_name="opensearch-project/OpenSearch",
        repository_url="https://github.com/opensearch-project/OpenSearch",
        description="Open source distributed search engine",
        language="Java",
        stars=13000,
        pushed_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        default_branch="main",
        version_label="3.6.0",
        version_source="latest_release",
        has_security_advisory=True,
        advisory_count=2,
        has_security_policy=True,
        score=1100,
    )

    async def fail_get_or_create_project(*args, **kwargs):
        raise RuntimeError("workspace import failed")

    monkeypatch.setattr(one_click_runner, "_get_or_create_project", fail_get_or_create_project)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        batch = OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=1, found_count=0, status="running")
        db.add_all([user, batch])
        await db.commit()

        await one_click_runner._audit_candidate(db, batch, candidate)

        project_result = await db.execute(select(OneClickCveBatchProject))
        batch_project = project_result.scalar_one()
        refreshed_batch = await db.get(OneClickCveBatch, "batch-1")

    await engine.dispose()

    assert batch_project.status == "failed"
    assert batch_project.error_message == "workspace import failed"
    assert refreshed_batch is not None
    assert "opensearch-project/OpenSearch" in (refreshed_batch.current_step or "")


@pytest.mark.asyncio
async def test_wait_for_task_completion_reads_fresh_task_status():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as setup_db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        project = Project(
            id="project-1",
            name="openclaw/openclaw",
            owner_id="user-1",
            source_type="repository",
            repository_url="https://github.com/openclaw/openclaw",
        )
        task = AgentTask(
            id="task-1",
            project_id="project-1",
            created_by="user-1",
            name="One-click CVE",
            version_label="v2026.6.1",
            repository_url_snapshot="https://github.com/openclaw/openclaw",
            status=AgentTaskStatus.PENDING,
        )
        setup_db.add_all([user, project, task])
        await setup_db.commit()

    async with session_factory() as db:
        cached_task = await db.get(AgentTask, "task-1")
        assert cached_task is not None
        assert cached_task.status == AgentTaskStatus.PENDING

        async def mark_task_failed():
            async with session_factory() as worker_db:
                worker_task = await worker_db.get(AgentTask, "task-1")
                worker_task.status = AgentTaskStatus.FAILED
                worker_task.error_message = "model service unavailable"
                await worker_db.commit()

        run_task = asyncio.create_task(mark_task_failed())

        with pytest.raises(RuntimeError, match="model service unavailable"):
            await one_click_runner._wait_for_task_completion(db, "task-1", run_task)

    await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_task_completion_cancels_agent_task_when_batch_is_cancelled(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as setup_db:
        user = User(
            id="user-1",
            email="owner@example.com",
            hashed_password="not-a-real-hash",
            full_name="Owner",
            is_active=True,
        )
        project = Project(
            id="project-1",
            name="gfx-rs/wgpu",
            owner_id="user-1",
            source_type="repository",
            repository_url="https://github.com/gfx-rs/wgpu",
        )
        batch = OneClickCveBatch(
            id="batch-1",
            user_id="user-1",
            requested_count=1,
            found_count=0,
            status="cancelled",
        )
        task = AgentTask(
            id="task-1",
            project_id="project-1",
            created_by="user-1",
            name="One-click CVE",
            version_label="v1.0.0",
            repository_url_snapshot="https://github.com/gfx-rs/wgpu",
            status=AgentTaskStatus.RUNNING,
        )
        setup_db.add_all([user, project, batch, task])
        await setup_db.commit()

    cancelled_tasks: list[str] = []

    def fake_request_agent_task_cancellation(task_id: str):
        cancelled_tasks.append(task_id)

    monkeypatch.setattr(one_click_runner, "request_agent_task_cancellation", fake_request_agent_task_cancellation)
    monkeypatch.setattr(one_click_runner, "POLL_INTERVAL_SECONDS", 0)

    async with session_factory() as db:
        with pytest.raises(one_click_runner.OneClickCveBatchCancelled):
            await one_click_runner._wait_for_task_completion(db, "task-1", batch_id="batch-1")

    async with session_factory() as db:
        task = await db.get(AgentTask, "task-1")

    await engine.dispose()

    assert cancelled_tasks == ["task-1"]
    assert task.status == AgentTaskStatus.CANCELLED
    assert "batch cancellation" in task.error_message
