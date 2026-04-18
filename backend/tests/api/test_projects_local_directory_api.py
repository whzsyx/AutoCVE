from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.api.v1.endpoints.projects import router as projects_router
from app.db.base import Base
from app.models.user import User
import app.api.v1.endpoints.projects as projects_endpoint
from app.models.project import Project

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1/projects")
    return app


def make_managed_root() -> Path:
    managed_root = WORKSPACE_ROOT / ".pytest-managed-projects" / str(uuid.uuid4())
    managed_root.mkdir(parents=True, exist_ok=True)
    return managed_root


@pytest.mark.asyncio
async def test_create_local_directory_project_requires_local_path():
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

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await projects_endpoint.create_project(
                db=db,
                project_in=projects_endpoint.ProjectCreate(
                    name="Managed Demo",
                    source_type="local_directory",
                    programming_languages=["Python"],
                ),
                current_user=SimpleNamespace(id="user-1", is_active=True),
            )

    await engine.dispose()

    assert exc_info.value.status_code == 422
    assert "local_path" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_create_local_directory_project_rejects_duplicate_registration():
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

    managed_root_path = make_managed_root()
    project_path = managed_root_path / "managed-demo"
    project_path.mkdir()
    original_root = projects_endpoint.settings.MANAGED_PROJECTS_ROOT
    projects_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root_path)

    payload = {
        "name": "Managed Demo",
        "source_type": "local_directory",
        "local_path": str(project_path),
        "programming_languages": ["Python"],
    }

    try:
        async with session_factory() as db:
            first = await projects_endpoint.create_project(
                db=db,
                project_in=projects_endpoint.ProjectCreate(**payload),
                current_user=SimpleNamespace(id="user-1", is_active=True),
            )

            with pytest.raises(HTTPException) as exc_info:
                await projects_endpoint.create_project(
                    db=db,
                    project_in=projects_endpoint.ProjectCreate(**payload),
                    current_user=SimpleNamespace(id="user-1", is_active=True),
                )

            result = await db.execute(
                projects_endpoint.select(Project).where(Project.owner_id == "user-1")
            )
            projects = result.scalars().all()
    finally:
        projects_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root
        shutil.rmtree(managed_root_path, ignore_errors=True)

    await engine.dispose()

    assert first.name == "Managed Demo"
    assert len(projects) == 1
    assert exc_info.value.status_code == 400
    assert "already registered" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_list_managed_local_directories_returns_first_level_directories(monkeypatch):
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

    managed_root_path = make_managed_root()
    (managed_root_path / "alpha").mkdir()
    (managed_root_path / "beta").mkdir()
    (managed_root_path / "README.md").write_text("ignore me", encoding="utf-8")
    original_root = projects_endpoint.settings.MANAGED_PROJECTS_ROOT
    projects_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root_path)

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
            response = await client.get("/api/v1/projects/managed-local-directories")
    finally:
        projects_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root
        shutil.rmtree(managed_root_path, ignore_errors=True)

    await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == ["alpha", "beta"]
    assert payload[0]["path"] == str((managed_root_path / "alpha").resolve())


@pytest.mark.asyncio
async def test_get_local_directory_project_file_content_returns_text():
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

    managed_root_path = make_managed_root()
    project_path = managed_root_path / "preview-demo"
    src_dir = project_path / "src"
    src_dir.mkdir(parents=True)
    target_file = src_dir / "app.py"
    target_file.write_text("print('hello from direct audit')\n", encoding="utf-8")
    original_root = projects_endpoint.settings.MANAGED_PROJECTS_ROOT
    projects_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root_path)

    async with session_factory() as db:
        created = await projects_endpoint.create_project(
            db=db,
            project_in=projects_endpoint.ProjectCreate(
                name="Preview Demo",
                source_type="local_directory",
                local_path=str(project_path),
                programming_languages=["Python"],
            ),
            current_user=SimpleNamespace(id="user-1", is_active=True),
        )

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
            response = await client.get(
                f"/api/v1/projects/{created.id}/file-content",
                params={"path": "src/app.py"},
            )
    finally:
        projects_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root
        shutil.rmtree(managed_root_path, ignore_errors=True)

    await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == "src/app.py"
    assert "hello from direct audit" in payload["content"]
    assert payload["truncated"] is False


@pytest.mark.asyncio
async def test_get_local_directory_project_file_content_rejects_path_escape():
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

    managed_root_path = make_managed_root()
    project_path = managed_root_path / "escape-demo"
    project_path.mkdir(parents=True)
    (project_path / "safe.txt").write_text("safe", encoding="utf-8")
    original_root = projects_endpoint.settings.MANAGED_PROJECTS_ROOT
    projects_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root_path)

    async with session_factory() as db:
        created = await projects_endpoint.create_project(
            db=db,
            project_in=projects_endpoint.ProjectCreate(
                name="Escape Demo",
                source_type="local_directory",
                local_path=str(project_path),
                programming_languages=["Text"],
            ),
            current_user=SimpleNamespace(id="user-1", is_active=True),
        )

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
            response = await client.get(
                f"/api/v1/projects/{created.id}/file-content",
                params={"path": "../outside.txt"},
            )
    finally:
        projects_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root
        shutil.rmtree(managed_root_path, ignore_errors=True)

    await engine.dispose()

    assert response.status_code == 400
    assert "project root" in response.json()["detail"]
