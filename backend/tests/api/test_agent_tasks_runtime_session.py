from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api import deps
import app.api.v1.endpoints.agent_tasks as agent_tasks_endpoint
from app.api.v1.endpoints.agent_tasks import router as agent_tasks_router
from app.db.base import Base
from app.models.agent_task import AgentEvent, AgentEventType, AgentFinding, AgentTask, AgentTaskStatus
from app.models.audit_session import AuditSession, AuditSessionMessage, AuditSessionTurn, AuditToolCall
from app.models.managed_vulnerability import ManagedVulnerability
from app.models.project import Project
from app.models.user import User
from app.services.finding_runtime.config import FindingRuntimeStack
from app.services.vulnerability_report_generation import GeneratedReportBundle, VulnerabilityReportGenerationService
import app.services.agent.tools as agent_tools_module
import app.services.rag as rag_module


class CapturingEventEmitter:
    def __init__(self):
        self.events = []

    async def emit_info(self, message: str, metadata=None):
        self.events.append(("info", message, metadata or {}))

    async def emit_tool_call(self, tool_name: str, tool_input, message=None):
        self.events.append(("tool_call", tool_name, tool_input, message))

    async def emit_tool_result(self, tool_name: str, tool_output, duration_ms: int, message=None):
        self.events.append(("tool_result", tool_name, tool_output, duration_ms, message))


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(agent_tasks_router, prefix='/api/v1/agent-tasks')
    return app


def _valid_generated_report_bundle() -> GeneratedReportBundle:
    return GeneratedReportBundle(
        report_en='# EN\n\n## Summary\nS\n\n## Vulnerability Description\napp/api.py reaches httpx.get.\n\nCore vulnerable code path:\n\n```python\ntarget = request.json["target"]\nresponse = httpx.get(target, timeout=5)\n```\n\n## Exploitation\nPOST /api/fetch.\n\n## Impact\nInternal service exposure.\n\n## Remediation\nValidate outbound targets.\n\n## Disclosure Notes\nTo be confirmed.\n\n## Supplemental Information\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-918 Server-Side Request Forgery (SSRF)',
        report_zh='# ZH\n\n## Summary\nS\n\n## Vulnerability Description\napp/api.py reaches httpx.get.\n\nCore vulnerable code path:\n\n```python\ntarget = request.json["target"]\nresponse = httpx.get(target, timeout=5)\n```\n\n## Exploitation\nPOST /api/fetch.\n\n## Impact\nInternal service exposure.\n\n## Remediation\nValidate outbound targets.\n\n## Disclosure Notes\nTo be confirmed.\n\n## Supplemental Information\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-918 Server-Side Request Forgery (SSRF)',
        report_cve='# demo_cve.md\n\n## CVE Submission Helper\n\n## Vulnerability type\n- [x] Other or Unknown\n\n## CWE\nCWE-918 Server-Side Request Forgery (SSRF)\n\n## Vendor of the product(s)\nDemo Vendor\n\n## Affected product(s)/code base\n### Product\ndemo-app\n\n### Version\nto be confirmed\n\n## Attack type\n- [x] Remote\n\n## Impact\n- [x] Information Disclosure\n\n## Affected component(s)\n/api/fetch endpoint\n\n## Core vulnerable code path\napp/api.py reaches httpx.get.\n\nCore vulnerable code path:\n\n```python\ntarget = request.json["target"]\nresponse = httpx.get(target, timeout=5)\n```\n\n## Attack vector(s)\nPOST /api/fetch\n\n## Suggested description of the vulnerability for use in the CVE\nSSRF in fetch endpoint.\n\n## Discoverer(s)/Credits\ncil\n\n## Reference(s)\n\n## Additional information\nNone.',
    )


@pytest.mark.asyncio
async def test_create_agent_task_requires_version_label(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
            repository_url='https://example.com/demo.git',
        )
        db.add_all([user, project])
        await db.commit()

    async def fake_execute_agent_task(task_id: str):
        return None

    monkeypatch.setattr(agent_tasks_endpoint, '_execute_agent_task', fake_execute_agent_task)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.post(
            '/api/v1/agent-tasks/',
            json={
                'project_id': 'project-1',
                'name': 'Runtime audit',
            },
        )

    await engine.dispose()

    assert response.status_code == 422
    assert 'version_label' in response.text


@pytest.mark.asyncio
async def test_create_agent_task_persists_version_snapshot(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
            repository_url='https://example.com/demo.git',
        )
        db.add_all([user, project])
        await db.commit()

    async def fake_execute_agent_task(task_id: str):
        return None

    monkeypatch.setattr(agent_tasks_endpoint, '_execute_agent_task', fake_execute_agent_task)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.post(
            '/api/v1/agent-tasks/',
            json={
                'project_id': 'project-1',
                'name': 'Runtime audit',
                'branch_name': 'release/v1',
                'version_label': 'v1.2.3',
            },
        )

    async with session_factory() as db:
        task = await db.get(AgentTask, response.json()['id'])

    await engine.dispose()

    assert response.status_code == 200
    assert task is not None
    assert task.version_label == 'v1.2.3'
    assert task.branch_name == 'release/v1'
    assert task.repository_url_snapshot == 'https://example.com/demo.git'
    assert response.json()['version_label'] == 'v1.2.3'
    assert response.json()['repository_url_snapshot'] == 'https://example.com/demo.git'


