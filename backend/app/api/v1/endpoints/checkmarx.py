from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.db.session import get_db
from app.models.checkmarx_scan import CheckmarxScanJob, CheckmarxScanResult
from app.models.user import User
from app.services.checkmarx_scan.export import build_results_workbook
from app.services.checkmarx_scan.runner import checkmarx_upload_root, run_checkmarx_scan_job

router = APIRouter()


class CheckmarxScanJobResponse(BaseModel):
    id: str
    status: str
    current_step: str | None
    progress: int
    project_name: str
    source_filename: str
    checkmarx_base_url: str | None
    checkmarx_project_id: str | None
    scan_id: str | None
    totals_json: str | None
    error_message: str | None
    created_at: Any
    started_at: Any
    completed_at: Any
    results_count: int = 0


class CheckmarxScanResultResponse(BaseModel):
    id: str
    scan_id: str
    path_id: str
    vulnerability: str
    type: str
    severity: int | None
    url: str
    ai_judgement: bool | None
    ai_reason: str | None


def ensure_checkmarx_enabled() -> None:
    if not settings.CHECKMARX_FEATURE_ENABLED:
        raise HTTPException(status_code=404, detail="Checkmarx scan feature is disabled")


def _job_response(job: CheckmarxScanJob, results_count: int = 0) -> CheckmarxScanJobResponse:
    return CheckmarxScanJobResponse(
        id=job.id,
        status=job.status,
        current_step=job.current_step,
        progress=job.progress,
        project_name=job.project_name,
        source_filename=job.source_filename,
        checkmarx_base_url=job.checkmarx_base_url,
        checkmarx_project_id=job.checkmarx_project_id,
        scan_id=job.scan_id,
        totals_json=job.totals_json,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        results_count=results_count,
    )


def _result_response(row: CheckmarxScanResult) -> CheckmarxScanResultResponse:
    return CheckmarxScanResultResponse(
        id=row.id,
        scan_id=row.scan_id,
        path_id=row.path_id,
        vulnerability=row.vulnerability,
        type=row.type,
        severity=row.severity,
        url=row.url,
        ai_judgement=row.ai_judgement,
        ai_reason=row.ai_reason,
    )


async def _get_owned_job(job_id: str, db: AsyncSession, current_user: User) -> CheckmarxScanJob:
    job = await db.get(CheckmarxScanJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Checkmarx scan job not found")
    if job.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="No permission to access this Checkmarx scan job")
    return job


@router.post("/scans", response_model=CheckmarxScanJobResponse, dependencies=[Depends(ensure_checkmarx_enabled)])
async def create_checkmarx_scan(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    base_url: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> CheckmarxScanJobResponse:
    resolved_base_url = (base_url or settings.CHECKMARX_BASE_URL or "").strip()
    if not resolved_base_url:
        raise HTTPException(status_code=400, detail="Checkmarx base_url is required")
    if not project_name.strip():
        raise HTTPException(status_code=400, detail="Project name is required")
    if not username.strip() or not password:
        raise HTTPException(status_code=400, detail="Checkmarx username and password are required")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a ZIP source package")

    job = CheckmarxScanJob(
        created_by=current_user.id,
        status="pending",
        current_step="等待启动",
        progress=0,
        project_name=project_name.strip(),
        source_filename=Path(file.filename).name,
        checkmarx_base_url=resolved_base_url,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    upload_path = checkmarx_upload_root() / f"{job.id}.zip"
    with upload_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    background_tasks.add_task(
        run_checkmarx_scan_job,
        job_id=job.id,
        source_zip_path=str(upload_path),
        base_url=resolved_base_url,
        username=username.strip(),
        password=password,
    )
    return _job_response(job)


@router.get("/scans", response_model=list[CheckmarxScanJobResponse], dependencies=[Depends(ensure_checkmarx_enabled)])
async def list_checkmarx_scans(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
    limit: int = 50,
) -> list[CheckmarxScanJobResponse]:
    result = await db.execute(
        select(CheckmarxScanJob, func.count(CheckmarxScanResult.id).label("results_count"))
        .outerjoin(CheckmarxScanResult, CheckmarxScanResult.job_id == CheckmarxScanJob.id)
        .where(CheckmarxScanJob.created_by == current_user.id)
        .group_by(CheckmarxScanJob.id)
        .order_by(CheckmarxScanJob.created_at.desc())
        .limit(max(1, min(200, limit)))
    )
    return [_job_response(job, int(results_count or 0)) for job, results_count in result.all()]


@router.get("/scans/{job_id}", response_model=CheckmarxScanJobResponse, dependencies=[Depends(ensure_checkmarx_enabled)])
async def get_checkmarx_scan(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> CheckmarxScanJobResponse:
    job = await _get_owned_job(job_id, db, current_user)
    result = await db.execute(select(CheckmarxScanResult).where(CheckmarxScanResult.job_id == job.id))
    return _job_response(job, results_count=len(result.scalars().all()))


@router.get("/scans/{job_id}/results", response_model=list[CheckmarxScanResultResponse], dependencies=[Depends(ensure_checkmarx_enabled)])
async def list_checkmarx_scan_results(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> list[CheckmarxScanResultResponse]:
    job = await _get_owned_job(job_id, db, current_user)
    result = await db.execute(
        select(CheckmarxScanResult)
        .where(CheckmarxScanResult.job_id == job.id)
        .order_by(CheckmarxScanResult.created_at.asc())
    )
    return [_result_response(row) for row in result.scalars().all()]


@router.get("/scans/{job_id}/export.xlsx", dependencies=[Depends(ensure_checkmarx_enabled)])
async def export_checkmarx_scan_results(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Response:
    job = await _get_owned_job(job_id, db, current_user)
    result = await db.execute(
        select(CheckmarxScanResult)
        .where(CheckmarxScanResult.job_id == job.id)
        .order_by(CheckmarxScanResult.created_at.asc())
    )
    rows = [
        {
            "scan_id": row.scan_id,
            "path_id": row.path_id,
            "vulnerability": row.vulnerability,
            "type": row.type,
            "url": row.url,
            "ai_judgement": row.ai_judgement,
            "ai_reason": row.ai_reason,
        }
        for row in result.scalars().all()
    ]
    content = build_results_workbook(rows)
    filename = f"checkmarx-{job.scan_id or uuid.uuid4().hex[:8]}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
