from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
import app.api.v1.endpoints.agent_direct_audit as agent_direct_audit_endpoint
from app.api.v1.endpoints.agent_direct_audit import router as agent_direct_audit_router
from app.db.base import Base
from app.models.agent_task import AgentFinding, AgentTask
from app.models.audit_session import AuditCheckpoint, AuditSession, AuditSessionMessage, AuditToolCall
from app.models.managed_vulnerability import ManagedVulnerability
from app.models.project import Project
from app.models.user import User
from app.services.finding_runtime.models import RuntimeStopReason, TurnExecutionResult


EN_REPORT = """# SSRF in /api/fetch via untrusted target parameter (affected versions to be confirmed)

## Summary
An authenticated attacker can trigger server-side requests to internal resources through the `/api/fetch` endpoint by supplying an untrusted `target` value.

## Vulnerability Description
The request handler forwards a user-controlled URL to the outbound HTTP client without an allowlist or scheme restriction.

```python
target = request.json["target"]
response = httpx.get(target, timeout=5)
```

## Exploitation
1. Authenticate to the application.
2. Send a POST request to `/api/fetch` with `target` set to an internal resource.

```http
POST /api/fetch HTTP/1.1
Host: demo.local
Content-Type: application/json

{"target":"http://127.0.0.1:8080/admin"}
```

## Impact
This issue can expose internal services, metadata endpoints, or administrative interfaces that should not be reachable by the attacker.

## Remediation
Restrict outbound destinations with a strict allowlist, normalize URLs before validation, and block internal IP ranges and dangerous schemes.

## Disclosure Notes
The affected version range is still being confirmed.

## Supplemental Information
### Affected products
- Ecosystem: self-hosted
- Package name: demo-app
- Affected versions: to be confirmed
- Patched versions: none confirmed

### Severity
- Scoring method: CVSS v3.1
- Score: 8.6
- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N

### Weaknesses
- CWE: CWE-918 Server-Side Request Forgery (SSRF)
"""

ZH_REPORT = """# SSRF in /api/fetch（受影响版本待确认）

## Summary
具备认证权限的攻击者可以通过向 `/api/fetch` 端点提交不受信任的 `target` 参数，让服务器向内部资源发起请求。

## Vulnerability Description
请求处理逻辑会将用户可控 URL 直接传给出站 HTTP 客户端，且没有做 allowlist 或协议限制。

```python
target = request.json["target"]
response = httpx.get(target, timeout=5)
```

## Exploitation
1. 登录应用。
2. 向 `/api/fetch` 发送 POST 请求，并把 `target` 设为内部资源地址。

```http
POST /api/fetch HTTP/1.1
Host: demo.local
Content-Type: application/json

{"target":"http://127.0.0.1:8080/admin"}
```

## Impact
攻击者可以借此访问本不应暴露的内部服务、元数据接口或管理接口。

## Remediation
对出站目标实施严格 allowlist，标准化 URL 后再校验，并阻止内部 IP 段和危险协议。

## Disclosure Notes
受影响版本范围仍待确认。

## 补充信息
### Affected products
- Ecosystem: self-hosted
- Package name: demo-app
- Affected versions: to be confirmed
- Patched versions: none confirmed

### Severity
- Scoring method: CVSS v3.1
- Score: 8.6
- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N

### Weaknesses
- CWE: CWE-918 Server-Side Request Forgery (SSRF)
"""

CVE_REPORT = """# demo_ssrf_cve.md

## CVE Submission Helper

## Vulnerability type
- [x] SSRF

## CWE
CWE-918 Server-Side Request Forgery (SSRF)

## Vendor of the product(s)
Demo Vendor

## Affected product(s)/code base
### Product
demo-app

### Version
to be confirmed

## Attack type
- [x] Remote

## Impact
- [x] Information disclosure

### Other impact
None.

## Affected component(s)
`/api/fetch` endpoint

## Core vulnerable code path
```python
target = request.json["target"]
response = httpx.get(target, timeout=5)
```

## Attack vector(s)
Send a crafted POST request to `/api/fetch` with an internal URL in `target`.

## Suggested description of the vulnerability for use in the CVE
An authenticated SSRF vulnerability in `/api/fetch` allows attackers to make the server request internal resources via an untrusted `target` parameter.

## Discoverer(s)/Credits
cil

## Reference(s)
- [ ] TBD

## Additional information
The fix version is not yet confirmed.
"""


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


def build_report_bundle_payload() -> str:
    return json.dumps(
        {
            "report_en": EN_REPORT,
            "report_zh": ZH_REPORT,
            "report_cve": CVE_REPORT,
        },
        ensure_ascii=False,
    )


