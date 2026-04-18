from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from datetime import datetime, timezone
from pathlib import Path
import shutil
import os
import uuid
import json

from app.api import deps
from app.core.config import settings
from app.db.session import get_db, AsyncSessionLocal
from app.models.project import Project
from app.models.user import User
from app.models.audit import AuditTask, AuditIssue
from app.models.agent_task import AgentTask, AgentTaskStatus, AgentFinding
from app.models.user_config import UserConfig
import zipfile
from app.services.scanner import (
    scan_repo_task,
    get_github_files,
    get_gitlab_files,
    get_gitea_files,
    get_github_branches,
    get_gitlab_branches,
    get_gitea_branches,
    fetch_file_content,
    should_exclude,
    is_text_file,
)
from app.services.zip_storage import (
    save_project_zip, load_project_zip, get_project_zip_meta,
    delete_project_zip, has_project_zip
)

router = APIRouter()

# Schemas
class ProjectCreate(BaseModel):
    name: str
    source_type: Optional[str] = "repository"  # 'repository' 或 'zip'
    repository_url: Optional[str] = None
    repository_type: Optional[str] = "other"  # github, gitlab, other
    local_path: Optional[str] = None
    workspace_mode: Optional[str] = None
    description: Optional[str] = None
    default_branch: Optional[str] = "main"
    programming_languages: Optional[List[str]] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    source_type: Optional[str] = None
    repository_url: Optional[str] = None
    repository_type: Optional[str] = None
    local_path: Optional[str] = None
    workspace_mode: Optional[str] = None
    description: Optional[str] = None
    default_branch: Optional[str] = None
    programming_languages: Optional[List[str]] = None

class OwnerSchema(BaseModel):
    id: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None

    class Config:
        from_attributes = True

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    source_type: Optional[str] = "repository"  # 'repository' 或 'zip'
    repository_url: Optional[str] = None
    repository_type: Optional[str] = None  # github, gitlab, other
    local_path: Optional[str] = None
    workspace_mode: Optional[str] = None
    default_branch: Optional[str] = None
    programming_languages: Optional[str] = None
    owner_id: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    owner: Optional[OwnerSchema] = None

    class Config:
        from_attributes = True

class StatsResponse(BaseModel):
    total_projects: int
    active_projects: int
    total_tasks: int
    completed_tasks: int
    total_issues: int
    resolved_issues: int
    avg_quality_score: float = 0.0


class ManagedLocalDirectoryResponse(BaseModel):
    name: str
    path: str


class ProjectFileContentResponse(BaseModel):
    path: str
    content: str
    size: int
    truncated: bool = False


def _get_managed_projects_root() -> Path:
    return Path(settings.MANAGED_PROJECTS_ROOT).resolve()


def _normalize_managed_local_path(local_path: str) -> str:
    managed_root = _get_managed_projects_root()
    candidate = Path(local_path).resolve()

    try:
        candidate.relative_to(managed_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="local_path must stay within the managed projects directory",
        ) from exc

    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail="local_path does not exist or is not a directory")

    return str(candidate)


def _ensure_project_relative_path(relative_path: str) -> str:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise HTTPException(status_code=400, detail="path must stay inside the project root")
    normalized = candidate.as_posix().lstrip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="path is required")
    return normalized


def _build_file_content_response(*, relative_path: str, content: str) -> ProjectFileContentResponse:
    encoded = content.encode("utf-8", errors="ignore")
    size = len(encoded)
    max_size = settings.MAX_FILE_SIZE_BYTES
    truncated = size > max_size
    if truncated:
        content = encoded[:max_size].decode("utf-8", errors="ignore")
    return ProjectFileContentResponse(
        path=relative_path,
        content=content,
        size=size,
        truncated=truncated,
    )


