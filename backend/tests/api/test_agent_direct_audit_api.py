from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
import app.api.v1.endpoints.agent_direct_audit as agent_direct_audit_endpoint
from app.api.v1.endpoints.agent_direct_audit import router as agent_direct_audit_router
from app.db.base import Base
from app.models.audit_session import AuditSession, AuditSessionMessage, AuditToolCall
from app.models.project import Project
from app.models.user import User


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_direct_audit_router, prefix="/api/v1/agent-direct-audit")
    return app


async def seed_direct_audit_fixture(session_factory) -> None:
    async with session_factory() as db:
        db.add_all(
            [
                User(
                    id="user-1",
                    email="owner@example.com",
                    hashed_password="not-a-real-hash",
                    full_name="Owner",
                    is_active=True,
                ),
                Project(
                    id="project-1",
                    name="Managed Demo",
                    owner_id="user-1",
                    source_type="local_directory",
                    local_path="D:/Projects/AuditAI/projects/managed-demo",
                    workspace_mode="in_place",
                ),
            ]
        )
        await db.commit()


def build_dependency_overrides(app: FastAPI, session_factory) -> None:
    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id="user-1", is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user


def parse_sse_events(raw_text: str) -> list[dict]:
    events: list[dict] = []
    for chunk in raw_text.split("\n\n"):
        if not chunk.strip():
            continue
        data_lines = [line[5:].strip() for line in chunk.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        events.append(json.loads("\n".join(data_lines)))
    return events


@pytest.mark.asyncio
async def test_create_direct_audit_session_and_list_project_sessions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async def fake_start_direct_audit_session(*, project, content, db, current_user):
        del current_user
        session = AuditSession(
            project_id=project.id,
            task_id=None,
            runtime_stack="runtime",
            state="running",
            system_prompt="You are the direct audit finding agent.",
            recon_payload={"project_name": project.name},
        )
        db.add(session)
        await db.flush()
        db.add(
            AuditSessionMessage(
                session_id=session.id,
                sequence=1,
                role="user",
                content=content,
                message_metadata={"kind": "direct_audit_user_message"},
                payload={"project_id": project.id},
            )
        )
        db.add(
            AuditSessionMessage(
                session_id=session.id,
                sequence=2,
                role="assistant",
                content="Initial direct audit reply.",
                message_metadata={"kind": "direct_audit_assistant_message"},
                payload={"continued": False},
            )
        )
        await db.commit()
        await db.refresh(session)
        return session

    monkeypatch.setattr(agent_direct_audit_endpoint, "start_direct_audit_session", fake_start_direct_audit_session)

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        create_response = await client.post(
            "/api/v1/agent-direct-audit/sessions",
            json={"project_id": "project-1", "content": "帮我看看有没有安全漏洞"},
        )
        list_response = await client.get("/api/v1/agent-direct-audit/sessions", params={"project_id": "project-1"})

    await engine.dispose()

    assert create_response.status_code == 200
    assert create_response.json()["project_id"] == "project-1"
    assert create_response.json()["task_id"] is None
    assert create_response.json()["runtime_stack"] == "runtime"
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_post_direct_audit_message_appends_user_message_and_continues(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="completed",
                system_prompt="direct audit",
                recon_payload={"project_name": "Managed Demo"},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    async def fake_continue_direct_audit_session(*, session, content, db, current_user):
        del current_user, content
        db.add(
            AuditSessionMessage(
                session_id=session.id,
                sequence=2,
                role="assistant",
                content="继续审计后的回复",
                message_metadata={"kind": "direct_audit_assistant_message"},
                payload={"continued": True},
            )
        )
        await db.commit()

    monkeypatch.setattr(agent_direct_audit_endpoint, "continue_direct_audit_session", fake_continue_direct_audit_session)

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-1/messages",
            json={"content": "再帮我看看还有没有遗漏的"},
        )
        messages = await client.get("/api/v1/agent-direct-audit/sessions/session-1/messages")

    await engine.dispose()

    assert response.status_code == 200
    assert response.json()["content"] == "再帮我看看还有没有遗漏的"
    assert response.json()["role"] == "user"
    assert len(messages.json()) == 2


@pytest.mark.asyncio
async def test_stream_approve_direct_audit_tool_call_grants_write_and_continues(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-approve-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="running",
                system_prompt="direct audit",
                recon_payload={
                    "project_info": {
                        "project_id": "project-1",
                        "name": "Managed Demo",
                        "workspace_root": "D:/Projects/AuditAI/projects/managed-demo",
                    }
                },
                runtime_state_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AuditToolCall(
                id="tool-call-1",
                session_id="session-approve-1",
                turn_id="turn-1",
                sequence=1,
                tool_use_id="tool-use-1",
                tool_name="Write",
                status="denied",
                is_concurrency_safe=False,
                input_payload={"path": "src/app.py", "content": "print('approved')", "overwrite": True},
                output_payload={
                    "permission_mode": "ask",
                    "permission_reason": "Writing source files requires explicit approval.",
                    "guardrail_code": "source_write_requires_approval",
                },
                error_message="Writing source files requires explicit approval.",
            )
        )
        await db.commit()

    async def fake_continue_direct_audit_session_stream(*, session, content, db, current_user):
        del current_user
        assert "src/app.py" in content
        approvals = ((session.runtime_state_json or {}).get("metadata") or {}).get("write_approvals") or []
        assert approvals[0]["path"] == "src/app.py"
        assert approvals[0]["guardrail_code"] == "source_write_requires_approval"

        assistant = AuditSessionMessage(
            session_id=session.id,
            sequence=2,
            role="assistant",
            content="宸叉敹鍒板啓鍏ユ壒鍑嗭紝姝ｅ湪缁х画瀹¤銆?",
            message_metadata={"kind": "direct_audit_assistant_message", "streaming": True},
            payload={"continued": True, "approval": True},
        )
        db.add(assistant)
        await db.commit()
        await db.refresh(assistant)
        yield {
            "type": "assistant_start",
            "message": {
                "id": "streaming-session-approve-1-2",
                "session_id": session.id,
                "sequence": 2,
                "role": "assistant",
                "content": "",
                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                "payload": {"continued": True, "approval": True},
                "created_at": assistant.created_at.isoformat(),
            },
        }
        yield {"type": "token", "content": "宸叉敹鍒板啓鍏ユ壒鍑?", "accumulated": "宸叉敹鍒板啓鍏ユ壒鍑?"}
        yield {
            "type": "done",
            "message": {
                "id": assistant.id,
                "session_id": assistant.session_id,
                "sequence": assistant.sequence,
                "role": assistant.role,
                "content": assistant.content,
                "metadata": dict(assistant.message_metadata or {}),
                "payload": dict(assistant.payload or {}),
                "created_at": assistant.created_at.isoformat(),
            },
            "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
        }

    monkeypatch.setattr(
        agent_direct_audit_endpoint,
        "continue_direct_audit_session_stream",
        fake_continue_direct_audit_session_stream,
    )

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-approve-1/tool-calls/tool-call-1/approve/stream",
        )
        messages = await client.get("/api/v1/agent-direct-audit/sessions/session-approve-1/messages")

    async with session_factory() as db:
        session = await db.get(AuditSession, "session-approve-1")

    await engine.dispose()

    assert response.status_code == 200
    events = parse_sse_events(response.text)
    assert [event["type"] for event in events] == ["user_message", "assistant_start", "token", "done"]
    assert "src/app.py" in events[0]["message"]["content"]
    assert len(messages.json()) == 2
    assert messages.json()[0]["role"] == "user"
    assert messages.json()[0]["metadata"]["kind"] == "direct_audit_approval"
    approvals = ((session.runtime_state_json or {}).get("metadata") or {}).get("write_approvals") or []
    assert approvals[0]["path"] == "src/app.py"
    assert messages.json()[0]["role"] == "user"
    assert messages.json()[0]["payload"]["continued"] is True
    assert messages.json()[1]["role"] == "assistant"
    assert messages.json()[1]["payload"]["continued"] is True