@pytest.mark.asyncio
async def test_agent_task_routes_include_runtime_session_id():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.RUNNING,
            current_phase='analysis',
            created_at=datetime.now(timezone.utc),
        )
        session = AuditSession(
            id='session-1',
            project_id='project-1',
            task_id='task-1',
            runtime_stack='runtime',
            state='running',
        )
        db.add_all([user, project, task, session])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        list_response = await client.get('/api/v1/agent-tasks/')
        detail_response = await client.get('/api/v1/agent-tasks/task-1')

    await engine.dispose()

    assert list_response.status_code == 200
    assert list_response.json()[0]['runtime_session_id'] == 'session-1'

    assert detail_response.status_code == 200
    assert detail_response.json()['runtime_session_id'] == 'session-1'


@pytest.mark.asyncio
async def test_agent_task_detail_uses_persisted_runtime_turn_and_tool_stats():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.FAILED,
            current_phase='analysis',
            total_iterations=0,
            tool_calls_count=0,
            created_at=datetime.now(timezone.utc),
        )
        session = AuditSession(
            id='session-1',
            project_id='project-1',
            task_id='task-1',
            runtime_stack='runtime',
            state='failed',
        )
        turn_1 = AuditSessionTurn(id='turn-1', session_id='session-1', sequence=1, model_name='finding', status='tool_results_ready')
        turn_2 = AuditSessionTurn(id='turn-2', session_id='session-1', sequence=2, model_name='finding', status='model_error')
        tool_calls = [
            AuditToolCall(id='tool-1', session_id='session-1', turn_id='turn-1', sequence=1, tool_use_id='call-1', tool_name='Read', status='completed'),
            AuditToolCall(id='tool-2', session_id='session-1', turn_id='turn-1', sequence=2, tool_use_id='call-2', tool_name='Grep', status='completed'),
            AuditToolCall(id='tool-3', session_id='session-1', turn_id='turn-2', sequence=3, tool_use_id='call-3', tool_name='Read', status='completed'),
        ]
        db.add_all([user, project, task, session, turn_1, turn_2, *tool_calls])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.get('/api/v1/agent-tasks/task-1')

    await engine.dispose()

    assert response.status_code == 200
    assert response.json()['total_iterations'] == 2
    assert response.json()['tool_calls_count'] == 3


@pytest.mark.asyncio
async def test_agent_task_detail_exposes_finding_outcome_semantics():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        recovered_task = AgentTask(
            id='task-recovered',
            project_id='project-1',
            created_by='user-1',
            name='Recovered task',
            version_label='runtime-test',
            status=AgentTaskStatus.COMPLETED,
            current_phase='reporting',
            created_at=now,
            agent_config={
                'finding_runtime_stack': 'runtime',
                'finding_runtime_result': {
                    'finding_outcome': 'recovered_only',
                    'runtime_completion_mode': 'fallback_recovered',
                    'finalized_findings_count': 0,
                    'recovered_candidates_count': 1,
                    'handoff_ready': False,
                    'recovered_candidates': [
                        {
                            'title': 'Recovered SSRF candidate',
                            'severity': 'high',
                            'vulnerability_type': 'ssrf',
                            'description': 'Recovered from transcript only.',
                            'file_path': 'src/demo.py',
                            'line_start': 42,
                            'report_status': 'recovered_candidate',
                            'origin': 'transcript_recovery',
                            'evidence_type': 'transcript_recovery',
                            'not_finalized': True,
                        }
                    ],
                },
            },
        )
        finalized_task = AgentTask(
            id='task-finalized',
            project_id='project-1',
            created_by='user-1',
            name='Finalized task',
            version_label='runtime-test',
            status=AgentTaskStatus.COMPLETED,
            current_phase='reporting',
            created_at=now,
            findings_count=2,
            high_count=2,
            agent_config={
                'finding_runtime_stack': 'runtime',
                'finding_runtime_result': {
                    'finding_outcome': 'finalized',
                    'runtime_completion_mode': 'finalize_tool',
                    'finalized_findings_count': 2,
                    'recovered_candidates_count': 0,
                    'handoff_ready': True,
                    'recovered_candidates': [],
                },
            },
        )
        db.add_all([user, project, recovered_task, finalized_task])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        recovered_response = await client.get('/api/v1/agent-tasks/task-recovered')
        finalized_response = await client.get('/api/v1/agent-tasks/task-finalized')

    await engine.dispose()

    assert recovered_response.status_code == 200
    assert recovered_response.json()['finding_outcome'] == 'recovered_only'
    assert recovered_response.json()['runtime_completion_mode'] == 'fallback_recovered'
    assert recovered_response.json()['finalized_findings_count'] == 0
    assert recovered_response.json()['recovered_candidates_count'] == 1
    assert recovered_response.json()['handoff_ready'] is False
    assert recovered_response.json()['recovered_candidates'][0]['title'] == 'Recovered SSRF candidate'

    assert finalized_response.status_code == 200
    assert finalized_response.json()['finding_outcome'] == 'finalized'
    assert finalized_response.json()['runtime_completion_mode'] == 'finalize_tool'
    assert finalized_response.json()['finalized_findings_count'] == 2
    assert finalized_response.json()['recovered_candidates_count'] == 0
    assert finalized_response.json()['handoff_ready'] is True


