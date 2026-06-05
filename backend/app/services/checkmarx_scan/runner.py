from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.checkmarx_scan import CheckmarxScanJob, CheckmarxScanResult
from app.services.checkmarx_scan.client import CheckmarxClient, CheckmarxClientConfig
from app.services.checkmarx_scan.workflow import build_workflow_payload, request_workflow_verdict


def build_client_config(*, base_url: str, username: str, password: str) -> CheckmarxClientConfig:
    return CheckmarxClientConfig(
        base_url=base_url,
        username=username,
        password=password,
        client_id=settings.CHECKMARX_CLIENT_ID,
        client_secret=settings.CHECKMARX_CLIENT_SECRET,
        scope=settings.CHECKMARX_SCOPE,
        timeout_connect=settings.CHECKMARX_TIMEOUT_CONNECT,
        timeout_read=settings.CHECKMARX_TIMEOUT_READ,
        upload_timeout_read=settings.CHECKMARX_SCAN_UPLOAD_READ_TIMEOUT,
        scan_timeout_seconds=settings.CHECKMARX_SCAN_TIMEOUT,
        poll_interval_seconds=settings.CHECKMARX_SCAN_POLL_INTERVAL,
        preset_id=settings.CHECKMARX_PRESET_ID,
        force_scan=settings.CHECKMARX_FORCE_SCAN,
        is_incremental=settings.CHECKMARX_IS_INCREMENTAL,
        sast_accept_api_version=settings.CHECKMARX_SAST_ACCEPT_API_VERSION,
        help_accept_api_version=settings.CHECKMARX_HELP_SAST_ACCEPT_API_VERSION,
        help_results_delay_seconds=settings.CHECKMARX_HELP_RESULTS_DELAY,
        help_results_429_max_retries=settings.CHECKMARX_HELP_RESULTS_429_RETRIES,
        help_results_429_base_wait_seconds=settings.CHECKMARX_HELP_RESULTS_429_BASE_WAIT,
    )


async def _update_job(
    db: AsyncSession,
    job: CheckmarxScanJob,
    *,
    status: str | None = None,
    step: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    started: bool = False,
    completed: bool = False,
) -> None:
    if status is not None:
        job.status = status
    if step is not None:
        job.current_step = step
    if progress is not None:
        job.progress = max(0, min(100, int(progress)))
    if error is not None:
        job.error_message = error
    now = datetime.now(timezone.utc)
    if started:
        job.started_at = now
    if completed:
        job.completed_at = now
    await db.commit()
    await db.refresh(job)


async def run_checkmarx_scan_job(
    *,
    job_id: str,
    source_zip_path: str,
    base_url: str,
    username: str,
    password: str,
) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(CheckmarxScanJob, job_id)
        if job is None:
            return

        try:
            await _update_job(db, job, status="running", step="正在认证 Checkmarx", progress=8, started=True)
            client = CheckmarxClient(build_client_config(base_url=base_url, username=username, password=password))
            token = await client.authenticate()

            await _update_job(db, job, step="正在解析 Checkmarx 项目", progress=18)
            project_id = await client.get_project_id(token, job.project_name)
            job.checkmarx_project_id = project_id
            await db.commit()
            await db.refresh(job)

            await _update_job(db, job, step="正在上传 ZIP 并启动扫描", progress=32)
            scan_id = await client.submit_scan(token, project_id, source_zip_path)
            job.scan_id = scan_id
            await db.commit()
            await db.refresh(job)

            await _update_job(db, job, step="正在等待 Checkmarx 扫描完成", progress=48)
            totals, rows = await client.wait_for_results(token, scan_id)
            job.totals_json = json.dumps(totals, ensure_ascii=False)
            await db.commit()
            await db.refresh(job)

            await _update_job(db, job, step="正在调用 AI 降误报工作流", progress=68)
            await _store_results_with_workflow(db, job, rows)

            await _update_job(db, job, status="completed", step="扫描完成", progress=100, completed=True)
        except Exception as exc:
            await db.rollback()
            await db.refresh(job)
            await _update_job(
                db,
                job,
                status="failed",
                step="扫描失败",
                progress=job.progress or 0,
                error=str(exc),
                completed=True,
            )


async def _store_results_with_workflow(
    db: AsyncSession,
    job: CheckmarxScanJob,
    rows: list[dict[str, Any]],
) -> None:
    workflow_enabled = settings.CHECKMARX_WORKFLOW_ENABLED
    workflow_url = (settings.WORKFLOW_URL or "").strip()
    workflow_token = (settings.WORKFLOW_API_TOKEN or "").strip()
    if workflow_enabled and rows and (not workflow_url or not workflow_token):
        raise RuntimeError("WORKFLOW_URL and WORKFLOW_API_TOKEN must be configured when Checkmarx workflow is enabled")

    total = max(1, len(rows))
    for index, row in enumerate(rows, start=1):
        ai_judgement: bool | None = None
        ai_reason: str | None = None
        workflow_response: Any = None

        if workflow_enabled:
            payload = build_workflow_payload(
                str(row["scan_id"]),
                str(row["path_id"]),
                str(row["vulnerability"]),
                settings.WORKFLOW_USER,
            )
            verdict, workflow_response = await request_workflow_verdict(
                url=workflow_url,
                token=workflow_token,
                payload=payload,
                timeout_seconds=settings.WORKFLOW_TIMEOUT,
            )
            ai_judgement = verdict.real_vuln
            ai_reason = verdict.reason

        db.add(
            CheckmarxScanResult(
                job_id=job.id,
                scan_id=str(row["scan_id"]),
                path_id=str(row["path_id"]),
                vulnerability=str(row["vulnerability"]),
                type=str(row["type"]),
                severity=row.get("severity"),
                url=str(row["url"]),
                ai_judgement=ai_judgement,
                ai_reason=ai_reason,
                raw_result=json.dumps(row.get("raw") or {}, ensure_ascii=False),
                workflow_response=json.dumps(workflow_response, ensure_ascii=False) if workflow_response is not None else None,
            )
        )

        job.progress = 68 + int(27 * index / total)
        job.current_step = f"正在调用 AI 工作流 ({index}/{len(rows)})"
        await db.commit()

    if not rows:
        await db.commit()


def checkmarx_upload_root() -> Path:
    root = Path(settings.CHECKMARX_UPLOAD_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root
