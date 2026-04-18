from __future__ import annotations

import asyncio
import os
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.endpoints.agent_tasks import _get_project_root, _get_user_config, _initialize_tools
from app.api.v1.endpoints.audit_sessions import (
    AuditSessionMessageCreate,
    AuditSessionMessageResponse,
    AuditSessionResponse,
    _chunk_text,
    _format_sse_event,
    _build_agent_user_config,
    _to_message_response,
    _to_session_response,
)
from app.core.config import settings
from app.core.encryption import decrypt_sensitive_data
from app.db.session import get_db
from app.models.audit_session import AuditSession, AuditSessionMessage, AuditToolCall
from app.models.project import Project
from app.models.user import User
from app.services.agent.tools.sandbox_tool import SandboxManager
from app.services.finding_runtime.bridge import FindingRuntimeBridge
from app.services.llm.service import LLMService
from app.services.runtime_core.runtime_guardrails import (
    register_shell_approval,
    set_guardrails_enabled,
)
from app.services.runtime_core.runtime_tool_registry import CanonicalWriteTool
from app.services.runtime_core.session_state import SessionRuntimeState as SharedSessionRuntimeState

router = APIRouter()

DEFAULT_DIRECT_AUDIT_MAX_TURNS = 8


class DirectAuditSessionCreate(BaseModel):
    project_id: str
    content: str
    guardrails_enabled: bool = False


class DirectAuditGuardrailUpdate(BaseModel):
    enabled: bool


def _build_direct_audit_system_prompt(project: Project) -> str:
    return (
        "You are AuditAI's finding agent in Agent Direct Audit mode. "
        "Work directly against the selected project workspace, use tools and skills as needed, "
        "and answer in Markdown with concrete evidence, file paths, and next steps when relevant. "
        f"Current project: {project.name}."
    )


def _build_direct_recon_payload(project: Project, workspace_root: str) -> dict[str, Any]:
    return {
        "project_info": {
            "project_id": project.id,
            "name": project.name,
            "source_type": project.source_type,
            "workspace_root": workspace_root,
            "repository_url": project.repository_url,
            "default_branch": project.default_branch,
        }
    }


async def _resolve_workspace_root(
    *,
    project: Project,
    workspace_key: str,
    github_token: Optional[str],
    gitlab_token: Optional[str],
    gitea_token: Optional[str],
    ssh_private_key: Optional[str],
) -> str:
    if project.source_type == "local_directory":
        if not project.local_path:
            raise HTTPException(status_code=400, detail="Local directory project is missing local_path")
        workspace_root = os.path.abspath(project.local_path)
        if not os.path.isdir(workspace_root):
            raise HTTPException(status_code=400, detail="Local project directory is unavailable")
        return workspace_root

    return await _get_project_root(
        project,
        f"direct-audit-{workspace_key}",
        project.default_branch,
        github_token=github_token,
        gitlab_token=gitlab_token,
        gitea_token=gitea_token,
        ssh_private_key=ssh_private_key,
        event_emitter=None,
    )