@router.get("/managed-local-directories", response_model=List[ManagedLocalDirectoryResponse])
async def list_managed_local_directories(
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    List first-level managed project directories available for local import.
    """
    managed_root = _get_managed_projects_root()
    if not managed_root.exists():
        return []

    directories = [
        ManagedLocalDirectoryResponse(name=entry.name, path=str(entry.resolve()))
        for entry in sorted(managed_root.iterdir(), key=lambda item: item.name.lower())
        if entry.is_dir()
    ]
    return directories

@router.post("/", response_model=ProjectResponse)
async def create_project(
    *,
    db: AsyncSession = Depends(get_db),
    project_in: ProjectCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Create new project.
    """
    import json
    # 根据 source_type 设置默认值
    source_type = project_in.source_type or "repository"
    normalized_local_path: Optional[str] = None

    if source_type == "local_directory":
        if not project_in.local_path:
            raise HTTPException(status_code=422, detail="local_path is required for local_directory projects")
        normalized_local_path = _normalize_managed_local_path(project_in.local_path)
        existing_result = await db.execute(
            select(Project).where(
                Project.owner_id == current_user.id,
                Project.source_type == "local_directory",
                Project.local_path == normalized_local_path,
                Project.is_active == True,
            )
        )
        if existing_result.scalars().first():
            raise HTTPException(status_code=400, detail="local directory is already registered")
    
    project = Project(
        name=project_in.name,
        source_type=source_type,
        repository_url=project_in.repository_url if source_type == "repository" else None,
        repository_type=project_in.repository_type or "other" if source_type == "repository" else "other",
        local_path=normalized_local_path if source_type == "local_directory" else None,
        workspace_mode=project_in.workspace_mode or ("in_place" if source_type == "local_directory" else None),
        description=project_in.description,
        default_branch=project_in.default_branch or "main",
        programming_languages=json.dumps(project_in.programming_languages or []),
        owner_id=current_user.id
    )
    db.add(project)
    await db.commit()
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.owner))
        .where(Project.id == project.id)
    )
    return result.scalars().first()

@router.get("/", response_model=List[ProjectResponse])
async def read_projects(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    include_deleted: bool = False,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Retrieve projects for current user.
    """
    query = select(Project).options(selectinload(Project.owner))
    # 只返回当前用户的项目
    query = query.where(Project.owner_id == current_user.id)
    if not include_deleted:
        query = query.where(Project.is_active == True)
    query = query.order_by(Project.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()

@router.get("/deleted", response_model=List[ProjectResponse])
async def read_deleted_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Retrieve deleted (soft-deleted) projects for current user.
    """
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.owner))
        .where(Project.owner_id == current_user.id)
        .where(Project.is_active == False)
        .order_by(Project.updated_at.desc())
    )
    return result.scalars().all()

@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get statistics for current user.
    """
    # 只统计当前用户的项目
    projects_result = await db.execute(
        select(Project).where(Project.owner_id == current_user.id)
    )
    projects = projects_result.scalars().all()
    project_ids = [p.id for p in projects]

    # 统计旧的 AuditTask
    tasks_result = await db.execute(
        select(AuditTask).where(AuditTask.project_id.in_(project_ids)) if project_ids else select(AuditTask).where(False)
    )
    tasks = tasks_result.scalars().all()
    task_ids = [t.id for t in tasks]

    # 统计旧的 AuditIssue
    issues_result = await db.execute(
        select(AuditIssue).where(AuditIssue.task_id.in_(task_ids)) if task_ids else select(AuditIssue).where(False)
    )
    issues = issues_result.scalars().all()

    # 🔥 同时统计新的 AgentTask
    agent_tasks_result = await db.execute(
        select(AgentTask).where(AgentTask.project_id.in_(project_ids)) if project_ids else select(AgentTask).where(False)
    )
    agent_tasks = agent_tasks_result.scalars().all()
    agent_task_ids = [t.id for t in agent_tasks]

    # 🔥 统计 AgentFinding
    agent_findings_result = await db.execute(
        select(AgentFinding).where(AgentFinding.task_id.in_(agent_task_ids)) if agent_task_ids else select(AgentFinding).where(False)
    )
    agent_findings = agent_findings_result.scalars().all()

    # 合并统计（旧任务 + 新 Agent 任务）
    total_tasks = len(tasks) + len(agent_tasks)
    completed_tasks = (
        len([t for t in tasks if t.status == "completed"]) +
        len([t for t in agent_tasks if t.status == AgentTaskStatus.COMPLETED])
    )
    total_issues = len(issues) + len(agent_findings)
    resolved_issues = (
        len([i for i in issues if i.status == "resolved"]) +
        len([f for f in agent_findings if f.status in ("fixed", "wont_fix", "false_positive")])
    )

    # 计算平均质量分（只统计已完成且有质量分的任务）
    quality_scores = (
        [t.quality_score for t in tasks if t.status == "completed" and t.quality_score and t.quality_score > 0] +
        [t.quality_score for t in agent_tasks if t.status == AgentTaskStatus.COMPLETED and t.quality_score and t.quality_score > 0]
    )
    avg_quality_score = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0

    return {
        "total_projects": len(projects),
        "active_projects": len([p for p in projects if p.is_active]),
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "total_issues": total_issues,
        "resolved_issues": resolved_issues,
        "avg_quality_score": avg_quality_score,
    }

@router.get("/{id}", response_model=ProjectResponse)
async def read_project(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get project by ID.
    """
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.owner))
        .where(Project.id == id)
    )
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限：只有项目所有者可以查看
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看此项目")
    
    return project

