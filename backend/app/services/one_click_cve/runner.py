from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.config import _build_test_user_config, _get_user_config_record, _merge_user_config
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.agent_task import AgentFinding, AgentTask, AgentTaskPhase, AgentTaskStatus, FindingStatus
from app.models.managed_vulnerability import ManagedVulnerability
from app.models.one_click_cve import (
    OneClickCveBatch,
    OneClickCveBatchProject,
    OneClickCveBatchStatus,
    OneClickCveProjectStatus,
)
from app.models.project import Project
from app.models.user_config import UserConfig
from app.services.agent.task_executor import request_agent_task_cancellation
from app.services.agent.task_queue import enqueue_agent_task, should_use_worker_queue
from app.services.llm.service import LLMService
from app.services.one_click_cve.discovery import GitHubCveDiscoveryService, GitHubRepositoryCandidate


POLL_INTERVAL_SECONDS = 5
ONE_CLICK_CVE_PREFLIGHT_AGENT = "finding"


class OneClickCveBatchCancelled(Exception):
    pass


def _format_exception_message(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


async def run_one_click_cve_batch(batch_id: str) -> None:
    async with AsyncSessionLocal() as db:
        batch = await db.get(OneClickCveBatch, batch_id)
        if batch is None:
            return
        try:
            if batch.status == OneClickCveBatchStatus.CANCELLED:
                await _finish_batch(db, batch, status=OneClickCveBatchStatus.CANCELLED, step="用户已取消")
                return
            await _mark_batch_preflight(db, batch)
            await _preflight_one_click_cve_llm(db, str(batch.user_id))
            await db.refresh(batch)
            if batch.status == OneClickCveBatchStatus.CANCELLED:
                await _finish_batch(db, batch, status=OneClickCveBatchStatus.CANCELLED, step="用户已取消")
                return
            await _mark_batch_running(db, batch)
            token = await _github_token_for_user(db, batch.user_id)
            if token:
                from app.services.one_click_cve.discovery import GitHubApiClient

                service = GitHubCveDiscoveryService(client=GitHubApiClient(token=token))
            else:
                service = GitHubCveDiscoveryService()

            summary = batch.summary_json if isinstance(batch.summary_json, dict) else {}
            prefer_security_advisory = bool(summary.get("prefer_security_advisory", True))
            candidates = await service.discover_candidates(
                target_count=max(batch.requested_count * 3, 8),
                excluded_full_names=set(),
                prefer_security_advisory=prefer_security_advisory,
            )
            if not candidates:
                await _finish_batch(db, batch, status=OneClickCveBatchStatus.EXHAUSTED, step="未找到符合条件的 GitHub 项目")
                return

            for candidate in candidates:
                await db.refresh(batch)
                if batch.status == OneClickCveBatchStatus.CANCELLED:
                    await _finish_batch(db, batch, status=OneClickCveBatchStatus.CANCELLED, step="用户已取消")
                    return
                if int(batch.found_count or 0) >= int(batch.requested_count):
                    break
                await _audit_candidate(db, batch, candidate)

            await db.refresh(batch)
            final_status = (
                OneClickCveBatchStatus.COMPLETED
                if int(batch.found_count or 0) >= int(batch.requested_count)
                else OneClickCveBatchStatus.EXHAUSTED
            )
            await _finish_batch(
                db,
                batch,
                status=final_status,
                step="已达到目标漏洞数量" if final_status == OneClickCveBatchStatus.COMPLETED else "候选项目已扫描完毕",
            )
        except OneClickCveBatchCancelled:
            await db.rollback()
            fresh = await db.get(OneClickCveBatch, batch_id)
            if fresh is not None:
                await _finish_batch(db, fresh, status=OneClickCveBatchStatus.CANCELLED, step="用户已取消")
        except Exception as exc:
            await db.rollback()
            fresh = await db.get(OneClickCveBatch, batch_id)
            if fresh is not None:
                fresh.status = OneClickCveBatchStatus.FAILED
                fresh.error_message = _format_exception_message(exc)
                fresh.completed_at = datetime.now(timezone.utc)
                fresh.current_step = "一键CVE执行失败"
                await db.commit()


async def _mark_batch_preflight(db: AsyncSession, batch: OneClickCveBatch) -> None:
    batch.status = OneClickCveBatchStatus.RUNNING
    batch.started_at = batch.started_at or datetime.now(timezone.utc)
    batch.current_step = "正在测试模型连通性"
    await db.commit()
    await db.refresh(batch)


async def _preflight_one_click_cve_llm(db: AsyncSession, user_id: str) -> None:
    record = await _get_user_config_record(db, user_id)
    merged = _merge_user_config(record)
    test_user_config = _build_test_user_config(merged, ONE_CLICK_CVE_PREFLIGHT_AGENT)
    llm_service = LLMService(user_config=test_user_config)

    await llm_service.chat_completion(
        messages=[
            {"role": "system", "content": "你是模型连通性测试助手，请简短回复。"},
            {"role": "user", "content": "请只回复：一键 CVE 模型连接成功。"},
        ],
        max_tokens=32,
        agent_type=ONE_CLICK_CVE_PREFLIGHT_AGENT,
    )


async def _raise_if_batch_cancelled(db: AsyncSession, batch_id: str) -> None:
    batch = await db.get(OneClickCveBatch, batch_id, populate_existing=True)
    if batch is not None and batch.status == OneClickCveBatchStatus.CANCELLED:
        raise OneClickCveBatchCancelled()


async def _cancel_agent_task_for_batch_cancellation(db: AsyncSession, task_id: str) -> None:
    request_agent_task_cancellation(str(task_id))
    task = await db.get(AgentTask, task_id, populate_existing=True)
    if task is None:
        return
    task.status = AgentTaskStatus.CANCELLED
    task.completed_at = datetime.now(timezone.utc)
    task.error_message = task.error_message or "Cancelled by one-click CVE batch cancellation"
    await db.commit()


async def _mark_batch_running(db: AsyncSession, batch: OneClickCveBatch) -> None:
    batch.status = OneClickCveBatchStatus.RUNNING
    batch.started_at = batch.started_at or datetime.now(timezone.utc)
    batch.current_step = "正在从 GitHub 搜索候选项目"
    await db.commit()
    await db.refresh(batch)


async def _finish_batch(db: AsyncSession, batch: OneClickCveBatch, *, status: str, step: str) -> None:
    await _refresh_batch_summary(db, batch)
    batch.status = status
    batch.current_step = step
    batch.completed_at = datetime.now(timezone.utc)
    await db.commit()


async def _audit_candidate(db: AsyncSession, batch: OneClickCveBatch, candidate: GitHubRepositoryCandidate) -> None:
    batch_id = str(batch.id)
    user_id = str(batch.user_id)
    item_id: str | None = None
    item = OneClickCveBatchProject(
        batch_id=batch_id,
        github_full_name=candidate.full_name,
        repository_url=candidate.repository_url,
        description=candidate.description,
        language=candidate.language,
        stars=candidate.stars,
        pushed_at=candidate.pushed_at,
        updated_at=candidate.updated_at,
        default_branch=candidate.default_branch,
        version_label=candidate.version_label,
        version_source=candidate.version_source,
        has_security_advisory=candidate.has_security_advisory,
        advisory_count=candidate.advisory_count,
        has_security_policy=candidate.has_security_policy,
        has_private_vulnerability_reporting=candidate.has_private_vulnerability_reporting,
        score=candidate.score,
        status=OneClickCveProjectStatus.IMPORTING,
        metadata_json={
            "version_label": candidate.version_label,
            "version_source": candidate.version_source,
            "has_private_vulnerability_reporting": candidate.has_private_vulnerability_reporting,
        },
    )
    db.add(item)
    if await _has_existing_managed_version(db, user_id, candidate.repository_url, candidate.version_label):
        item.status = OneClickCveProjectStatus.SKIPPED
        item.error_message = "已存在相同项目链接和版本的漏洞管理记录，跳过审计"
        batch.current_step = f"跳过 {candidate.full_name} {candidate.version_label}"
        await db.commit()
        await _refresh_batch_summary(db, batch)
        await db.commit()
        return

    batch.current_step = f"正在导入 {candidate.full_name}"
    await db.commit()
    await db.refresh(item)
    item_id = str(item.id)

    try:
        project = await _get_or_create_project(db, user_id, candidate)
        await _raise_if_batch_cancelled(db, batch_id)
        item.project_id = project.id
        item.status = OneClickCveProjectStatus.AUDITING
        batch.current_step = f"正在审计 {candidate.full_name}"
        await db.commit()

        task = await _create_agent_task(db, project=project, user_id=user_id, candidate=candidate, batch_id=batch_id)
        item.agent_task_id = task.id
        await db.commit()

        if should_use_worker_queue():
            await enqueue_agent_task(task.id)
            await _wait_for_task_completion(db, task.id, batch_id=batch_id)
        else:
            from app.services.agent.task_executor import execute_agent_task

            run_task = asyncio.create_task(execute_agent_task(task.id))
            await _wait_for_task_completion(db, task.id, run_task, batch_id=batch_id)
        findings_count = await _count_task_findings(db, task.id)
        item.findings_count = findings_count
        item.status = OneClickCveProjectStatus.COMPLETED
        batch.found_count = await _count_batch_findings(db, batch_id)
        await _refresh_batch_summary(db, batch)
        await db.commit()
    except OneClickCveBatchCancelled:
        await db.rollback()
        item_ref = await db.get(OneClickCveBatchProject, item_id) if item_id else None
        batch_ref = await db.get(OneClickCveBatch, batch_id)
        if item_ref is not None:
            item_ref.status = OneClickCveProjectStatus.CANCELLED
            item_ref.error_message = "Cancelled by one-click CVE batch cancellation"
        if batch_ref is not None:
            batch_ref.current_step = "用户已取消"
        await db.commit()
        raise
    except Exception as exc:
        await db.rollback()
        item_ref = await db.get(OneClickCveBatchProject, item_id) if item_id else None
        batch_ref = await db.get(OneClickCveBatch, batch_id)
        if item_ref is not None:
            item_ref.status = OneClickCveProjectStatus.FAILED
            item_ref.error_message = _format_exception_message(exc)
        if batch_ref is not None:
            batch_ref.current_step = f"{candidate.full_name} 审计失败，继续下一个候选项目"
        await db.commit()


async def _get_or_create_project(
    db: AsyncSession,
    user_id: str,
    candidate: GitHubRepositoryCandidate,
) -> Project:
    existing_result = await db.execute(
        select(Project).where(
            Project.owner_id == user_id,
            Project.source_type == "repository",
            Project.repository_url == candidate.repository_url,
            Project.is_active == True,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        return existing

    project = Project(
        id=str(uuid4()),
        name=candidate.full_name,
        description=candidate.description,
        source_type="repository",
        repository_url=candidate.repository_url,
        repository_type="github",
        default_branch=candidate.default_branch or "main",
        programming_languages=json.dumps([candidate.language] if candidate.language else []),
        owner_id=user_id,
    )
    db.add(project)
    await db.flush()

    from app.api.v1.endpoints.projects import _prepare_project_workspace

    try:
        await _prepare_project_workspace(project=project, db=db, user_id=user_id, refresh=True)
    except Exception:
        await db.rollback()
        raise
    await db.commit()
    await db.refresh(project)
    return project


async def _create_agent_task(
    db: AsyncSession,
    *,
    project: Project,
    user_id: str,
    candidate: GitHubRepositoryCandidate,
    batch_id: str,
) -> AgentTask:
    task = AgentTask(
        id=str(uuid4()),
        project_id=project.id,
        name=f"一键CVE - {candidate.full_name}",
        description="Automatically launched by one-click CVE discovery.",
        status=AgentTaskStatus.PENDING,
        current_phase=AgentTaskPhase.PLANNING,
        version_label=candidate.version_label,
        version_tag=candidate.version_label if candidate.version_source in {"latest_release", "latest_tag"} else None,
        branch_name=candidate.default_branch or project.default_branch or "main",
        repository_url_snapshot=project.repository_url,
        verification_level="sandbox",
        exclude_patterns=["node_modules", "__pycache__", ".git", "*.min.js", "dist", "build", "vendor"],
        max_iterations=settings.AGENT_MAX_ITERATIONS,
        timeout_seconds=settings.AGENT_TIMEOUT_SECONDS,
        agent_config={
            "finding_runtime_stack": getattr(settings, "FINDING_RUNTIME_STACK_DEFAULT", "runtime"),
            "one_click_cve_batch_id": batch_id,
        },
        audit_scope={
            "one_click_cve": {
                "batch_id": batch_id,
                "github_full_name": candidate.full_name,
                "version_label": candidate.version_label,
                "version_source": candidate.version_source,
                "has_security_advisory": candidate.has_security_advisory,
                "advisory_count": candidate.advisory_count,
                "has_private_vulnerability_reporting": candidate.has_private_vulnerability_reporting,
            }
        },
        created_by=user_id,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


async def _wait_for_task_completion(
    db: AsyncSession,
    task_id: str,
    run_task: asyncio.Task | None = None,
    *,
    batch_id: str | None = None,
) -> None:
    while run_task is None or not run_task.done():
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        await db.commit()
        if batch_id is not None:
            try:
                await _raise_if_batch_cancelled(db, batch_id)
            except OneClickCveBatchCancelled:
                await _cancel_agent_task_for_batch_cancellation(db, task_id)
                if run_task is not None and not run_task.done():
                    run_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await run_task
                raise
        task = await db.get(AgentTask, task_id, populate_existing=True)
        if task is None:
            raise RuntimeError("Agent task disappeared during one-click CVE run")
        if task.status in {AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED, AgentTaskStatus.CANCELLED}:
            break
    if run_task is not None:
        await run_task
    await db.commit()
    task = await db.get(AgentTask, task_id, populate_existing=True)
    if task is None:
        raise RuntimeError("Agent task disappeared during one-click CVE run")
    if task.status == AgentTaskStatus.FAILED:
        raise RuntimeError(task.error_message or "Agent task failed")


async def _count_task_findings(db: AsyncSession, task_id: str) -> int:
    result = await db.execute(
        select(func.count(AgentFinding.id)).where(
            AgentFinding.task_id == task_id,
            AgentFinding.status != FindingStatus.FALSE_POSITIVE,
        )
    )
    return int(result.scalar_one() or 0)


async def _count_batch_findings(db: AsyncSession, batch_id: str) -> int:
    result = await db.execute(
        select(func.count(AgentFinding.id))
        .join(AgentTask, AgentTask.id == AgentFinding.task_id)
        .join(OneClickCveBatchProject, OneClickCveBatchProject.agent_task_id == AgentTask.id)
        .where(
            OneClickCveBatchProject.batch_id == batch_id,
            AgentFinding.status != FindingStatus.FALSE_POSITIVE,
        )
    )
    return int(result.scalar_one() or 0)


async def _refresh_batch_summary(db: AsyncSession, batch: OneClickCveBatch) -> None:
    result = await db.execute(
        select(OneClickCveBatchProject)
        .where(OneClickCveBatchProject.batch_id == batch.id)
        .order_by(OneClickCveBatchProject.created_at.asc())
    )
    projects = list(result.scalars().all())
    batch.found_count = sum(int(project.findings_count or 0) for project in projects)
    batch.summary_json = {
        "requested_count": batch.requested_count,
        "found_count": batch.found_count,
        "projects_scanned": len([project for project in projects if project.status == OneClickCveProjectStatus.COMPLETED]),
        "projects_skipped": len([project for project in projects if project.status == OneClickCveProjectStatus.SKIPPED]),
        "projects_failed": len([project for project in projects if project.status == OneClickCveProjectStatus.FAILED]),
    }


async def _has_existing_managed_version(db: AsyncSession, user_id: str, repository_url: str, version_label: str) -> bool:
    variants = _repository_url_variants(repository_url)
    if not variants or not version_label:
        return False
    result = await db.execute(
        select(func.count(ManagedVulnerability.id))
        .join(Project, Project.id == ManagedVulnerability.project_id)
        .where(
            Project.owner_id == user_id,
            ManagedVulnerability.version_label == version_label,
            func.lower(ManagedVulnerability.repository_url_snapshot).in_(variants),
        )
    )
    return int(result.scalar_one() or 0) > 0


def _repository_url_variants(repository_url: str) -> set[str]:
    normalized = str(repository_url or "").strip().rstrip("/")
    if not normalized:
        return set()
    without_git = normalized[:-4] if normalized.lower().endswith(".git") else normalized
    values = {
        normalized,
        without_git,
        f"{without_git}.git",
    }
    return {value.lower() for value in values if value}


async def _github_token_for_user(db: AsyncSession, user_id: str) -> str | None:
    from app.core.encryption import decrypt_sensitive_data

    result = await db.execute(select(UserConfig).where(UserConfig.user_id == user_id))
    config = result.scalar_one_or_none()
    if not config or not config.other_config:
        return settings.GITHUB_TOKEN
    import json

    other_config = json.loads(config.other_config)
    encrypted = other_config.get("githubToken")
    if not encrypted:
        return settings.GITHUB_TOKEN
    try:
        return decrypt_sensitive_data(encrypted)
    except Exception:
        return settings.GITHUB_TOKEN


async def _load_recent_user_repositories(db: AsyncSession, user_id: str) -> set[str]:
    result = await db.execute(
        select(OneClickCveBatchProject.github_full_name)
        .join(OneClickCveBatch, OneClickCveBatch.id == OneClickCveBatchProject.batch_id)
        .where(OneClickCveBatch.user_id == user_id)
        .order_by(OneClickCveBatchProject.created_at.desc())
        .limit(200)
    )
    return {str(row[0]).lower() for row in result.fetchall() if row[0]}