@pytest.mark.asyncio
async def test_create_agent_task_persists_runtime_stack_in_agent_config(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        db.add_all([user, project])
        await db.commit()

    async def fake_execute_agent_task(task_id: str):
        return None

    monkeypatch.setattr(agent_tasks_endpoint, '_execute_agent_task', fake_execute_agent_task)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.post(
            '/api/v1/agent-tasks/',
            json={
                'project_id': 'project-1',
                'name': 'Runtime audit',
                'version_label': 'runtime-v1',
                'finding_runtime_stack': 'runtime',
            },
        )

    async with session_factory() as db:
        task = await db.get(AgentTask, response.json()['id'])

    await engine.dispose()

    assert response.status_code == 200
    assert task is not None
    assert task.agent_config == {'finding_runtime_stack': 'runtime'}


@pytest.mark.asyncio
async def test_agent_task_routes_include_resolved_runtime_stack():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.RUNNING,
            current_phase='analysis',
            created_at=datetime.now(timezone.utc),
            agent_config={'finding_runtime_stack': 'runtime'},
        )
        db.add_all([user, project, task])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        list_response = await client.get('/api/v1/agent-tasks/')
        detail_response = await client.get('/api/v1/agent-tasks/task-1')

    await engine.dispose()

    assert list_response.status_code == 200
    assert list_response.json()[0]['finding_runtime_stack'] == 'runtime'
    assert detail_response.status_code == 200
    assert detail_response.json()['finding_runtime_stack'] == 'runtime'


@pytest.mark.asyncio
async def test_create_agent_task_uses_default_runtime_stack_from_settings(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        db.add_all([user, project])
        await db.commit()

    async def fake_execute_agent_task(task_id: str):
        return None

    monkeypatch.setattr(agent_tasks_endpoint, '_execute_agent_task', fake_execute_agent_task)
    monkeypatch.setattr(agent_tasks_endpoint.settings, 'FINDING_RUNTIME_STACK_DEFAULT', 'runtime', raising=False)

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.post(
            '/api/v1/agent-tasks/',
            json={
                'project_id': 'project-1',
                'name': 'Default runtime audit',
                'version_label': 'runtime-default',
            },
        )

    async with session_factory() as db:
        task = await db.get(AgentTask, response.json()['id'])

    await engine.dispose()

    assert response.status_code == 200
    assert task is not None
    assert task.agent_config == {'finding_runtime_stack': 'runtime'}


@pytest.mark.asyncio
async def test_agent_task_events_list_returns_history_for_activity_log():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.RUNNING,
            current_phase='analysis',
            created_at=datetime.now(timezone.utc),
        )
        event = AgentEvent(
            id='event-1',
            task_id='task-1',
            event_type=AgentEventType.THINKING,
            sequence=1,
            phase='analysis',
            message='thinking...',
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([user, project, task, event])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.get('/api/v1/agent-tasks/task-1/events/list')

    await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]['task_id'] == 'task-1'
    assert payload[0]['event_type'] == AgentEventType.THINKING
    assert payload[0]['timestamp']
    assert payload[0]['created_at']