async def _build_direct_runtime_context(
    *,
    project: Project,
    db: AsyncSession,
    current_user: User,
    workspace_root: Optional[str] = None,
) -> tuple[FindingRuntimeBridge, SandboxManager, str, int, str, dict[str, Any]]:
    user_config = await _get_user_config(db, current_user.id)
    other_config = (user_config or {}).get("otherConfig", {})
    github_token = other_config.get("githubToken") or settings.GITHUB_TOKEN
    gitlab_token = other_config.get("gitlabToken") or settings.GITLAB_TOKEN
    gitea_token = other_config.get("giteaToken") or settings.GITEA_TOKEN
    ssh_private_key = None
    if other_config.get("sshPrivateKey"):
        try:
            ssh_private_key = decrypt_sensitive_data(other_config["sshPrivateKey"])
        except Exception:
            ssh_private_key = None

    resolved_workspace_root = workspace_root or await _resolve_workspace_root(
        project=project,
        workspace_key=str(uuid4()),
        github_token=github_token,
        gitlab_token=gitlab_token,
        gitea_token=gitea_token,
        ssh_private_key=ssh_private_key,
    )

    sandbox_manager = SandboxManager()
    await sandbox_manager.initialize()

    llm_service = LLMService(user_config=_build_agent_user_config(user_config, "finding"))
    tools = await _initialize_tools(
        resolved_workspace_root,
        llm_service,
        user_config,
        sandbox_manager=sandbox_manager,
        exclude_patterns=None,
        target_files=None,
        project_id=str(project.id),
        event_emitter=None,
        task_id=None,
        user_id=current_user.id,
    )
    bridge = FindingRuntimeBridge(
        llm_service=llm_service,
        tools=tools.get("finding", {}),
        user_id=current_user.id,
    )
    return (
        bridge,
        sandbox_manager,
        "finding",
        DEFAULT_DIRECT_AUDIT_MAX_TURNS,
        _build_direct_audit_system_prompt(project),
        _build_direct_recon_payload(project, resolved_workspace_root),
    )


async def _get_owned_project(*, project_id: str, db: AsyncSession, current_user: User) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return project