@pytest.mark.asyncio
async def test_stream_direct_audit_message_emits_sse_events(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="running",
                system_prompt="direct audit",
                recon_payload={"project_name": "Managed Demo"},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    async def fake_continue_direct_audit_session_stream(*, session, content, db, current_user):
        del current_user, content
        assistant = AuditSessionMessage(
            session_id=session.id,
            sequence=2,
            role="assistant",
            content="流式审计回复",
            message_metadata={"kind": "direct_audit_assistant_message", "streaming": True},
            payload={"continued": True},
        )
        db.add(assistant)
        await db.commit()
        await db.refresh(assistant)
        yield {
            "type": "assistant_start",
            "message": {
                "id": "streaming-session-1-2",
                "session_id": session.id,
                "sequence": 2,
                "role": "assistant",
                "content": "",
                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                "payload": {"continued": True},
                "created_at": assistant.created_at.isoformat(),
            },
        }
        yield {"type": "token", "content": "流式", "accumulated": "流式"}
        yield {"type": "token", "content": "审计回复", "accumulated": "流式审计回复"}
        yield {
            "type": "done",
            "message": {
                "id": assistant.id,
                "session_id": assistant.session_id,
                "sequence": assistant.sequence,
                "role": assistant.role,
                "content": assistant.content,
                "metadata": dict(assistant.message_metadata or {}),
                "payload": dict(assistant.payload or {}),
                "created_at": assistant.created_at.isoformat(),
            },
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        }

    monkeypatch.setattr(
        agent_direct_audit_endpoint,
        "continue_direct_audit_session_stream",
        fake_continue_direct_audit_session_stream,
    )

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-1/messages/stream",
            json={"content": "继续审计"},
        )
        messages = await client.get("/api/v1/agent-direct-audit/sessions/session-1/messages")

    await engine.dispose()

    assert response.status_code == 200
    events = parse_sse_events(response.text)
    assert [event["type"] for event in events] == ["user_message", "assistant_start", "token", "token", "done"]
    assert events[0]["message"]["content"] == "继续审计"
    assert events[-1]["message"]["content"] == "流式审计回复"
    assert len(messages.json()) == 2
    assert messages.json()[0]["content"] == "继续审计"
    assert messages.json()[1]["content"] == "流式审计回复"