def build_findings_payload() -> dict[str, object]:
    return {
        "findings": [
            {
                "title": "SSRF through webhook fetcher",
                "vulnerability_type": "ssrf",
                "severity": "high",
                "file_path": "src/fetcher.py",
                "line_start": 12,
                "line_end": 18,
                "confidence": 0.93,
                "source": "POST body target",
                "sink": "httpx.get(target)",
                "description": "Outbound fetch uses attacker-controlled target without an allowlist.",
                "impact": "Enables requests to internal services.",
                "suggestion": "Restrict outbound targets and block internal ranges.",
                "references": ["CWE-918"],
            }
        ],
        "summary": "Confirmed one SSRF finding with a closed source-to-sink path.",
    }


@pytest.mark.asyncio
async def test_create_direct_audit_session_and_list_project_sessions(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async def fake_start_direct_audit_session(*, project, content, guardrails_enabled, db, current_user):
        del current_user
        session = AuditSession(
            project_id=project.id,
            task_id=None,
            runtime_stack="runtime",
            state="running",
            system_prompt="You are the direct audit finding agent.",
            recon_payload={"project_name": project.name},
            runtime_state_json={"metadata": {"guardrails": {"enabled": guardrails_enabled}}},
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
    assert create_response.json()["guardrails_enabled"] is False
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
            json={"scope": "single_use"},
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
    assert approvals[0]["scope"] == "single_use"
    assert messages.json()[0]["role"] == "user"
    assert messages.json()[0]["payload"]["continued"] is True
    assert messages.json()[0]["payload"]["approval_scope"] == "single_use"
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
async def test_continue_direct_audit_session_stream_emits_runtime_error_from_checkpoint(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-runtime-error-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="failed",
                system_prompt="direct audit",
                recon_payload={"project_info": {"workspace_root": "D:/Projects/AuditAI/projects/managed-demo"}},
                runtime_state_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AuditCheckpoint(
                session_id="session-runtime-error-1",
                turn_id="turn-runtime-error-1",
                checkpoint_type="auto",
                state_payload={
                    "stop_reason": "model_error",
                    "phase": "model",
                    "error": "无效的 API Key (使用了占位符): sk-your-api-key...",
                },
            )
        )
        await db.commit()
        session = await db.get(AuditSession, "session-runtime-error-1")
        assert session is not None

        class FakeBridge:
            async def continue_chat_session_stream(self, **kwargs):
                del kwargs
                return {
                    "session_id": "session-runtime-error-1",
                    "runner_result": TurnExecutionResult(
                        turn_id="turn-runtime-error-1",
                        stop_reason=RuntimeStopReason.MODEL_ERROR,
                    ),
                }

        class FakeSandbox:
            async def cleanup(self):
                return None

        async def fake_build_direct_runtime_context(**kwargs):
            del kwargs
            return FakeBridge(), FakeSandbox(), "finding", 8, "prompt", {}

        monkeypatch.setattr(
            agent_direct_audit_endpoint,
            "_build_direct_runtime_context",
            fake_build_direct_runtime_context,
        )

        events = []
        async for event in agent_direct_audit_endpoint.continue_direct_audit_session_stream(
            session=session,
            content="继续审计",
            db=db,
            current_user=SimpleNamespace(id="user-1", is_active=True),
        ):
            events.append(event)

    await engine.dispose()

    assert events == [
        {
            "type": "error",
            "message_text": "当前 LLM API Key 仍是占位符 `sk-your-api-key`，请先在模型配置或 backend/.env 中填入真实可用的 Key，再重试 Agent直审。",
        }
    ]


@pytest.mark.asyncio
async def test_stream_direct_audit_session_creation_emits_session_created_and_messages(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async def fake_start_direct_audit_session_stream(*, project, content, guardrails_enabled, db, current_user):
        del current_user
        session = AuditSession(
            id="session-stream-1",
            project_id=project.id,
            task_id=None,
            runtime_stack="runtime",
            state="running",
            system_prompt="direct audit",
            recon_payload={"project_name": project.name},
            runtime_state_json={"metadata": {"guardrails": {"enabled": guardrails_enabled}}},
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


@pytest.mark.asyncio
async def test_update_direct_audit_guardrails_persists_toggle():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-guardrails-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="running",
                system_prompt="direct audit",
                recon_payload={"project_name": "Managed Demo"},
                runtime_state_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            "/api/v1/agent-direct-audit/sessions/session-guardrails-1/guardrails",
            json={"enabled": True},
        )

    async with session_factory() as db:
        session = await db.get(AuditSession, "session-guardrails-1")

    await engine.dispose()

    assert response.status_code == 200
    assert response.json()["guardrails_enabled"] is True
    assert ((session.runtime_state_json or {}).get("metadata") or {}).get("guardrails", {}).get("enabled") is True


@pytest.mark.asyncio
async def test_stream_approve_direct_audit_shell_tool_call_grants_command_and_continues(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-shell-approve-1",
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
                id="tool-call-shell-1",
                session_id="session-shell-approve-1",
                turn_id="turn-1",
                sequence=1,
                tool_use_id="tool-use-1",
                tool_name="PowerShell",
                status="denied",
                is_concurrency_safe=False,
                input_payload={"command": "Set-Content README.md hello"},
                output_payload={
                    "permission_mode": "ask",
                    "permission_reason": "Running a mutating PowerShell command requires explicit approval while guardrails are enabled.",
                    "guardrail_code": "shell_command_requires_approval",
                },
                error_message="Running a mutating PowerShell command requires explicit approval while guardrails are enabled.",
            )
        )
        await db.commit()

    async def fake_continue_direct_audit_session_stream(*, session, content, db, current_user):
        del current_user
        assert "PowerShell command" in content
        approvals = ((session.runtime_state_json or {}).get("metadata") or {}).get("shell_approvals") or []
        assert approvals[0]["tool_name"] == "PowerShell"
        assert "Set-Content README.md hello" in approvals[0]["command"]

        assistant = AuditSessionMessage(
            session_id=session.id,
            sequence=2,
            role="assistant",
            content="approved shell follow-up",
            message_metadata={"kind": "direct_audit_assistant_message", "streaming": True},
            payload={"continued": True, "approval": True},
        )
        db.add(assistant)
        await db.commit()
        await db.refresh(assistant)
        yield {
            "type": "assistant_start",
            "message": {
                "id": "streaming-session-shell-approve-1-2",
                "session_id": session.id,
                "sequence": 2,
                "role": "assistant",
                "content": "",
                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                "payload": {"continued": True, "approval": True},
                "created_at": assistant.created_at.isoformat(),
            },
        }
        yield {"type": "token", "content": "approved ", "accumulated": "approved "}
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
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
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
            "/api/v1/agent-direct-audit/sessions/session-shell-approve-1/tool-calls/tool-call-shell-1/approve/stream",
            json={"scope": "session"},
        )
        messages = await client.get("/api/v1/agent-direct-audit/sessions/session-shell-approve-1/messages")

    async with session_factory() as db:
        session = await db.get(AuditSession, "session-shell-approve-1")

    await engine.dispose()

    assert response.status_code == 200
    events = parse_sse_events(response.text)
    assert [event["type"] for event in events] == ["user_message", "assistant_start", "token", "done"]
    assert "PowerShell" in events[0]["message"]["content"]
    approvals = ((session.runtime_state_json or {}).get("metadata") or {}).get("shell_approvals") or []
    assert approvals[0]["tool_name"] == "PowerShell"
    assert approvals[0]["scope"] == "session"
    assert events[1]["message"]["content"] == ""
    assert events[-1]["message"]["content"] == "approved shell follow-up"
    assert len(messages.json()) == 2
    assert messages.json()[0]["payload"]["approval_scope"] == "session"


@pytest.mark.asyncio
async def test_sync_latest_direct_audit_report_creates_managed_vulnerability_records():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-report-sync-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="completed",
                system_prompt="direct audit",
                recon_payload={
                    "project_info": {
                        "project_id": "project-1",
                        "name": "Managed Demo",
                        "workspace_root": "D:/Projects/AuditAI/projects/managed-demo",
                        "repository_url": "https://example.com/demo.git",
                        "default_branch": "main",
                    }
                },
                runtime_state_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AuditSessionMessage(
                id="message-report-bundle-1",
                session_id="session-report-sync-1",
                sequence=1,
                role="assistant",
                content=build_report_bundle_payload(),
                message_metadata={"kind": "direct_audit_assistant_message"},
                payload={"continued": True},
            )
        )
        await db.commit()

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-report-sync-1/managed-vulnerabilities/sync-latest-report",
        )
        synced = await client.get("/api/v1/agent-direct-audit/sessions/session-report-sync-1/managed-vulnerabilities")

    async with session_factory() as db:
        tasks = (await db.execute(select(AgentTask))).scalars().all()
        findings = (await db.execute(select(AgentFinding))).scalars().all()
        managed = (await db.execute(select(ManagedVulnerability))).scalars().all()

    await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == "project-1"
    assert payload["vulnerability_type"] == "ssrf"
    assert payload["severity"] == "high"
    assert payload["report_generation_status"] == "completed"
    assert {item["report_kind"] for item in payload["reports"]} == {"en", "zh", "cve"}
    assert len(tasks) == 1
    assert tasks[0].task_type == "agent_direct_audit_report"
    assert len(findings) == 1
    assert findings[0].task_id == tasks[0].id
    assert len(managed) == 1
    assert managed[0].finding_id == findings[0].id
    assert synced.status_code == 200
    assert len(synced.json()) == 1
    assert synced.json()[0]["id"] == payload["id"]