@router.put("/{id}", response_model=ProjectResponse)
async def update_project(
    id: str,
    *,
    db: AsyncSession = Depends(get_db),
    project_in: ProjectUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Update project.
    """
    import json
    result = await db.execute(select(Project).where(Project.id == id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限：只有项目所有者可以更新
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权更新此项目")
    
    update_data = project_in.model_dump(exclude_unset=True)
    target_source_type = update_data.get("source_type", project.source_type)

    if "programming_languages" in update_data and update_data["programming_languages"] is not None:
        update_data["programming_languages"] = json.dumps(update_data["programming_languages"])

    if target_source_type == "local_directory":
        local_path = update_data.get("local_path", project.local_path)
        if not local_path:
            raise HTTPException(status_code=422, detail="local_path is required for local_directory projects")

        normalized_local_path = _normalize_managed_local_path(local_path)
        existing_result = await db.execute(
            select(Project).where(
                Project.owner_id == current_user.id,
                Project.source_type == "local_directory",
                Project.local_path == normalized_local_path,
                Project.id != project.id,
                Project.is_active == True,
            )
        )
        if existing_result.scalars().first():
            raise HTTPException(status_code=400, detail="local directory is already registered")

        update_data["local_path"] = normalized_local_path
        update_data["workspace_mode"] = update_data.get("workspace_mode") or project.workspace_mode or "in_place"
    elif "source_type" in update_data and update_data["source_type"] != "local_directory":
        update_data["local_path"] = None
        update_data["workspace_mode"] = None
    
    for field, value in update_data.items():
        setattr(project, field, value)
    
    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    return project

@router.delete("/{id}")
async def delete_project(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Soft delete project.
    """
    result = await db.execute(select(Project).where(Project.id == id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限：只有项目所有者可以删除
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权删除此项目")
    
    project.is_active = False
    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "项目已删除"}

@router.post("/{id}/restore")
async def restore_project(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Restore soft-deleted project.
    """
    result = await db.execute(select(Project).where(Project.id == id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限：只有项目所有者可以恢复
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权恢复此项目")
    
    project.is_active = True
    project.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "项目已恢复"}

@router.delete("/{id}/permanent")
async def permanently_delete_project(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Permanently delete project.
    """
    result = await db.execute(select(Project).where(Project.id == id))
    project = result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限：只有项目所有者可以永久删除
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权永久删除此项目")
    
    # 如果是ZIP类型项目，删除关联的ZIP文件和元数据
    if project.source_type == "zip":
        try:
            await delete_project_zip(id)
            print(f"[Project] 已删除项目 {id} 的ZIP文件")
        except Exception as e:
            print(f"[Warning] 删除ZIP文件失败: {e}")
    
    await db.delete(project)
    await db.commit()
    return {"message": "项目已永久删除"}


@router.get("/{id}/files")
async def get_project_files(
    id: str,
    branch: Optional[str] = None,
    exclude_patterns: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get list of files in the project.
    可选参数:
    - branch: 指定仓库分支（仅对仓库类型项目有效）
    - exclude_patterns: JSON 格式的排除模式数组，如 ["node_modules/**", "*.log"]
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # Check permissions
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看此项目")
    
    # 解析排除模式
    parsed_exclude_patterns = []
    if exclude_patterns:
        try:
            parsed_exclude_patterns = json.loads(exclude_patterns)
        except json.JSONDecodeError:
            pass
    
    files = []
    
    if project.source_type == "zip":
        # Handle ZIP project
        zip_path = await load_project_zip(id)
        print(f"📦 ZIP项目 {id} 文件路径: {zip_path}")
        if not zip_path or not os.path.exists(zip_path):
            print(f"⚠️ ZIP文件不存在: {zip_path}")
            return []
            
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if not file_info.is_dir():
                        name = file_info.filename
                        # 使用统一的排除逻辑，支持用户自定义排除模式
                        if should_exclude(name, parsed_exclude_patterns):
                            continue
                        # 只显示支持的代码文件
                        if not is_text_file(name):
                            continue
                        files.append({"path": name, "size": file_info.file_size})
        except Exception as e:
            print(f"Error reading zip file: {e}")
            raise HTTPException(status_code=500, detail="无法读取项目文件")
            
    elif project.source_type == "local_directory":
        if not project.local_path:
            raise HTTPException(status_code=400, detail="local directory project is missing local_path")

        project_root = Path(project.local_path)
        if not project_root.exists() or not project_root.is_dir():
            raise HTTPException(status_code=400, detail="local project directory is unavailable")

        for file_path in project_root.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(project_root).as_posix()
            if should_exclude(relative_path, parsed_exclude_patterns):
                continue
            if not is_text_file(relative_path):
                continue

            try:
                file_size = file_path.stat().st_size
            except OSError:
                continue
            files.append({"path": relative_path, "size": file_size})

    elif project.source_type == "repository":
        # Handle Repository project
        if not project.repository_url:
            return []

        # Get tokens from user config
        from sqlalchemy.future import select
        from app.core.encryption import decrypt_sensitive_data
        from app.core.config import settings
        from app.services.git_ssh_service import GitSSHOperations

        SENSITIVE_OTHER_FIELDS = ['githubToken', 'gitlabToken', 'sshPrivateKey']

        result = await db.execute(
            select(UserConfig).where(UserConfig.user_id == current_user.id)
        )
        config = result.scalar_one_or_none()

        github_token = settings.GITHUB_TOKEN
        gitlab_token = settings.GITLAB_TOKEN
        ssh_private_key = None

        if config and config.other_config:
            other_config = json.loads(config.other_config)
            for field in SENSITIVE_OTHER_FIELDS:
                if field in other_config and other_config[field]:
                    decrypted_val = decrypt_sensitive_data(other_config[field])
                    if field == 'githubToken':
                        github_token = decrypted_val
                    elif field == 'gitlabToken':
                        gitlab_token = decrypted_val
                    elif field == 'sshPrivateKey':
                        ssh_private_key = decrypted_val

        # 检查是否为SSH URL
        is_ssh_url = GitSSHOperations.is_ssh_url(project.repository_url)
        target_branch = branch or project.default_branch or "main"

        try:
            if is_ssh_url:
                # 使用SSH方式获取文件列表
                if not ssh_private_key:
                    raise HTTPException(
                        status_code=400,
                        detail="仓库使用SSH URL，但未配置SSH密钥。请先在设置中生成SSH密钥。"
                    )

                print(f"🔐 使用SSH方式获取文件列表: {project.repository_url}")
                files_with_content = GitSSHOperations.get_repo_files_via_ssh(
                    project.repository_url,
                    ssh_private_key,
                    target_branch,
                    parsed_exclude_patterns
                )
                files = [{"path": f["path"], "size": len(f.get("content", ""))} for f in files_with_content]
            else:
                # 使用API方式获取文件列表
                repo_type = project.repository_type or "other"

                if repo_type == "github":
                    # 传入用户自定义排除模式
                    repo_files = await get_github_files(project.repository_url, target_branch, github_token, parsed_exclude_patterns)
                    files = [{"path": f["path"], "size": 0} for f in repo_files]
                elif repo_type == "gitlab":
                    # 传入用户自定义排除模式
                    repo_files = await get_gitlab_files(project.repository_url, target_branch, gitlab_token, parsed_exclude_patterns)
                    files = [{"path": f["path"], "size": 0} for f in repo_files]
                else:
                    raise HTTPException(status_code=400, detail="不支持的仓库类型")
        except HTTPException:
            raise
        except Exception as e:
             print(f"Error fetching repo files: {e}")
             raise HTTPException(status_code=500, detail=f"无法获取仓库文件: {str(e)}")

    return files


@router.get("/{id}/file-content", response_model=ProjectFileContentResponse)
async def get_project_file_content(
    id: str,
    path: str,
    branch: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Read a single text file from the selected project for workspace preview.
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看此项目")

    relative_path = _ensure_project_relative_path(path)

    if project.source_type == "local_directory":
        if not project.local_path:
            raise HTTPException(status_code=400, detail="local directory project is missing local_path")

        project_root = Path(project.local_path).resolve()
        file_path = (project_root / relative_path).resolve()
        try:
            file_path.relative_to(project_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="path must stay inside the project root") from exc

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="project file not found")
        if not is_text_file(relative_path):
            raise HTTPException(status_code=400, detail="only text files can be previewed")

        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="file is not valid UTF-8 text")

        return _build_file_content_response(relative_path=relative_path, content=content)

    if project.source_type == "zip":
        zip_path = await load_project_zip(id)
        if not zip_path or not os.path.exists(zip_path):
            raise HTTPException(status_code=404, detail="project zip not found")

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                try:
                    with zip_ref.open(relative_path, "r") as file_handle:
                        raw_content = file_handle.read()
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail="project file not found") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="无法读取项目文件") from exc

        try:
            content = raw_content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="file is not valid UTF-8 text") from exc

        return _build_file_content_response(relative_path=relative_path, content=content)

    if project.source_type == "repository":
        if not project.repository_url:
            raise HTTPException(status_code=400, detail="repository_url is required for repository projects")

        from app.core.encryption import decrypt_sensitive_data
        from app.services.git_ssh_service import GitSSHOperations

        sensitive_fields = ["githubToken", "gitlabToken", "giteaToken", "sshPrivateKey"]
        result = await db.execute(select(UserConfig).where(UserConfig.user_id == current_user.id))
        config = result.scalar_one_or_none()

        github_token = settings.GITHUB_TOKEN
        gitlab_token = settings.GITLAB_TOKEN
        gitea_token = settings.GITEA_TOKEN
        ssh_private_key = None

        if config and config.other_config:
            other_config = json.loads(config.other_config)
            for field in sensitive_fields:
                if field in other_config and other_config[field]:
                    decrypted_val = decrypt_sensitive_data(other_config[field])
                    if field == "githubToken":
                        github_token = decrypted_val
                    elif field == "gitlabToken":
                        gitlab_token = decrypted_val
                    elif field == "giteaToken":
                        gitea_token = decrypted_val
                    elif field == "sshPrivateKey":
                        ssh_private_key = decrypted_val

        target_branch = branch or project.default_branch or "main"

        if GitSSHOperations.is_ssh_url(project.repository_url):
            if not ssh_private_key:
                raise HTTPException(status_code=400, detail="repository uses SSH but ssh private key is not configured")

            files_with_content = GitSSHOperations.get_repo_files_via_ssh(
                project.repository_url,
                ssh_private_key,
                target_branch,
                [],
            )
            matched_file = next((item for item in files_with_content if item.get("path") == relative_path), None)
            if not matched_file:
                raise HTTPException(status_code=404, detail="project file not found")
            content = matched_file.get("content", "")
            return _build_file_content_response(relative_path=relative_path, content=content)

        repo_type = project.repository_type or "other"
        if repo_type == "github":
            repo_files = await get_github_files(project.repository_url, target_branch, github_token, [])
        elif repo_type == "gitlab":
            repo_files = await get_gitlab_files(project.repository_url, target_branch, gitlab_token, [])
        elif repo_type == "gitea":
            repo_files = await get_gitea_files(project.repository_url, target_branch, gitea_token, [])
        else:
            raise HTTPException(status_code=400, detail="不支持的仓库类型")

        matched_file = next((item for item in repo_files if item.get("path") == relative_path), None)
        if not matched_file:
            raise HTTPException(status_code=404, detail="project file not found")

        headers = {}
        if repo_type == "github" and github_token:
            headers["Authorization"] = f"Bearer {github_token}"
        elif repo_type == "gitlab" and matched_file.get("token"):
            headers["PRIVATE-TOKEN"] = matched_file["token"]
        elif repo_type == "gitea" and matched_file.get("token"):
            headers["Authorization"] = f"token {matched_file['token']}"

        content = await fetch_file_content(matched_file["url"], headers)
        if content is None:
            raise HTTPException(status_code=404, detail="project file not found")

        return _build_file_content_response(relative_path=relative_path, content=content)

    raise HTTPException(status_code=400, detail="unsupported project source type")