@pytest.mark.asyncio
async def test_stream_direct_audit_session_creation_emits_session_created_and_messages(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async def fake_start_direct_audit_session_stream(*, project, content, db, current_user):
        del current_user
        session = AuditSession(
            id="session-stream-1",
            project_id=project.id,
            task_id=None,
            runtime_stack="runtime",
            state="running",
            system_prompt="direct audit",
            recon_payload={"project_name": project.name},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.add(
            AuditSessionMessage(
                session_id=session.id,
                sequence=1,
                role="user",
                content=content,
                message_metadata={"kind": "direct_audit_user_message", "streaming": True},
                payload={"continued": False, "streaming": True},
            )
        )
        db.add(
            AuditSessionMessage(
                session_id=session.id,
                sequence=2,
                role="assistant",
                content="首条流式审计回复",
                message_metadata={"kind": "direct_audit_assistant_message", "streaming": True},
                payload={"continued": False},
            )
        )
        await db.commit()
        yield {"type": "session_created", "session_id": session.id, "project_id": project.id}
        yield {
            "type": "user_message",
            "message": {
                "id": "user-1",
                "session_id": session.id,
                "sequence": 1,
                "role": "user",
                "content": content,
                "metadata": {"kind": "direct_audit_user_message", "streaming": True},
                "payload": {"continued": False, "streaming": True},
                "created_at": session.created_at.isoformat(),
            },
        }
        yield {
            "type": "assistant_start",
            "message": {
                "id": "streaming-session-stream-1-2",
                "session_id": session.id,
                "sequence": 2,
                "role": "assistant",
                "content": "",
                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                "payload": {"continued": False},
                "created_at": session.created_at.isoformat(),
            },
        }
        yield {"type": "token", "content": "首条", "accumulated": "首条"}
        yield {"type": "token", "content": "流式审计回复", "accumulated": "首条流式审计回复"}
        yield {
            "type": "done",
            "message": {
                "id": "assistant-1",
                "session_id": session.id,
                "sequence": 2,
                "role": "assistant",
                "content": "首条流式审计回复",
                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                "payload": {"continued": False},
                "created_at": session.created_at.isoformat(),
            },
            "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
        }

    monkeypatch.setattr(
        agent_direct_audit_endpoint,
        "start_direct_audit_session_stream",
        fake_start_direct_audit_session_stream,
    )

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/stream",
            json={"project_id": "project-1", "content": "帮我实时审计这个项目"},
        )
        messages = await client.get("/api/v1/agent-direct-audit/sessions/session-stream-1/messages")

    await engine.dispose()

    assert response.status_code == 200
    events = parse_sse_events(response.text)
    assert [event["type"] for event in events] == ["session_created", "user_message", "assistant_start", "token", "token", "done"]
    assert events[0]["session_id"] == "session-stream-1"
    assert events[1]["message"]["content"] == "帮我实时审计这个项目"
    assert events[-1]["message"]["content"] == "首条流式审计回复"
    assert len(messages.json()) == 2