@pytest.mark.asyncio
async def test_stream_agent_events_does_not_require_progress_percent_attribute():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.CANCELLED,
            current_phase='analysis',
            created_at=datetime.now(timezone.utc),
        )
        event = AgentEvent(
            id='event-1',
            task_id='task-1',
            event_type=AgentEventType.THINKING,
            sequence=1,
            phase='analysis',
            message='thinking...',
            event_metadata={'progress_percent': 30},
            created_at=datetime.now(timezone.utc),
        )
        db.add_all([user, project, task, event])
        await db.commit()

    app = build_test_app()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    async def override_get_current_user():
        return SimpleNamespace(id='user-1', is_active=True)

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(agent_tasks_endpoint, 'async_session_factory', session_factory, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.get('/api/v1/agent-tasks/task-1/events')

    await engine.dispose()
    monkeypatch.undo()

    assert response.status_code == 200
    assert '"progress_percent": 30' in response.text
    assert '"type": "task_end"' in response.text


@pytest.mark.asyncio
async def test_auto_generate_managed_reports_when_verification_disabled(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
            repository_url='https://example.com/demo.git',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='v1.2.3',
            branch_name='release/v1',
            repository_url_snapshot='https://example.com/demo.git',
            status=AgentTaskStatus.COMPLETED,
            current_phase='reporting',
        )
        session = AuditSession(
            id='session-1',
            project_id='project-1',
            task_id='task-1',
            runtime_stack='runtime',
            state='completed',
        )
        finding = AgentFinding(
            id='finding-1',
            task_id='task-1',
            vulnerability_type='ssrf',
            severity='high',
            title='SSRF in fetch endpoint',
            description='User controlled target reaches outbound HTTP client.',
            file_path='app/api.py',
            line_start=21,
            line_end=22,
            code_snippet='response = httpx.get(target, timeout=5)',
            finding_metadata={
                'raw_finding': {
                    'title': 'SSRF in fetch endpoint',
                    'description': 'User controlled target reaches outbound HTTP client.',
                    'impact': 'Internal service exposure',
                    'references': ['https://owasp.org/www-community/attacks/Server_Side_Request_Forgery'],
                }
            },
        )
        db.add_all([user, project, task, session, finding])
        await db.commit()

        async def fake_get_user_config(db_session, user_id):
            return {}

        captured = {}

        async def fake_generate_managed_report_bundle_from_session(
            db_session,
            *,
            session,
            task,
            finding,
            managed_vulnerability,
            report_service,
        ):
            del db_session, task, finding
            captured['session_id'] = session.id
            captured['managed_vulnerability_id'] = managed_vulnerability.id
            prompt = report_service.build_generation_prompt(vulnerability=managed_vulnerability)
            assert 'cve-report-writer' not in prompt
            assert 'Read/Grep/Glob/Skill' not in prompt
            assert 'FinalizeVulnerabilityReports' in prompt
            return GeneratedReportBundle(
                report_en='# EN\n\n## Summary\n\n## Vulnerability Description\n\n## Exploitation\n\n## Impact\n\n## Remediation\n\n## Disclosure Notes\n\n## Supplemental Information\n\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-918 Server-Side Request Forgery (SSRF)',
                report_zh='# ZH\n\n## Summary\n\n## Vulnerability Description\n\n## Exploitation\n\n## Impact\n\n## Remediation\n\n## Disclosure Notes\n\n## 补充信息\n\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-918 Server-Side Request Forgery (SSRF)',
                report_cve='# demo_cve.md\n\n## CVE Submission Helper\n\n## Vulnerability type\n- [x] SSRF\n\n## CWE\nCWE-918 Server-Side Request Forgery (SSRF)\n\n## Vendor of the product(s)\nDemo Vendor\n\n## Affected product(s)/code base\n### Product\ndemo-app\n\n### Version\nto be confirmed\n\n## Attack type\n- [x] Remote\n\n## Impact\n- [x] Information disclosure\n\n## Affected component(s)\n/api/fetch endpoint\n\n## Core vulnerable code path\n`python\nresponse = httpx.get(target, timeout=5)\n`\n\n## Attack vector(s)\nPOST /api/fetch\n\n## Suggested description of the vulnerability for use in the CVE\nSSRF in fetch endpoint.\n\n## Discoverer(s)/Credits\ncil\n\n## Reference(s)\n- [ ] TBD\n\n## Additional information\nNone.',
            )

        async def fail_chat_completion(self, *args, **kwargs):
            raise AssertionError('chat_completion should not be called for managed report generation')

        monkeypatch.setattr(agent_tasks_endpoint, '_get_user_config', fake_get_user_config)
        monkeypatch.setattr(agent_tasks_endpoint, '_generate_managed_report_bundle_from_session', fake_generate_managed_report_bundle_from_session)
        monkeypatch.setattr('app.services.llm.service.LLMService.chat_completion', fail_chat_completion)
        event_emitter = CapturingEventEmitter()

        stats = await agent_tasks_endpoint._auto_generate_managed_vulnerability_reports(
            db=db,
            task=task,
            workflow_config={'agentStates': {'verification': {'enabled': False}}},
            findings=[finding],
            event_emitter=event_emitter,
        )
        await db.commit()

        managed_result = await db.execute(
            select(ManagedVulnerability)
            .options(selectinload(ManagedVulnerability.reports))
            .where(ManagedVulnerability.task_id == 'task-1')
        )
        managed = managed_result.scalar_one()
        message_result = await db.execute(
            select(AuditSessionMessage)
            .where(AuditSessionMessage.session_id == 'session-1')
            .order_by(AuditSessionMessage.sequence)
        )
        messages = list(message_result.scalars().all())

    await engine.dispose()

    assert stats == {'generated': 1, 'failed': 0, 'skipped': 0}
    assert managed.report_generation_status == 'completed'
    assert {report.report_kind for report in managed.reports} == {'en', 'zh', 'cve'}
    assert captured == {'session_id': 'session-1', 'managed_vulnerability_id': managed.id}
    assert len(messages) == 2
    assert messages[0].role == 'user'
    assert messages[0].message_metadata['kind'] == 'internal_managed_report_request'
    assert 'FinalizeVulnerabilityReports' in messages[0].content
    assert 'cve-report-writer' not in messages[0].content
    assert messages[1].message_metadata['kind'] == 'internal_managed_report_complete'
    info_messages = [event[1] for event in event_emitter.events if event[0] == 'info']
    assert any('Managed report generation prompt' in message for message in info_messages)
    assert any('Managed report model response' in message for message in info_messages)
    assert all('cve-report-writer' not in message for message in info_messages)


@pytest.mark.asyncio
async def test_auto_generate_managed_reports_runs_when_verification_enabled(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='v1.2.3',
            status=AgentTaskStatus.COMPLETED,
        )
        finding = AgentFinding(
            id='finding-1',
            task_id='task-1',
            vulnerability_type='ssrf',
            severity='high',
            title='SSRF in fetch endpoint',
            description='User controlled target reaches outbound HTTP client.',
            file_path='app/api.py',
            line_start=21,
            line_end=22,
            finding_metadata={'raw_finding': {'impact': 'Internal service exposure'}},
        )
        db.add_all([user, project, task, finding])
        await db.commit()

        async def fake_chat_completion(self, *args, **kwargs):
            del self, args, kwargs
            return {"content": json.dumps({
                "report_en": _valid_generated_report_bundle().report_en,
                "report_zh": _valid_generated_report_bundle().report_zh,
                "report_cve": _valid_generated_report_bundle().report_cve,
            })}

        monkeypatch.setattr('app.services.llm.service.LLMService.chat_completion', fake_chat_completion)

        stats = await agent_tasks_endpoint._auto_generate_managed_vulnerability_reports(
            db=db,
            task=task,
            workflow_config={'agentStates': {'verification': {'enabled': True}}},
            findings=[finding],
        )

        managed_result = await db.execute(select(ManagedVulnerability).where(ManagedVulnerability.task_id == 'task-1'))
        message_result = await db.execute(select(AuditSessionMessage).where(AuditSessionMessage.session_id == 'task-1'))

    await engine.dispose()

    assert stats == {'generated': 1, 'failed': 0, 'skipped': 0}
    assert len(managed_result.scalars().all()) == 1
    assert message_result.scalars().all() == []


@pytest.mark.asyncio
async def test_auto_generate_managed_reports_runs_when_verification_config_missing(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Managed Project',
            description='demo',
            owner_id=user.id,
            source_type='repository',
            repository_url='https://example.com/repo.git',
        )
        task = AgentTask(
            id='task-1',
            project_id=project.id,
            created_by=user.id,
            name='Audit demo',
            version_label='runtime-test',
            status=AgentTaskStatus.COMPLETED,
            current_phase='reporting',
            agent_config={'finding_runtime_stack': 'runtime'},
        )
        finding = AgentFinding(
            id='finding-1',
            task_id=task.id,
            title='Recovered finding',
            severity='high',
            vulnerability_type='idor',
            description='Recovered from transcript',
            file_path='server/api/UserRoute.js',
            line_start=10,
            line_end=20,
        )
        session = AuditSession(
            id='session-1',
            project_id=project.id,
            task_id=task.id,
            runtime_stack='runtime',
            state='completed',
            system_prompt='audit',
            recon_payload={'project_info': {'workspace_root': '/tmp/project'}},
            runtime_state_json={},
        )
        db.add_all([user, project, task, finding, session])
        await db.commit()

        async def fake_get_user_config(db_session, user_id):
            del db_session, user_id
            return {}

        async def fake_generate_managed_report_bundle_from_session(
            db_session,
            *,
            session,
            task,
            finding,
            managed_vulnerability,
            report_service,
        ):
            del db_session, task, finding, managed_vulnerability, report_service
            assert session.id == 'session-1'
            return GeneratedReportBundle(
                report_en='# EN\n\n## Summary\n\n## Vulnerability Description\n\n## Exploitation\n\n## Impact\n\n## Remediation\n\n## Disclosure Notes\n\n## Supplemental Information\n\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-639 Authorization Bypass Through User-Controlled Key',
                report_zh='# ZH\n\n## Summary\n\n## Vulnerability Description\n\n## Exploitation\n\n## Impact\n\n## Remediation\n\n## Disclosure Notes\n\n## 补充信息\n\n### Affected products\n- Ecosystem: self-hosted\n- Package name: demo-app\n- Affected versions: to be confirmed\n- Patched versions: none confirmed\n\n### Severity\n- Scoring method: CVSS v3.1\n- Score: 8.6\n- Vector string: CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:H/I:L/A:N\n\n### Weaknesses\n- CWE: CWE-639 Authorization Bypass Through User-Controlled Key',
                report_cve='# demo_cve.md\n\n## CVE Submission Helper\n\n## Vulnerability type\n- [x] Incorrect Authorization\n\n## CWE\nCWE-639 Authorization Bypass Through User-Controlled Key\n\n## Vendor of the product(s)\nDemo Vendor\n\n## Affected product(s)/code base\n### Product\ndemo-app\n\n### Version\nto be confirmed\n\n## Attack type\n- [x] Remote\n\n## Impact\n- [x] Unauthorized access\n\n## Affected component(s)\n/api/user endpoint\n\n## Core vulnerable code path\n`javascript\nreturn res.status(200).send(user)\n`\n\n## Attack vector(s)\nGET /api/user?id=<other-user>\n\n## Suggested description of the vulnerability for use in the CVE\nIDOR in user endpoint.\n\n## Discoverer(s)/Credits\ncil\n\n## Reference(s)\n- [ ] TBD\n\n## Additional information\nNone.',
            )

        async def fail_chat_completion(self, *args, **kwargs):
            raise AssertionError('chat_completion should not be called when runtime session is available')

        monkeypatch.setattr(agent_tasks_endpoint, '_get_user_config', fake_get_user_config)
        monkeypatch.setattr(
            agent_tasks_endpoint,
            '_generate_managed_report_bundle_from_session',
            fake_generate_managed_report_bundle_from_session,
        )
        monkeypatch.setattr('app.services.llm.service.LLMService.chat_completion', fail_chat_completion)

        stats = await agent_tasks_endpoint._auto_generate_managed_vulnerability_reports(
            db=db,
            task=task,
            workflow_config={'finding_runtime_stack': 'runtime'},
            findings=[finding],
        )
        await db.commit()

        managed_result = await db.execute(select(ManagedVulnerability))
        managed = managed_result.scalar_one()

    await engine.dispose()

    assert stats == {'generated': 1, 'failed': 0, 'skipped': 0}
    assert managed.report_generation_status == 'completed'


@pytest.mark.asyncio
async def test_generate_managed_report_bundle_uses_unbounded_report_continuation(monkeypatch):
    captured: dict[str, object] = {}

    class FakeBridge:
        def prepare_report_generation_continuation(self, **kwargs):
            captured["prepare_kwargs"] = kwargs

        async def continue_session_until_report_payload(self, **kwargs):
            captured.update(kwargs)
            bundle = _valid_generated_report_bundle()
            return {
                "final_payload": {
                    "report_en": bundle.report_en,
                    "report_zh": bundle.report_zh,
                    "report_cve": bundle.report_cve,
                }
            }

    class FakeSandbox:
        async def cleanup(self):
            captured["cleanup"] = True

    async def fake_build_runtime_follow_up_context(**kwargs):
        del kwargs
        return FakeBridge(), FakeSandbox(), "finding-runtime", 99

    monkeypatch.setattr(
        "app.api.v1.endpoints.audit_sessions._build_runtime_follow_up_context",
        fake_build_runtime_follow_up_context,
    )

    managed = ManagedVulnerability(
        id="managed-1",
        project_id="project-1",
        task_id="task-1",
        finding_id="finding-1",
        project_name="Demo Project",
        version_label="v1.0.0",
        vulnerability_name="SSRF in fetch endpoint",
        vulnerability_type="ssrf",
        severity="high",
    )

    result = await agent_tasks_endpoint._generate_managed_report_bundle_from_session(
        db=None,
        session=SimpleNamespace(id="session-1", runtime_stack=FindingRuntimeStack.RUNTIME.value),
        task=SimpleNamespace(created_by="user-1"),
        finding=SimpleNamespace(id="finding-1"),
        managed_vulnerability=managed,
        report_service=VulnerabilityReportGenerationService(),
    )

    assert isinstance(result, GeneratedReportBundle)
    prepare_kwargs = captured["prepare_kwargs"]
    assert prepare_kwargs["session_id"] == "session-1"
    assert "report generation continuation runtime" in prepare_kwargs["system_prompt"]
    assert "<report_generation_context>" in prepare_kwargs["context_message"]
    assert "managed-1" in prepare_kwargs["context_message"]
    assert prepare_kwargs["metadata"]["kind"] == "internal_managed_report_request"
    assert captured["max_turns"] is None
    assert captured["finalizer_prompts"] == []
    assert "FinalizeVulnerabilityReports" in str(captured["terminal_action_nudge_message"])
    assert captured["cleanup"] is True


def test_disabled_managed_report_stats_skips_all_persisted_findings():
    stats = agent_tasks_endpoint._disabled_managed_report_stats(
        [SimpleNamespace(id='finding-1'), SimpleNamespace(id='finding-2')]
    )

    assert agent_tasks_endpoint.AUTO_MANAGED_REPORT_POSTPROCESSING_ENABLED is True
    assert stats == {'generated': 0, 'failed': 0, 'skipped': 2}


@pytest.mark.asyncio
async def test_sync_managed_vulnerability_records_imports_findings_without_generating_reports(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Managed Project',
            owner_id=user.id,
            source_type='repository',
            repository_url='https://example.com/repo.git',
        )
        task = AgentTask(
            id='task-1',
            project_id=project.id,
            created_by=user.id,
            name='Runtime audit',
            version_label='runtime-test',
            status=AgentTaskStatus.COMPLETED,
        )
        finding = AgentFinding(
            id='finding-1',
            task_id=task.id,
            title='SSRF in fetch endpoint',
            severity='high',
            vulnerability_type='ssrf',
            description='User controlled target reaches outbound HTTP client.',
            file_path='app/api.py',
            line_start=21,
            line_end=22,
            finding_metadata={'raw_finding': {'impact': 'Internal service exposure'}},
        )
        db.add_all([user, project, task, finding])
        await db.commit()

        async def fail_chat_completion(self, *args, **kwargs):
            raise AssertionError('chat_completion should not be called when importing managed vulnerability records')

        monkeypatch.setattr('app.services.llm.service.LLMService.chat_completion', fail_chat_completion)

        stats = await agent_tasks_endpoint._sync_managed_vulnerability_records(
            db=db,
            task=task,
            findings=[finding],
        )
        await db.commit()

        managed_result = await db.execute(
            select(ManagedVulnerability)
            .options(selectinload(ManagedVulnerability.reports))
            .where(ManagedVulnerability.task_id == task.id)
        )
        managed = managed_result.scalar_one()

    await engine.dispose()

    assert agent_tasks_endpoint.AUTO_MANAGED_REPORT_POSTPROCESSING_ENABLED is True
    assert stats == {'created': 1, 'existing': 0, 'failed': 0}
    assert managed.finding_id == 'finding-1'
    assert managed.vulnerability_name == 'SSRF in fetch endpoint'
    assert managed.report_generation_status == 'pending'
    assert {report.report_kind for report in managed.reports} == {'en', 'zh', 'cve'}
    assert all(report.generation_status == 'pending' for report in managed.reports)
    assert all(report.markdown_content == '' for report in managed.reports)


@pytest.mark.asyncio
async def test_get_project_root_falls_back_to_managed_directory_when_zip_missing(monkeypatch, tmp_path):
    managed_root = tmp_path / 'projects'
    fallback_dir = managed_root / 'chartbrew-4.9.0'
    fallback_dir.mkdir(parents=True)
    (fallback_dir / 'README.md').write_text('fallback workspace', encoding='utf-8')

    async def fake_load_project_zip(project_id):
        del project_id
        return None

    original_root = agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT
    monkeypatch.setattr('app.services.zip_storage.load_project_zip', fake_load_project_zip)
    agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root)

    project = Project(
        id='project-zip-1',
        name='chartbrew',
        owner_id='user-1',
        source_type='zip',
        repository_type='other',
        default_branch='main',
    )

    try:
        project_root = await agent_tasks_endpoint._get_project_root(project, 'zip-fallback-task')
        assert Path(project_root, 'README.md').read_text(encoding='utf-8') == 'fallback workspace'
        assert Path(project_root).resolve() != fallback_dir.resolve()
        assert Path(project_root).resolve().is_relative_to(
            managed_root.resolve() / '.auditai_workspaces' / 'projects' / project.id
        )
    finally:
        agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root


