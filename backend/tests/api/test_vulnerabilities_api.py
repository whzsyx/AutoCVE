from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import deps
from app.api.v1.api import api_router
from app.db.base import Base
from app.models.agent_task import AgentTask
from app.models.managed_vulnerability import ManagedVulnerability, ManagedVulnerabilityReport
from app.models.project import Project
from app.models.user import User

EN_REPORT = """# EN

## Summary

## Details

## POC

## Impact

## Remediation

## Disclosure Notes

## Supplemental Information

### Affected products
- Ecosystem: self-hosted

### Severity
- Scoring method: CVSS v3.1

### Weaknesses
- CWE: CWE-918
"""

ZH_REPORT = """# ZH

## Summary

## Details

## POC

## Impact

## Remediation

## Disclosure Notes

## ????

### Affected products
- Ecosystem: self-hosted

### Severity
- Scoring method: CVSS v3.1

### Weaknesses
- CWE: CWE-918
"""

CVE_REPORT = """## Vulnerability type
"""


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix='/api/v1')
    return app


async def seed_vulnerability_fixture(session_factory: async_sessionmaker):
    async with session_factory() as db:
        user = User(
            id='user-1',
            email='owner@example.com',
            hashed_password='not-a-real-hash',
            full_name='Owner',
            is_active=True,
        )
        other_user = User(
            id='user-2',
            email='other@example.com',
            hashed_password='not-a-real-hash',
            full_name='Other',
            is_active=True,
        )
        project = Project(
            id='project-1',
            name='Demo Project',
            owner_id='user-1',
            source_type='repository',
            repository_url='https://example.com/demo.git',
        )
        other_project = Project(
            id='project-2',
            name='Hidden Project',
            owner_id='user-2',
            source_type='repository',
            repository_url='https://example.com/hidden.git',
        )
        task = AgentTask(
            id='task-1',
            project_id='project-1',
            created_by='user-1',
            name='Audit demo',
            version_label='v1.2.3',
            branch_name='release/v1',
            commit_sha='abc123def456',
            repository_url_snapshot='https://example.com/demo.git',
            status='completed',
        )
        other_task = AgentTask(
            id='task-2',
            project_id='project-2',
            created_by='user-2',
            name='Other audit',
            version_label='hidden-v1',
            branch_name='main',
            commit_sha='fff111',
            repository_url_snapshot='https://example.com/hidden.git',
            status='completed',
        )
        managed = ManagedVulnerability(
            id='managed-1',
            project_id='project-1',
            task_id='task-1',
            finding_id='finding-1',
            project_name='Demo Project',
            version_label='v1.2.3',
            version_tag='release-v1',
            branch_name='release/v1',
            commit_sha='abc123def456',
            repository_url_snapshot='https://example.com/demo.git',
            vulnerability_name='SSRF in fetch endpoint',
            vulnerability_type='ssrf',
            severity='high',
            file_path='app/api.py',
            line_start=21,
            line_end=22,
            human_review_result='pending',
            cve_request_status='not_requested',
            report_generation_status='completed',
            source_metadata={'raw_finding': {'impact': 'Internal service exposure'}},
        )
        managed.reports = [
            ManagedVulnerabilityReport(
                id='report-en',
                report_kind='en',
                markdown_content=EN_REPORT,
                generation_status='completed',
                source_type='auto_generated',
                template_key='system:vulnerability-report:en',
                template_version='1',
                template_snapshot='# <Title>',
            ),
            ManagedVulnerabilityReport(
                id='report-zh',
                report_kind='zh',
                markdown_content=ZH_REPORT,
                generation_status='completed',
                source_type='auto_generated',
                template_key='system:vulnerability-report:zh',
                template_version='1',
                template_snapshot='# <Title>',
            ),
            ManagedVulnerabilityReport(
                id='report-cve',
                report_kind='cve',
                markdown_content=CVE_REPORT,
                generation_status='completed',
                source_type='auto_generated',
                template_key='system:vulnerability-report:cve',
                template_version='1',
                template_snapshot='## Vulnerability type',
            ),
        ]
        hidden_managed = ManagedVulnerability(
            id='managed-2',
            project_id='project-2',
            task_id='task-2',
            finding_id='finding-2',
            project_name='Hidden Project',
            version_label='hidden-v1',
            branch_name='main',
            commit_sha='fff111',
            repository_url_snapshot='https://example.com/hidden.git',
            vulnerability_name='Hidden issue',
            vulnerability_type='idor',
            severity='medium',
            human_review_result='pending',
            cve_request_status='not_requested',
            report_generation_status='pending',
        )
        db.add_all([user, other_user, project, other_project, task, other_task, managed, hidden_managed])
        await db.commit()