@pytest.mark.asyncio
async def test_sync_latest_direct_audit_report_reuses_existing_managed_vulnerability_for_same_report():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-report-sync-2",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="completed",
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
            AuditSessionMessage(
                id="message-report-bundle-2",
                session_id="session-report-sync-2",
                sequence=1,
                role="assistant",
                content=build_report_bundle_payload(),
                message_metadata={"kind": "direct_audit_assistant_message"},
                payload={"continued": True},
            )
        )
        await db.commit()

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-report-sync-2/managed-vulnerabilities/sync-latest-report",
        )
        second = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-report-sync-2/managed-vulnerabilities/sync-latest-report",
        )

    async with session_factory() as db:
        task_count = len((await db.execute(select(AgentTask))).scalars().all())
        finding_count = len((await db.execute(select(AgentFinding))).scalars().all())
        managed_count = len((await db.execute(select(ManagedVulnerability))).scalars().all())

    await engine.dispose()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert task_count == 1
    assert finding_count == 1
    assert managed_count == 1


@pytest.mark.asyncio
async def test_start_direct_audit_session_finalizes_payload_and_generates_report_bundle(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    findings_payload = build_findings_payload()
    continuation_calls: list[str] = []

    async with session_factory() as db:
        project = await db.get(Project, "project-1")
        current_user = SimpleNamespace(id="user-1", is_active=True)

        class FakeBridge:
            async def run_chat_session(self, **kwargs):
                session = AuditSession(
                    id="session-finalize-1",
                    project_id=project.id,
                    task_id=None,
                    runtime_stack="runtime",
                    state="failed",
                    system_prompt="direct audit",
                    recon_payload={
                        "project_info": {
                            "project_id": project.id,
                            "name": project.name,
                            "workspace_root": project.local_path,
                            "repository_url": "https://example.com/demo.git",
                            "default_branch": "main",
                        }
                    },
                    runtime_state_json={},
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(session)
                db.add(
                    AuditSessionMessage(
                        id="message-user-initial",
                        session_id=session.id,
                        sequence=1,
                        role="user",
                        content=str(kwargs.get("user_message") or ""),
                        message_metadata={"kind": "direct_audit_user_message"},
                        payload={"continued": False},
                    )
                )
                await db.commit()
                await kwargs["on_session_created"](session.id)
                return {
                    "session_id": session.id,
                    "runner_result": TurnExecutionResult(
                        turn_id="turn-finalize-1",
                        stop_reason=RuntimeStopReason.MAX_TURNS,
                    ),
                }

            async def continue_session_until_payload(self, **kwargs):
                del kwargs
                continuation_calls.append("continue")
                if len(continuation_calls) == 1:
                    db.add(
                        AuditSessionMessage(
                            id="message-final-findings",
                            session_id="session-finalize-1",
                            sequence=2,
                            role="assistant",
                            content=json.dumps(findings_payload, ensure_ascii=False),
                            message_metadata={"kind": "direct_audit_assistant_message"},
                            payload={"continued": False},
                        )
                    )
                    await db.commit()
                    return {
                        "session_id": "session-finalize-1",
                        "runner_result": TurnExecutionResult(
                            turn_id="turn-finalize-2",
                            stop_reason=RuntimeStopReason.COMPLETED,
                        ),
                        "final_payload": findings_payload,
                    }

                next_sequence = await db.scalar(
                    select(agent_direct_audit_endpoint.func.max(AuditSessionMessage.sequence)).where(
                        AuditSessionMessage.session_id == "session-finalize-1"
                    )
                )
                db.add(
                    AuditSessionMessage(
                        id="message-report-bundle",
                        session_id="session-finalize-1",
                        sequence=int(next_sequence or 0) + 1,
                        role="assistant",
                        content=build_report_bundle_payload(),
                        message_metadata={"kind": "direct_audit_assistant_message"},
                        payload={"continued": True},
                    )
                )
                await db.commit()
                return {
                    "session_id": "session-finalize-1",
                    "runner_result": TurnExecutionResult(
                        turn_id="turn-finalize-3",
                        stop_reason=RuntimeStopReason.COMPLETED,
                    ),
                    "final_payload": json.loads(build_report_bundle_payload()),
                }

        class FakeSandbox:
            async def cleanup(self):
                return None

        async def fake_build_direct_runtime_context(**kwargs):
            del kwargs
            return FakeBridge(), FakeSandbox(), "finding", 8, "prompt", {}

        monkeypatch.setattr(
            agent_direct_audit_endpoint,
            "_build_direct_runtime_context",
            fake_build_direct_runtime_context,
        )

        session = await agent_direct_audit_endpoint.start_direct_audit_session(
            project=project,
            content="请继续审计并收敛最终结论",
            guardrails_enabled=False,
            db=db,
            current_user=current_user,
        )

        messages = (
            await db.execute(
                select(AuditSessionMessage)
                .where(AuditSessionMessage.session_id == session.id)
                .order_by(AuditSessionMessage.sequence)
            )
        ).scalars().all()

    await engine.dispose()

    assert continuation_calls == ["continue", "continue"]
    assert session.state == "completed"
    assert any("summary" in message.content for message in messages if message.role == "assistant")
    assert any("report_en" in message.content for message in messages if message.role == "assistant")
    assert any(
        (message.message_metadata or {}).get("kind") == "internal_direct_audit_report_request"
        for message in messages
        if message.role == "user"
    )


@pytest.mark.asyncio
async def test_sync_latest_direct_audit_report_generates_missing_bundle_from_final_payload(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_direct_audit_fixture(session_factory)

    findings_payload = build_findings_payload()

    async with session_factory() as db:
        db.add(
            AuditSession(
                id="session-report-recovery-1",
                project_id="project-1",
                task_id=None,
                runtime_stack="runtime",
                state="failed",
                system_prompt="direct audit",
                recon_payload={
                    "project_info": {
                        "project_id": "project-1",
                        "name": "Managed Demo",
                        "workspace_root": "D:/Projects/AuditAI/projects/managed-demo",
                        "repository_url": "https://example.com/demo.git",
                        "default_branch": "main",
                    }
                },
                runtime_state_json={},
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AuditSessionMessage(
                id="message-final-findings-recovery",
                session_id="session-report-recovery-1",
                sequence=1,
                role="assistant",
                content=json.dumps(findings_payload, ensure_ascii=False),
                message_metadata={"kind": "direct_audit_assistant_message"},
                payload={"continued": True},
            )
        )
        await db.commit()

    class FakeBridge:
        async def continue_session_until_payload(self, **kwargs):
            del kwargs
            async with session_factory() as db:
                next_sequence = await db.scalar(
                    select(agent_direct_audit_endpoint.func.max(AuditSessionMessage.sequence)).where(
                        AuditSessionMessage.session_id == "session-report-recovery-1"
                    )
                )
                db.add(
                    AuditSessionMessage(
                        id="message-report-bundle-recovery",
                        session_id="session-report-recovery-1",
                        sequence=int(next_sequence or 0) + 1,
                        role="assistant",
                        content=build_report_bundle_payload(),
                        message_metadata={"kind": "direct_audit_assistant_message"},
                        payload={"continued": True},
                    )
                )
                await db.commit()
            return {
                "session_id": "session-report-recovery-1",
                "runner_result": TurnExecutionResult(
                    turn_id="turn-recovery-1",
                    stop_reason=RuntimeStopReason.COMPLETED,
                ),
                "final_payload": json.loads(build_report_bundle_payload()),
            }

    class FakeSandbox:
        async def cleanup(self):
            return None

    async def fake_build_direct_runtime_context(**kwargs):
        del kwargs
        return FakeBridge(), FakeSandbox(), "finding", 8, "prompt", {}

    monkeypatch.setattr(
        agent_direct_audit_endpoint,
        "_build_direct_runtime_context",
        fake_build_direct_runtime_context,
    )

    app = build_test_app()
    build_dependency_overrides(app, session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/agent-direct-audit/sessions/session-report-recovery-1/managed-vulnerabilities/sync-latest-report",
        )

    async with session_factory() as db:
        managed = (await db.execute(select(ManagedVulnerability))).scalars().all()
        session = await db.get(AuditSession, "session-report-recovery-1")

    await engine.dispose()

    assert response.status_code == 200
    assert response.json()["report_generation_status"] == "completed"
    assert len(managed) == 1
    assert session.state == "completed"
