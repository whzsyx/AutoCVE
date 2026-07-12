from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.api.v1.endpoints import agent_tasks as agent_tasks_endpoint
from app.api.v1.endpoints import one_click_cve as one_click_cve_endpoint
from app.api.v1.endpoints.one_click_cve import router as one_click_cve_router
from app.db.base import Base
from app.models.agent_task import AgentTask, AgentTaskStatus
from app.models.one_click_cve import OneClickCveBatch, OneClickCveBatchProject, OneClickCveProjectStatus
from app.models.project import Project
from app.models.user import User


MODEL_PREFLIGHT_STEP = "\u6b63\u5728\u6d4b\u8bd5\u6a21\u578b\u8fde\u901a\u6027"
QUEUED_STEP = "\u7b49\u5f85\u4e00\u952e CVE worker \u6267\u884c"


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(one_click_cve_router, prefix="/api/v1/one-click-cve")
    return app


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_persists_current_user_batch_and_enqueues_worker(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(
            User(
                id="user-1",
                email="owner@example.com",
                hashed_password="not-a-real-hash",
                full_name="Owner",
                is_active=True,
            )
        )
        await db.commit()

    enqueued_batches: list[str] = []

    async def fake_enqueue_batch(batch_id: str):
        enqueued_batches.append(batch_id)

    async def fail_run_batch(batch_id: str):
        raise AssertionError("one-click CVE batch should be queued instead of running in the API process")

    async def fail_preflight(db, current_user):
        raise AssertionError("model preflight must run in the background batch runner")

    monkeypatch.setattr(one_click_cve_endpoint, "should_use_one_click_cve_worker_queue", lambda: True)
    monkeypatch.setattr(one_click_cve_endpoint, "enqueue_one_click_cve_batch", fake_enqueue_batch)
    monkeypatch.setattr(one_click_cve_endpoint, "run_one_click_cve_batch", fail_run_batch)
    monkeypatch.setattr(one_click_cve_endpoint, "_preflight_one_click_cve_llm", fail_preflight, raising=False)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/one-click-cve/batches", json={"target_count": 5})

    payload = response.json()
    async with session_factory() as db:
        batch = await db.get(OneClickCveBatch, payload["id"])

    await engine.dispose()

    assert response.status_code == 200
    assert batch is not None
    assert batch.user_id == "user-1"
    assert batch.requested_count == 5
    assert batch.status == "pending"
    assert batch.current_step == QUEUED_STEP
    assert batch.summary_json == {"prefer_security_advisory": True}
    assert payload["prefer_security_advisory"] is True
    assert payload["current_step"] == QUEUED_STEP
    assert enqueued_batches == [batch.id]


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_can_disable_security_advisory_preference(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(
            User(
                id="user-1",
                email="owner@example.com",
                hashed_password="not-a-real-hash",
                full_name="Owner",
                is_active=True,
            )
        )
        await db.commit()

    async def fake_run_batch(batch_id: str):
        return None

    async def fail_preflight(db, current_user):
        raise AssertionError("model preflight must run in the background batch runner")

    monkeypatch.setattr(one_click_cve_endpoint, "run_one_click_cve_batch", fake_run_batch)
    monkeypatch.setattr(one_click_cve_endpoint, "_preflight_one_click_cve_llm", fail_preflight, raising=False)
    monkeypatch.setattr(one_click_cve_endpoint, "should_use_one_click_cve_worker_queue", lambda: False)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/one-click-cve/batches",
            json={"target_count": 5, "prefer_security_advisory": False},
        )

    payload = response.json()
    async with session_factory() as db:
        batch = await db.get(OneClickCveBatch, payload["id"])

    await engine.dispose()

    assert response.status_code == 200
    assert batch is not None
    assert batch.current_step == MODEL_PREFLIGHT_STEP
    assert batch.summary_json == {"prefer_security_advisory": False}
    assert payload["prefer_security_advisory"] is False


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_returns_immediately_when_llm_preflight_would_fail(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(
            User(
                id="user-1",
                email="owner@example.com",
                hashed_password="not-a-real-hash",
                full_name="Owner",
                is_active=True,
            )
        )
        await db.commit()

    scheduled_batches: list[str] = []

    async def fake_run_batch(batch_id: str):
        scheduled_batches.append(batch_id)

    async def fail_preflight(db, current_user):
        raise AssertionError("model preflight must not block batch creation")

    monkeypatch.setattr(one_click_cve_endpoint, "run_one_click_cve_batch", fake_run_batch)
    monkeypatch.setattr(one_click_cve_endpoint, "_preflight_one_click_cve_llm", fail_preflight, raising=False)
    monkeypatch.setattr(one_click_cve_endpoint, "should_use_one_click_cve_worker_queue", lambda: False)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/one-click-cve/batches", json={"target_count": 5})

    payload = response.json()
    async with session_factory() as db:
        batch = await db.get(OneClickCveBatch, payload["id"])

    await engine.dispose()

    assert response.status_code == 200
    assert batch is not None
    assert batch.status == "pending"
    assert batch.current_step == MODEL_PREFLIGHT_STEP
    assert scheduled_batches == [batch.id]


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_rejects_count_above_ten():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/one-click-cve/batches", json={"target_count": 11})

    await engine.dispose()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cancel_one_click_cve_batch_cancels_active_agent_tasks(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class FakeRunner:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class FakeAsyncioTask:
        def __init__(self):
            self.cancelled = False

        def done(self):
            return False

        def cancel(self):
            self.cancelled = True

    fake_runner = FakeRunner()
    fake_asyncio_task = FakeAsyncioTask()
    monkeypatch.setitem(agent_tasks_endpoint._running_tasks, "task-running", fake_runner)
    monkeypatch.setitem(agent_tasks_endpoint._running_asyncio_tasks, "task-running", fake_asyncio_task)

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
            name="owner/repo",
            owner_id="user-1",
            source_type="repository",
            repository_url="https://github.com/owner/repo",
        )
        batch = OneClickCveBatch(id="batch-1", user_id="user-1", requested_count=2, found_count=0, status="running")
        running_task = AgentTask(
            id="task-running",
            project_id="project-1",
            created_by="user-1",
            name="Running one-click CVE task",
            version_label="v1.0.0",
            status=AgentTaskStatus.RUNNING,
        )
        pending_task = AgentTask(
            id="task-pending",
            project_id="project-1",
            created_by="user-1",
            name="Pending one-click CVE task",
            version_label="v1.0.1",
            status=AgentTaskStatus.PENDING,
        )
        completed_task = AgentTask(
            id="task-completed",
            project_id="project-1",
            created_by="user-1",
            name="Completed one-click CVE task",
            version_label="v1.0.2",
            status=AgentTaskStatus.COMPLETED,
        )
        db.add_all(
            [
                user,
                project,
                batch,
                running_task,
                pending_task,
                completed_task,
                OneClickCveBatchProject(
                    id="batch-project-running",
                    batch_id="batch-1",
                    project_id="project-1",
                    agent_task_id="task-running",
                    github_full_name="owner/repo",
                    repository_url="https://github.com/owner/repo",
                    status=OneClickCveProjectStatus.AUDITING,
                ),
                OneClickCveBatchProject(
                    id="batch-project-pending",
                    batch_id="batch-1",
                    project_id="project-1",
                    agent_task_id="task-pending",
                    github_full_name="owner/repo-pending",
                    repository_url="https://github.com/owner/repo-pending",
                    status=OneClickCveProjectStatus.AUDITING,
                ),
                OneClickCveBatchProject(
                    id="batch-project-completed",
                    batch_id="batch-1",
                    project_id="project-1",
                    agent_task_id="task-completed",
                    github_full_name="owner/repo-completed",
                    repository_url="https://github.com/owner/repo-completed",
                    status=OneClickCveProjectStatus.COMPLETED,
                ),
            ]
        )
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/v1/one-click-cve/batches/batch-1/cancel")

        async with session_factory() as db:
            running = await db.get(AgentTask, "task-running")
            pending = await db.get(AgentTask, "task-pending")
            completed = await db.get(AgentTask, "task-completed")
            running_project = await db.get(OneClickCveBatchProject, "batch-project-running")
            pending_project = await db.get(OneClickCveBatchProject, "batch-project-pending")
            completed_project = await db.get(OneClickCveBatchProject, "batch-project-completed")

        assert response.status_code == 200
        assert running.status == AgentTaskStatus.CANCELLED
        assert pending.status == AgentTaskStatus.CANCELLED
        assert completed.status == AgentTaskStatus.COMPLETED
        assert running_project.status == OneClickCveProjectStatus.CANCELLED
        assert pending_project.status == OneClickCveProjectStatus.CANCELLED
        assert completed_project.status == OneClickCveProjectStatus.COMPLETED
        assert fake_runner.cancelled is True
        assert fake_asyncio_task.cancelled is True
        assert "task-running" in agent_tasks_endpoint._cancelled_tasks
        assert "task-pending" in agent_tasks_endpoint._cancelled_tasks
    finally:
        agent_tasks_endpoint._cancelled_tasks.discard("task-running")
        agent_tasks_endpoint._cancelled_tasks.discard("task-pending")
        await engine.dispose()