async def _get_owned_direct_session(*, session_id: str, db: AsyncSession, current_user: User) -> AuditSession:
    session = await db.get(AuditSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Audit session not found")
    project = await _get_owned_project(project_id=session.project_id, db=db, current_user=current_user)
    if session.task_id is not None:
        raise HTTPException(status_code=400, detail="Session is not an Agent Direct Audit session")
    session.project = project  # type: ignore[attr-defined]
    return session


async def _get_owned_direct_tool_call(
    *,
    session_id: str,
    tool_call_id: str,
    db: AsyncSession,
    current_user: User,
) -> tuple[AuditSession, AuditToolCall]:
    session = await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    tool_call = await db.get(AuditToolCall, tool_call_id)
    if tool_call is None or tool_call.session_id != session.id:
        raise HTTPException(status_code=404, detail="Tool call not found")
    return session, tool_call


async def _apply_direct_audit_guardrail_setting(*, session_id: str, db: AsyncSession, enabled: bool) -> AuditSession:
    session = await db.get(AuditSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Audit session not found")
    runtime_payload = dict(session.runtime_state_json or {})
    runtime_payload["session_id"] = session.id
    runtime_state = SharedSessionRuntimeState.model_validate(runtime_payload)
    set_guardrails_enabled(runtime_state, enabled)
    session.runtime_state_json = runtime_state.model_dump(mode="json")
    await db.commit()
    await db.refresh(session)
    return session


def _build_direct_audit_write_approval_content(*, path: str) -> str:
    return (
        f"I approve writing to `{path}` for this direct-audit session. "
        "Please retry the previously blocked write if it is still needed, then continue the audit and explain what changed."
    )


def _build_direct_audit_shell_approval_content(*, tool_name: str, command: str) -> str:
    return (
        f"I approve running the {tool_name} command `{command}` for this direct-audit session. "
        "Please retry the blocked command if it is still needed, then continue the audit and explain what changed."
    )


def _grant_tool_call_approval(session: AuditSession, tool_call: AuditToolCall) -> dict[str, Any]:
    input_payload = dict(tool_call.input_payload or {})
    output_payload = dict(tool_call.output_payload or {})
    if str(output_payload.get("permission_mode") or "") != "ask":
        raise HTTPException(status_code=400, detail="Tool call does not require approval")

    runtime_payload = dict(session.runtime_state_json or {})
    runtime_payload["session_id"] = session.id
    runtime_state = SharedSessionRuntimeState.model_validate(runtime_payload)
    guardrail_code = str(output_payload.get("guardrail_code") or "").strip()

    if tool_call.tool_name == "Write":
        target_path = str(input_payload.get("path") or "").strip()
        if not target_path:
            raise HTTPException(status_code=400, detail="Tool call is missing a writable path")
        CanonicalWriteTool.register_approval(
            runtime_state,
            path=target_path,
            guardrail_code=guardrail_code or "source_write_requires_approval",
            tool_call_id=tool_call.id,
        )
        session.runtime_state_json = runtime_state.model_dump(mode="json")
        return {
            "approval_content": _build_direct_audit_write_approval_content(path=target_path),
            "payload": {
                "path": target_path,
                "guardrail_code": guardrail_code or "source_write_requires_approval",
            },
        }

    if tool_call.tool_name in {"Bash", "PowerShell"}:
        command = str(input_payload.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="Tool call is missing a shell command")
        register_shell_approval(
            runtime_state,
            tool_name=tool_call.tool_name,
            command=command,
            guardrail_code=guardrail_code or "shell_command_requires_approval",
            tool_call_id=tool_call.id,
        )
        session.runtime_state_json = runtime_state.model_dump(mode="json")
        return {
            "approval_content": _build_direct_audit_shell_approval_content(tool_name=tool_call.tool_name, command=command),
            "payload": {
                "tool_name": tool_call.tool_name,
                "command": command,
                "guardrail_code": guardrail_code or "shell_command_requires_approval",
            },
        }

    raise HTTPException(status_code=400, detail="This tool is not yet approveable in Agent Direct Audit")


async def start_direct_audit_session(
    *,
    project: Project,
    content: str,
    guardrails_enabled: bool,
    db: AsyncSession,
    current_user: User,
) -> AuditSession:
    bridge, sandbox_manager, model_name, max_turns, system_prompt, recon_payload = await _build_direct_runtime_context(
        project=project,
        db=db,
        current_user=current_user,
    )
    try:
        async def handle_session_created(session_id: str):
            await _apply_direct_audit_guardrail_setting(
                session_id=session_id,
                db=db,
                enabled=guardrails_enabled,
            )

        result = await bridge.run_chat_session(
            project_id=project.id,
            task_id=None,
            system_prompt=system_prompt,
            recon_payload=recon_payload,
            user_message=content,
            model_name=model_name,
            max_turns=max_turns,
            on_session_created=handle_session_created,
        )
    finally:
        try:
            await sandbox_manager.cleanup()
        except Exception:
            pass

    session = await db.get(AuditSession, result["session_id"])
    if session is None:
        raise HTTPException(status_code=500, detail="Direct audit session was not persisted")
    return session


async def start_direct_audit_session_stream(
    *,
    project: Project,
    content: str,
    guardrails_enabled: bool,
    db: AsyncSession,
    current_user: User,
):
    bridge, sandbox_manager, model_name, max_turns, system_prompt, recon_payload = await _build_direct_runtime_context(
        project=project,
        db=db,
        current_user=current_user,
    )
    try:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def handle_session_created(session_id: str):
            await _apply_direct_audit_guardrail_setting(
                session_id=session_id,
                db=db,
                enabled=guardrails_enabled,
            )
            await queue.put({"type": "session_created", "session_id": session_id, "project_id": project.id})

        async def handle_user_message_created(message_id: str):
            user_message = await db.get(AuditSessionMessage, message_id)
            if user_message is not None:
                await queue.put({"type": "user_message", "message": _to_message_response(user_message).model_dump(mode="json")})

        async def collect_event(event: dict[str, Any]):
            await queue.put(event)

        async def worker():
            try:
                await bridge.run_chat_session_stream(
                    project_id=project.id,
                    task_id=None,
                    system_prompt=system_prompt,
                    recon_payload=recon_payload,
                    user_message=content,
                    model_name=model_name,
                    max_turns=max_turns,
                    event_sink=collect_event,
                    on_session_created=handle_session_created,
                    on_user_message_created=handle_user_message_created,
                )
            finally:
                await queue.put(None)

        worker_task = asyncio.create_task(worker())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await worker_task
        finally:
            if not worker_task.done():
                worker_task.cancel()
    finally:
        try:
            await sandbox_manager.cleanup()
        except Exception:
            pass


async def continue_direct_audit_session(
    *,
    session: AuditSession,
    content: str,
    db: AsyncSession,
    current_user: User,
) -> None:
    del content
    project = await _get_owned_project(project_id=session.project_id, db=db, current_user=current_user)
    workspace_root = str(((session.recon_payload or {}).get("project_info") or {}).get("workspace_root") or "").strip() or None
    bridge, sandbox_manager, model_name, max_turns, _system_prompt, _recon_payload = await _build_direct_runtime_context(
        project=project,
        db=db,
        current_user=current_user,
        workspace_root=workspace_root,
    )
    try:
        await bridge.continue_chat_session(session_id=session.id, model_name=model_name, max_turns=max_turns)
    finally:
        try:
            await sandbox_manager.cleanup()
        except Exception:
            pass


async def continue_direct_audit_session_stream(
    *,
    session: AuditSession,
    content: str,
    db: AsyncSession,
    current_user: User,
):
    project = await _get_owned_project(project_id=session.project_id, db=db, current_user=current_user)
    workspace_root = str(((session.recon_payload or {}).get("project_info") or {}).get("workspace_root") or "").strip() or None
    bridge, sandbox_manager, model_name, max_turns, _system_prompt, _recon_payload = await _build_direct_runtime_context(
        project=project,
        db=db,
        current_user=current_user,
        workspace_root=workspace_root,
    )
    try:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def collect_event(event: dict[str, Any]):
            await queue.put(event)

        async def worker():
            try:
                await bridge.continue_chat_session_stream(
                    session_id=session.id,
                    model_name=model_name,
                    max_turns=max_turns,
                    event_sink=collect_event,
                )
            finally:
                await queue.put(None)

        worker_task = asyncio.create_task(worker())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await worker_task
        finally:
            if not worker_task.done():
                worker_task.cancel()
    finally:
        try:
            await sandbox_manager.cleanup()
        except Exception:
            pass


@router.get("/sessions", response_model=list[AuditSessionResponse])
async def list_direct_audit_sessions(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> list[AuditSessionResponse]:
    await _get_owned_project(project_id=project_id, db=db, current_user=current_user)
    result = await db.execute(
        select(AuditSession)
        .where(
            AuditSession.project_id == project_id,
            AuditSession.task_id.is_(None),
            AuditSession.runtime_stack == "runtime",
        )
        .order_by(AuditSession.updated_at.desc(), AuditSession.created_at.desc())
    )
    return [_to_session_response(item) for item in result.scalars().all()]


@router.post("/sessions", response_model=AuditSessionResponse)
async def create_direct_audit_session(
    payload: DirectAuditSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> AuditSessionResponse:
    project = await _get_owned_project(project_id=payload.project_id, db=db, current_user=current_user)
    session = await start_direct_audit_session(
        project=project,
        content=payload.content,
        guardrails_enabled=payload.guardrails_enabled,
        db=db,
        current_user=current_user,
    )
    return _to_session_response(session)


@router.post("/sessions/stream")
async def stream_create_direct_audit_session(
    payload: DirectAuditSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> StreamingResponse:
    project = await _get_owned_project(project_id=payload.project_id, db=db, current_user=current_user)

    async def event_generator():
        try:
            async for event in start_direct_audit_session_stream(
                project=project,
                content=payload.content,
                guardrails_enabled=payload.guardrails_enabled,
                db=db,
                current_user=current_user,
            ):
                yield _format_sse_event(event)
        except Exception as exc:
            await db.rollback()
            yield _format_sse_event({"type": "error", "message_text": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/sessions/{session_id}", response_model=AuditSessionResponse)
async def get_direct_audit_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> AuditSessionResponse:
    session = await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    return _to_session_response(session)


@router.patch("/sessions/{session_id}/guardrails", response_model=AuditSessionResponse)
async def update_direct_audit_guardrails(
    session_id: str,
    payload: DirectAuditGuardrailUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> AuditSessionResponse:
    await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    session = await _apply_direct_audit_guardrail_setting(
        session_id=session_id,
        db=db,
        enabled=payload.enabled,
    )
    return _to_session_response(session)


@router.get("/sessions/{session_id}/messages", response_model=list[AuditSessionMessageResponse])
async def list_direct_audit_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> list[AuditSessionMessageResponse]:
    await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    result = await db.execute(
        select(AuditSessionMessage)
        .where(AuditSessionMessage.session_id == session_id)
        .order_by(AuditSessionMessage.sequence)
    )
    return [_to_message_response(message) for message in result.scalars().all()]


@router.post("/sessions/{session_id}/messages", response_model=AuditSessionMessageResponse)
async def create_direct_audit_session_message(
    session_id: str,
    payload: AuditSessionMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> AuditSessionMessageResponse:
    session = await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    next_sequence = await db.scalar(
        select(func.max(AuditSessionMessage.sequence)).where(AuditSessionMessage.session_id == session_id)
    )
    message = AuditSessionMessage(
        session_id=session_id,
        sequence=(next_sequence or 0) + 1,
        role="user",
        content=payload.content,
        message_metadata={"kind": "direct_audit_user_message"},
        payload={"continued": True},
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    await continue_direct_audit_session(
        session=session,
        content=payload.content,
        db=db,
        current_user=current_user,
    )

    return _to_message_response(message)


@router.post("/sessions/{session_id}/messages/stream")
async def stream_direct_audit_session_message(
    session_id: str,
    payload: AuditSessionMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> StreamingResponse:
    session = await _get_owned_direct_session(session_id=session_id, db=db, current_user=current_user)
    next_sequence = await db.scalar(
        select(func.max(AuditSessionMessage.sequence)).where(AuditSessionMessage.session_id == session_id)
    )
    user_message = AuditSessionMessage(
        session_id=session_id,
        sequence=(next_sequence or 0) + 1,
        role="user",
        content=payload.content,
        message_metadata={"kind": "direct_audit_user_message", "streaming": True},
        payload={"continued": True, "streaming": True},
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)

    async def event_generator():
        yield _format_sse_event({
            "type": "user_message",
            "message": _to_message_response(user_message).model_dump(mode="json"),
        })
        try:
            async for event in continue_direct_audit_session_stream(
                session=session,
                content=payload.content,
                db=db,
                current_user=current_user,
            ):
                yield _format_sse_event(event)
        except Exception as exc:
            await db.rollback()
            yield _format_sse_event({"type": "error", "message_text": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/sessions/{session_id}/tool-calls/{tool_call_id}/approve/stream")
async def stream_approve_direct_audit_tool_call(
    session_id: str,
    tool_call_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(deps.get_current_user),
) -> StreamingResponse:
    session, tool_call = await _get_owned_direct_tool_call(
        session_id=session_id,
        tool_call_id=tool_call_id,
        db=db,
        current_user=current_user,
    )
    approval = _grant_tool_call_approval(session, tool_call)
    approval_content = str(approval.get("approval_content") or "").strip()
    approval_payload = dict(approval.get("payload") or {})
    next_sequence = await db.scalar(
        select(func.max(AuditSessionMessage.sequence)).where(AuditSessionMessage.session_id == session_id)
    )
    user_message = AuditSessionMessage(
        session_id=session_id,
        sequence=(next_sequence or 0) + 1,
        role="user",
        content=approval_content,
        message_metadata={
            "kind": "direct_audit_approval",
            "streaming": True,
            "tool_call_id": tool_call.id,
        },
        payload={
            "continued": True,
            "streaming": True,
            "approval": True,
            "tool_call_id": tool_call.id,
            **approval_payload,
        },
    )
    db.add(user_message)
    await db.commit()
    await db.refresh(user_message)
    await db.refresh(session)

    async def event_generator():
        yield _format_sse_event({
            "type": "user_message",
            "message": _to_message_response(user_message).model_dump(mode="json"),
        })
        try:
            async for event in continue_direct_audit_session_stream(
                session=session,
                content=approval_content,
                db=db,
                current_user=current_user,
            ):
                yield _format_sse_event(event)
        except Exception as exc:
            await db.rollback()
            yield _format_sse_event({"type": "error", "message_text": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