class ScanRequest(BaseModel):
    file_paths: Optional[List[str]] = None
    full_scan: bool = True
    exclude_patterns: Optional[List[str]] = None
    branch_name: Optional[str] = None


@router.post("/{id}/scan")
async def scan_project(
    id: str,
    background_tasks: BackgroundTasks,
    scan_request: Optional[ScanRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Start a scan task.
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 获取分支和排除模式
    branch_name = scan_request.branch_name if scan_request else None
    exclude_patterns = scan_request.exclude_patterns if scan_request else None

    # Create Task Record
    task = AuditTask(
        project_id=project.id,
        created_by=current_user.id,
        task_type="repository",
        status="pending",
        branch_name=branch_name or project.default_branch or "main",
        exclude_patterns=json.dumps(exclude_patterns or []),
        scan_config=json.dumps(scan_request.dict()) if scan_request else "{}"
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 获取用户配置（包含解密敏感字段）
    from app.core.encryption import decrypt_sensitive_data

    # 需要解密的敏感字段列表
    SENSITIVE_LLM_FIELDS = [
        'llmApiKey', 'geminiApiKey', 'openaiApiKey', 'claudeApiKey',
        'qwenApiKey', 'deepseekApiKey', 'zhipuApiKey', 'moonshotApiKey',
        'baiduApiKey', 'minimaxApiKey', 'doubaoApiKey'
    ]
    SENSITIVE_OTHER_FIELDS = ['githubToken', 'gitlabToken']

    def decrypt_config(config_dict: dict, sensitive_fields: list) -> dict:
        """解密配置中的敏感字段"""
        decrypted = config_dict.copy()
        for field in sensitive_fields:
            if field in decrypted and decrypted[field]:
                decrypted[field] = decrypt_sensitive_data(decrypted[field])
        return decrypted

    result = await db.execute(
        select(UserConfig).where(UserConfig.user_id == current_user.id)
    )
    config = result.scalar_one_or_none()
    user_config = {}
    if config:
        llm_config = json.loads(config.llm_config) if config.llm_config else {}
        other_config = json.loads(config.other_config) if config.other_config else {}
        # 解密敏感字段
        llm_config = decrypt_config(llm_config, SENSITIVE_LLM_FIELDS)
        other_config = decrypt_config(other_config, SENSITIVE_OTHER_FIELDS)
        user_config = {
            'llmConfig': llm_config,
            'otherConfig': other_config,
        }

    # 将扫描配置注入到 user_config 中，以便 scan_repo_task 使用
    if scan_request and scan_request.file_paths:
        user_config['scan_config'] = {'file_paths': scan_request.file_paths}

    # Trigger Background Task
    background_tasks.add_task(scan_repo_task, task.id, AsyncSessionLocal, user_config)

    return {"task_id": task.id, "status": "started"}


# ============ ZIP文件管理端点 ============

class ZipFileMetaResponse(BaseModel):
    has_file: bool
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    uploaded_at: Optional[str] = None


@router.get("/{id}/zip", response_model=ZipFileMetaResponse)
async def get_project_zip_info(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取项目ZIP文件信息
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查是否有ZIP文件
    has_file = await has_project_zip(id)
    if not has_file:
        return {"has_file": False}
    
    # 获取元数据
    meta = await get_project_zip_meta(id)
    if meta:
        return {
            "has_file": True,
            "original_filename": meta.get("original_filename"),
            "file_size": meta.get("file_size"),
            "uploaded_at": meta.get("uploaded_at")
        }
    
    return {"has_file": True}


@router.post("/{id}/zip")
async def upload_project_zip(
    id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    上传或更新项目ZIP文件
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权操作此项目")
    
    # 检查项目类型
    if project.source_type != "zip":
        raise HTTPException(status_code=400, detail="仅ZIP类型项目可以上传ZIP文件")
    
    # 验证文件类型
    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="请上传ZIP格式文件")
    
    # 保存到临时文件
    temp_file_id = str(uuid.uuid4())
    temp_file_path = f"/tmp/{temp_file_id}.zip"
    
    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # 检查文件大小
        file_size = os.path.getsize(temp_file_path)
        if file_size > 500 * 1024 * 1024:  # 500MB limit
            raise HTTPException(status_code=400, detail="文件大小不能超过500MB")
        
        # 保存到持久化存储
        meta = await save_project_zip(id, temp_file_path, file.filename)
        
        return {
            "message": "ZIP文件上传成功",
            "original_filename": meta["original_filename"],
            "file_size": meta["file_size"],
            "uploaded_at": meta["uploaded_at"]
        }
    finally:
        # 清理临时文件
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)


@router.delete("/{id}/zip")
async def delete_project_zip_file(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    删除项目ZIP文件
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权操作此项目")
    
    deleted = await delete_project_zip(id)
    
    if deleted:
        return {"message": "ZIP文件已删除"}
    else:
        return {"message": "没有找到ZIP文件"}


# ============ 分支管理端点 ============

@router.get("/{id}/branches")
async def get_project_branches(
    id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    获取项目仓库的分支列表
    """
    project = await db.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查是否为仓库类型项目
    if project.source_type != "repository":
        raise HTTPException(status_code=400, detail="仅仓库类型项目支持获取分支")
    
    if not project.repository_url:
        raise HTTPException(status_code=400, detail="项目未配置仓库地址")
    
    # 获取用户配置的 Token
    from app.core.config import settings
    from app.core.encryption import decrypt_sensitive_data
    
    config = await db.execute(
        select(UserConfig).where(UserConfig.user_id == current_user.id)
    )
    config = config.scalar_one_or_none()
    
    github_token = settings.GITHUB_TOKEN
    gitea_token = settings.GITEA_TOKEN
    gitlab_token = settings.GITLAB_TOKEN

    SENSITIVE_OTHER_FIELDS = ['githubToken', 'gitlabToken', 'giteaToken']
    
    if config and config.other_config:
        import json
        other_config = json.loads(config.other_config)
        for field in SENSITIVE_OTHER_FIELDS:
            if field in other_config and other_config[field]:
                decrypted_val = decrypt_sensitive_data(other_config[field])
                if field == 'githubToken':
                    github_token = decrypted_val
                elif field == 'gitlabToken':
                    gitlab_token = decrypted_val
                elif field == 'giteaToken':
                    gitea_token = decrypted_val
    
    repo_type = project.repository_type or "other"
    
    # 详细日志
    print(f"[Branch] 项目: {project.name}, 类型: {repo_type}, URL: {project.repository_url}")
    
    try:
        if repo_type == "github":
            if not github_token:
                print("[Branch] 警告: GitHub Token 未配置，可能会遇到 API 限制")
            branches = await get_github_branches(project.repository_url, github_token)
        elif repo_type == "gitlab":
            if not gitlab_token:
                print("[Branch] 警告: GitLab Token 未配置，可能无法访问私有仓库")
            branches = await get_gitlab_branches(project.repository_url, gitlab_token)
        elif repo_type == "gitea":
            if not gitea_token:
                print("[Branch] 警告: Gitea Token 未配置，可能无法访问私有仓库")
            branches = await get_gitea_branches(project.repository_url, gitea_token)
        else:
            # 对于其他类型，返回默认分支
            print(f"[Branch] 仓库类型 '{repo_type}' 不支持获取分支，返回默认分支")
            branches = [project.default_branch or "main"]
        
        print(f"[Branch] 成功获取 {len(branches)} 个分支")
        
        # 将默认分支放在第一位
        default_branch = project.default_branch or "main"
        if default_branch in branches:
            branches.remove(default_branch)
            branches.insert(0, default_branch)
        
        return {"branches": branches, "default_branch": default_branch}
    
    except Exception as e:
        error_msg = str(e)
        print(f"[Branch] 获取分支列表失败: {error_msg}")
        # 返回默认分支作为后备
        return {
            "branches": [project.default_branch or "main"],
            "default_branch": project.default_branch or "main",
            "error": str(e)
        }