@pytest.mark.asyncio
async def test_vulnerability_list_filters_by_version_and_name():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_vulnerability_fixture(session_factory)
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
        response = await client.get(
            '/api/v1/vulnerabilities',
            params={'version_label': 'v1.2.3', 'vulnerability_name': 'fetch'},
        )

    await engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]['id'] == 'managed-1'
    assert payload[0]['project_name'] == 'Demo Project'
    assert payload[0]['version_label'] == 'v1.2.3'
    assert payload[0]['report_generation_status'] == 'completed'


@pytest.mark.asyncio
async def test_vulnerability_detail_and_report_endpoints():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_vulnerability_fixture(session_factory)
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
        detail = await client.get('/api/v1/vulnerabilities/managed-1')
        reports = await client.get('/api/v1/vulnerabilities/managed-1/reports')
        report = await client.get('/api/v1/vulnerabilities/managed-1/reports/zh')
        export = await client.get('/api/v1/vulnerabilities/managed-1/reports/zh/export')

    await engine.dispose()

    assert detail.status_code == 200
    assert detail.json()['id'] == 'managed-1'
    assert len(detail.json()['reports']) == 3
    assert reports.status_code == 200
    assert len(reports.json()) == 3
    assert report.status_code == 200
    assert report.json()['report_kind'] == 'zh'
    assert '## ????' in report.json()['markdown_content']
    assert export.status_code == 200
    assert export.headers['content-type'].startswith('text/markdown')
    assert '## ????' in export.text


@pytest.mark.asyncio
async def test_vulnerability_update_report_edit_and_delete():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_vulnerability_fixture(session_factory)
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
        update = await client.patch(
            '/api/v1/vulnerabilities/managed-1',
            json={
                'human_review_result': 'false_positive',
                'cve_request_status': 'failed',
                'cve_failure_reason': 'Needs more evidence',
                'cve_id': 'CVE-2099-0001',
            },
        )
        report_update = await client.patch(
            '/api/v1/vulnerabilities/managed-1/reports/zh',
            json={'markdown_content': '# Updated report', 'source_type': 'manual_edit'},
        )
        delete_response = await client.delete('/api/v1/vulnerabilities/managed-1')

    async with session_factory() as db:
        managed = await db.get(ManagedVulnerability, 'managed-1')
        hidden = await db.get(ManagedVulnerability, 'managed-2')
        report_row = await db.get(ManagedVulnerabilityReport, 'report-zh')

    await engine.dispose()

    assert update.status_code == 200
    assert update.json()['human_review_result'] == 'false_positive'
    assert update.json()['cve_request_status'] == 'failed'
    assert update.json()['cve_failure_reason'] == 'Needs more evidence'
    assert update.json()['cve_id'] == 'CVE-2099-0001'
    assert report_update.status_code == 200
    assert report_update.json()['markdown_content'] == '# Updated report'
    assert report_update.json()['source_type'] == 'manual_edit'
    assert delete_response.status_code == 204
    assert managed is None
    assert report_row is None
    assert hidden is not None


@pytest.mark.asyncio
async def test_vulnerability_endpoints_enforce_project_ownership():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await seed_vulnerability_fixture(session_factory)
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
        response = await client.get('/api/v1/vulnerabilities/managed-2')

    await engine.dispose()

    assert response.status_code == 404