@pytest.mark.asyncio
async def test_get_project_root_copies_persistent_zip_source_directory_when_available(tmp_path):
    managed_root = tmp_path / 'projects'
    persistent_root = managed_root / 'zip-demo'
    persistent_root.mkdir(parents=True)
    (persistent_root / 'README.md').write_text('persistent workspace', encoding='utf-8')
    original_root = agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT
    agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root)

    project = Project(
        id='project-zip-persistent-1',
        name='zip-demo',
        owner_id='user-1',
        source_type='zip',
        local_path=str(persistent_root.resolve()),
        repository_type='other',
        default_branch='main',
    )

    try:
        project_root = await agent_tasks_endpoint._get_project_root(project, 'zip-persistent-task')
        assert Path(project_root, 'README.md').read_text(encoding='utf-8') == 'persistent workspace'
        assert Path(project_root).resolve() != persistent_root.resolve()
        assert Path(project_root).resolve().is_relative_to(
            managed_root.resolve() / '.auditai_workspaces' / 'projects' / project.id
        )
    finally:
        agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root


@pytest.mark.asyncio
async def test_get_project_root_reuses_project_scoped_workspace_without_refresh(tmp_path):
    managed_root = tmp_path / 'projects'
    persistent_root = managed_root / 'zip-demo'
    persistent_root.mkdir(parents=True)
    (persistent_root / 'README.md').write_text('persistent workspace', encoding='utf-8')
    original_root = agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT
    agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root)

    project = Project(
        id='project-reuse-1',
        name='zip-demo',
        owner_id='user-1',
        source_type='zip',
        local_path=str(persistent_root.resolve()),
        repository_type='other',
        default_branch='main',
    )

    try:
        first_root = Path(await agent_tasks_endpoint._get_project_root(project, 'task-a'))
        marker = first_root / '.auditai' / 'tasks' / 'task-a' / 'marker.txt'
        marker.parent.mkdir(parents=True)
        marker.write_text('keep me', encoding='utf-8')

        second_root = Path(await agent_tasks_endpoint._get_project_root(project, 'task-b'))

        assert second_root.resolve() == first_root.resolve()
        assert first_root.resolve().is_relative_to(
            managed_root.resolve() / '.auditai_workspaces' / 'projects' / project.id
        )
        assert marker.read_text(encoding='utf-8') == 'keep me'
    finally:
        agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root


