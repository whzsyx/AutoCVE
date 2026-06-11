from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.api.v1.endpoints import one_click_cve as one_click_cve_endpoint
from app.api.v1.endpoints.one_click_cve import router as one_click_cve_router
from app.db.base import Base
from app.models.one_click_cve import OneClickCveBatch
from app.models.user import User


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(one_click_cve_router, prefix="/api/v1/one-click-cve")
    return app


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_persists_current_user_batch(monkeypatch):
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

    monkeypatch.setattr(one_click_cve_endpoint, "run_one_click_cve_batch", fake_run_batch)

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
    assert batch.summary_json == {"prefer_security_advisory": True}
    assert payload["prefer_security_advisory"] is True
    assert scheduled_batches == [batch.id]


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

    monkeypatch.setattr(one_click_cve_endpoint, "run_one_click_cve_batch", fake_run_batch)

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
    assert batch.summary_json == {"prefer_security_advisory": False}
    assert payload["prefer_security_advisory"] is False


@pytest.mark.asyncio
async def test_create_one_click_cve_batch_rejects_count_above_twenty():
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
        response = await client.post("/api/v1/one-click-cve/batches", json={"target_count": 21})

    await engine.dispose()

    assert response.status_code == 422