@pytest.mark.asyncio
async def test_get_project_root_refresh_rebuilds_project_scoped_workspace(tmp_path):
    managed_root = tmp_path / 'projects'
    persistent_root = managed_root / 'zip-demo'
    persistent_root.mkdir(parents=True)
    (persistent_root / 'README.md').write_text('persistent workspace', encoding='utf-8')
    original_root = agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT
    agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = str(managed_root)

    project = Project(
        id='project-refresh-1',
        name='zip-demo',
        owner_id='user-1',
        source_type='zip',
        local_path=str(persistent_root.resolve()),
        repository_type='other',
        default_branch='main',
    )

    try:
        project_root = Path(await agent_tasks_endpoint._get_project_root(project, 'task-a'))
        marker = project_root / '.auditai' / 'tasks' / 'task-a' / 'marker.txt'
        marker.parent.mkdir(parents=True)
        marker.write_text('remove me', encoding='utf-8')

        refreshed_root = Path(
            await agent_tasks_endpoint._get_project_root(project, 'task-b', refresh=True)
        )

        assert refreshed_root.resolve() == project_root.resolve()
        assert not marker.exists()
        assert (refreshed_root / 'README.md').read_text(encoding='utf-8') == 'persistent workspace'
    finally:
        agent_tasks_endpoint.settings.MANAGED_PROJECTS_ROOT = original_root


@pytest.mark.asyncio
async def test_initialize_tools_builds_sandbox_tools_without_local_scope_error(monkeypatch):
    class FakeSimpleTool:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class FakeSandboxTool(FakeSimpleTool):
        pass

    simple_tool_names = [
        'FileReadTool',
        'ReadManyFilesTool',
        'FileSearchTool',
        'ListFilesTool',
        'PatternMatchTool',
        'DataFlowAnalysisTool',
        'SemgrepTool',
        'BanditTool',
        'GitleaksTool',
        'NpmAuditTool',
        'SafetyTool',
        'TruffleHogTool',
        'OSVScannerTool',
        'ThinkTool',
        'ReflectTool',
        'CreateVulnerabilityReportTool',
        'SkillBodyTool',
        'SkillResourceTool',
        'SmartScanTool',
        'QuickAuditTool',
        'SandboxTool',
        'SandboxHttpTool',
        'VulnerabilityVerifyTool',
        'PhpTestTool',
        'PythonTestTool',
        'JavaScriptTestTool',
        'JavaTestTool',
        'GoTestTool',
        'RubyTestTool',
        'ShellTestTool',
        'UniversalCodeTestTool',
        'CommandInjectionTestTool',
        'SqlInjectionTestTool',
        'XssTestTool',
        'PathTraversalTestTool',
        'SstiTestTool',
        'DeserializationTestTool',
        'UniversalVulnTestTool',
        'RunCodeTool',
        'ExtractFunctionTool',
    ]

    for name in simple_tool_names:
        monkeypatch.setattr(
            agent_tools_module,
            name,
            FakeSandboxTool if name == 'SandboxTool' else type(name, (FakeSimpleTool,), {}),
            raising=False,
        )

    monkeypatch.setattr(agent_tools_module, 'build_shared_agent_tool_catalog', lambda **kwargs: {'read_file': FakeSimpleTool()}, raising=False)
    monkeypatch.setattr(agent_tools_module, 'build_agent_skill_tools', lambda **kwargs: {}, raising=False)
    monkeypatch.setattr(agent_tools_module, 'build_agent_tool_catalog', lambda **kwargs: {}, raising=False)
    monkeypatch.setattr(agent_tasks_endpoint, 'SecurityKnowledgeQueryTool', type('SecurityKnowledgeQueryTool', (FakeSimpleTool,), {}), raising=False)
    monkeypatch.setattr(agent_tasks_endpoint, 'GetVulnerabilityKnowledgeTool', type('GetVulnerabilityKnowledgeTool', (FakeSimpleTool,), {}), raising=False)

    tools = await agent_tasks_endpoint._initialize_tools(
        project_root='D:/repo',
        llm_service=SimpleNamespace(),
        user_config={},
        sandbox_manager=SimpleNamespace(),
        user_id='user-1',
    )

    assert 'sandbox_exec' in tools['finding']
    assert isinstance(tools['finding']['sandbox_exec'], FakeSandboxTool)
    assert 'sandbox_exec' in tools['verification']
    assert isinstance(tools['verification']['sandbox_exec'], FakeSandboxTool)
    assert 'rag_query' not in tools['recon']
    assert 'rag_query' not in tools['analysis']
    assert 'security_search' not in tools['analysis']
    assert 'function_context' not in tools['analysis']
    assert 'rag_query' not in tools['finding']
    assert 'query_security_knowledge' not in tools['analysis']
    assert 'get_vulnerability_knowledge' not in tools['analysis']


def test_initialize_tools_does_not_import_legacy_knowledge_tools():
    source = Path(agent_tasks_endpoint.__file__).read_text(encoding='utf-8')
    initialize_tools_source = source[source.index('async def _initialize_tools') : source.index('async def _collect_project_info')]
    assert 'SecurityKnowledgeQueryTool' not in initialize_tools_source
    assert 'GetVulnerabilityKnowledgeTool' not in initialize_tools_source
