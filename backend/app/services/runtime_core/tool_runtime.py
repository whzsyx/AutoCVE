from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from app.models.audit_session import AuditCheckpointType, AuditToolCallStatus
from app.services.runtime_core.permission_runtime import RuntimePermissionRuntime, ToolPermissionDecision
from app.services.finding_runtime.models import (
    ToolCallRequest,
    ToolExecutionPayload,
    ToolExecutionRecord,
)


@dataclass(slots=True)
class ToolExecutionContext:
    session_id: str
    turn_id: str
    tool_use_id: str
    tool_call_id: str
    agent_type: str = "runtime"
    session: Any = None
    recon_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def match_runtime_event_hooks(hook_config: dict[str, Any], *, event_name: str, tool_name: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for entry in hook_config.get(event_name) or []:
        matcher = str(entry.get("matcher") or "").strip()
        if matcher not in {"", "*", tool_name}:
            continue
        matched.append(dict(entry))
    return matched


class RuntimeTool:
    name: str = ""
    description: str = ""
    input_model: type[BaseModel] | None = None

    def validate_input(self, raw_input: dict[str, Any]) -> Any:
        if self.input_model is None:
            return raw_input
        return self.input_model.model_validate(raw_input or {})

    def is_concurrency_safe(self, parsed_input: Any) -> bool:
        return False

    def concurrency_key(self, parsed_input: Any) -> str | None:
        return None

    async def check_permission(
        self,
        parsed_input: Any,
        context: ToolExecutionContext,
    ) -> ToolPermissionDecision:
        return ToolPermissionDecision(allowed=True)

    async def execute(self, parsed_input: Any, context: ToolExecutionContext) -> ToolExecutionPayload:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema() if self.input_model is not None else {"type": "object"}
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


class ToolRegistry:
    def __init__(self, tools: list[RuntimeTool] | None = None):
        self._tools: dict[str, RuntimeTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: RuntimeTool) -> None:
        if not tool.name:
            raise ValueError("Runtime tools must define a name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> RuntimeTool | None:
        return self._tools.get(name)

    def describe_tools(self) -> list[dict[str, Any]]:
        return [tool.describe() for tool in self._tools.values()]


@dataclass(slots=True)
class _PreparedToolCall:
    request: ToolCallRequest
    tool: RuntimeTool | None
    parsed_input: Any = None
    is_concurrency_safe: bool = False
    concurrency_key: str | None = None
    validation_error: str | None = None


class ToolOrchestrator:
    def __init__(self, *, session_store, tool_registry: ToolRegistry, agent_type: str = "finding", permission_runtime: RuntimePermissionRuntime | None = None):
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._agent_type = agent_type
        self._permission_runtime = permission_runtime or RuntimePermissionRuntime(session_store=session_store, agent_type=agent_type)

    async def execute_tool_calls(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_calls: list[ToolCallRequest],
        session: Any = None,
        recon_payload: dict[str, Any] | None = None,
    ) -> list[ToolExecutionRecord]:
        prepared = [self._prepare_call(tool_call) for tool_call in tool_calls]
        records: list[ToolExecutionRecord] = []

        for is_concurrency_safe, batch in self._partition_batches(prepared):
            if is_concurrency_safe:
                batch_records = await asyncio.gather(
                    *[
                        self._execute_prepared_call(
                            prepared_call,
                            session_id=session_id,
                            turn_id=turn_id,
                            session=session,
                            recon_payload=recon_payload or {},
                        )
                        for prepared_call in batch
                    ]
                )
                records.extend(batch_records)
            else:
                for prepared_call in batch:
                    records.append(
                        await self._execute_prepared_call(
                            prepared_call,
                            session_id=session_id,
                            turn_id=turn_id,
                            session=session,
                            recon_payload=recon_payload or {},
                        )
                    )
        return records

    def _prepare_call(self, request: ToolCallRequest) -> _PreparedToolCall:
        tool = self._tool_registry.get(request.name)
        if tool is None:
            return _PreparedToolCall(request=request, tool=None)
        try:
            parsed_input = tool.validate_input(request.input)
        except ValidationError as exc:
            return _PreparedToolCall(request=request, tool=tool, validation_error=str(exc))

        try:
            is_concurrency_safe = bool(tool.is_concurrency_safe(parsed_input))
        except Exception:
            is_concurrency_safe = False
        try:
            raw_concurrency_key = tool.concurrency_key(parsed_input) if is_concurrency_safe else None
            concurrency_key = str(raw_concurrency_key).strip() or None if raw_concurrency_key is not None else None
        except Exception:
            concurrency_key = None
        return _PreparedToolCall(
            request=request,
            tool=tool,
            parsed_input=parsed_input,
            is_concurrency_safe=is_concurrency_safe,
            concurrency_key=concurrency_key,
        )

    @staticmethod
    def _partition_batches(prepared_calls: list[_PreparedToolCall]) -> list[tuple[bool, list[_PreparedToolCall]]]:
        batches: list[tuple[bool, list[_PreparedToolCall]]] = []
        active_keys: set[str] = set()
        for prepared_call in prepared_calls:
            if prepared_call.is_concurrency_safe:
                key = prepared_call.concurrency_key
                can_join_batch = bool(batches and batches[-1][0] and (not key or key not in active_keys))
                if can_join_batch:
                    batches[-1][1].append(prepared_call)
                    if key:
                        active_keys.add(key)
                    continue
                batches.append((True, [prepared_call]))
                active_keys = {key} if key else set()
                continue
            batches.append((False, [prepared_call]))
            active_keys = set()
        return batches

    async def _execute_prepared_call(
        self,
        prepared_call: _PreparedToolCall,
        *,
        session_id: str,
        turn_id: str,
        session: Any,
        recon_payload: dict[str, Any],
    ) -> ToolExecutionRecord:
        request = prepared_call.request
        tool_call_id = self._session_store.start_tool_call(
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id=request.id,
            tool_name=request.name,
            input_payload=request.input,
            is_concurrency_safe=prepared_call.is_concurrency_safe,
        )
        started = perf_counter()

        if prepared_call.tool is None:
            return self._finalize_error_record(
                tool_call_id=tool_call_id,
                request=request,
                status=AuditToolCallStatus.MISSING.value,
                is_concurrency_safe=prepared_call.is_concurrency_safe,
                started=started,
                message=f"Unknown tool: {request.name}",
            )

        if prepared_call.validation_error is not None:
            return self._finalize_error_record(
                tool_call_id=tool_call_id,
                request=request,
                status=AuditToolCallStatus.INVALID.value,
                is_concurrency_safe=prepared_call.is_concurrency_safe,
                started=started,
                message=prepared_call.validation_error,
            )

        context = ToolExecutionContext(
            session_id=session_id,
            turn_id=turn_id,
            tool_use_id=request.id,
            tool_call_id=tool_call_id,
            agent_type=self._agent_type,
            session=session,
            recon_payload=dict(recon_payload or {}),
        )

        runtime_permission = self._permission_runtime.evaluate_tool_use(tool_name=request.name, context=context)
        if not runtime_permission.allowed:
            self._emit_hook_event(
                event_name="PermissionDenied",
                context=context,
                tool_name=request.name,
                payload={"reason": runtime_permission.reason, "source": runtime_permission.source, "mode": runtime_permission.mode},
            )
            return self._finalize_error_record(
                tool_call_id=tool_call_id,
                request=request,
                status=AuditToolCallStatus.DENIED.value,
                is_concurrency_safe=prepared_call.is_concurrency_safe,
                started=started,
                message=runtime_permission.reason or "Tool permission denied",
                output_payload={
                    "permission_source": runtime_permission.source,
                    "permission_mode": runtime_permission.mode,
                    "permission_reason": runtime_permission.reason,
                },
            )

        permission = await prepared_call.tool.check_permission(prepared_call.parsed_input, context)
        if not permission.allowed:
            self._emit_hook_event(
                event_name="PermissionDenied",
                context=context,
                tool_name=request.name,
                payload={"reason": permission.reason, "source": permission.source or "tool", "mode": getattr(permission, "mode", "deny")},
            )
            return self._finalize_error_record(
                tool_call_id=tool_call_id,
                request=request,
                status=AuditToolCallStatus.DENIED.value,
                is_concurrency_safe=prepared_call.is_concurrency_safe,
                started=started,
                message=permission.reason or "Tool permission denied",
                output_payload={
                    "permission_source": permission.source or "tool",
                    "permission_mode": getattr(permission, "mode", "deny"),
                    "permission_reason": permission.reason,
                    "guardrail_code": getattr(permission, "guardrail_code", None),
                },
            )

        self._emit_hook_event(event_name="PreToolUse", context=context, tool_name=request.name)
        try:
            result = await prepared_call.tool.execute(prepared_call.parsed_input, context)
        except Exception as exc:
            self._emit_hook_event(
                event_name="PostToolUseFailure",
                context=context,
                tool_name=request.name,
                payload={"error": str(exc)},
            )
            return self._finalize_error_record(
                tool_call_id=tool_call_id,
                request=request,
                status=AuditToolCallStatus.FAILED.value,
                is_concurrency_safe=prepared_call.is_concurrency_safe,
                started=started,
                message=str(exc),
            )

        self._emit_hook_event(event_name="PostToolUse", context=context, tool_name=request.name)
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        self._session_store.complete_tool_call(
            tool_call_id,
            status=AuditToolCallStatus.COMPLETED.value,
            output_payload=result.output_payload,
            error_message=None,
            duration_ms=duration_ms,
        )
        return ToolExecutionRecord(
            tool_call_id=tool_call_id,
            request=request,
            status=AuditToolCallStatus.COMPLETED.value,
            is_concurrency_safe=prepared_call.is_concurrency_safe,
            result=result,
            error_message=None,
            duration_ms=duration_ms,
        )

    def _emit_hook_event(self, *, event_name: str, context: ToolExecutionContext, tool_name: str, payload: dict[str, Any] | None = None) -> None:
        runtime_state = self._session_store.load_runtime_state(context.session_id)
        session_hooks = runtime_state.metadata.get("session_hooks") or {}
        payload_dict = dict(payload or {})
        persist_without_hook = (
            event_name == "PermissionDenied"
            and payload_dict.get("source") == "permission_rule"
        )
        wrote_checkpoint = False
        for skill_ref, hook_config in session_hooks.items():
            matched_hooks = match_runtime_event_hooks(hook_config, event_name=event_name, tool_name=tool_name)
            if not matched_hooks:
                continue
            self._session_store.create_checkpoint(
                session_id=context.session_id,
                turn_id=context.turn_id,
                checkpoint_type=AuditCheckpointType.AUTO,
                state_payload={
                    "kind": "runtime_hook",
                    "event": event_name,
                    "tool_name": tool_name,
                    "agent_type": self._agent_type,
                    "skill_ref": skill_ref,
                    "matched_hooks": matched_hooks,
                    **payload_dict,
                },
            )
            wrote_checkpoint = True
        if persist_without_hook and not wrote_checkpoint:
            self._session_store.create_checkpoint(
                session_id=context.session_id,
                turn_id=context.turn_id,
                checkpoint_type=AuditCheckpointType.AUTO,
                state_payload={
                    "kind": "runtime_hook",
                    "event": event_name,
                    "tool_name": tool_name,
                    "agent_type": self._agent_type,
                    "skill_ref": None,
                    "matched_hooks": [],
                    **payload_dict,
                },
            )

    def _finalize_error_record(
        self,
        *,
        tool_call_id: str,
        request: ToolCallRequest,
        status: str,
        is_concurrency_safe: bool,
        started: float,
        message: str,
        output_payload: dict[str, Any] | None = None,
    ) -> ToolExecutionRecord:
        duration_ms = max(0, int((perf_counter() - started) * 1000))
        result = ToolExecutionPayload(content=message, output_payload=dict(output_payload or {}), metadata={}, is_error=True)
        self._session_store.complete_tool_call(
            tool_call_id,
            status=status,
            output_payload=result.output_payload,
            error_message=message,
            duration_ms=duration_ms,
        )
        return ToolExecutionRecord(
            tool_call_id=tool_call_id,
            request=request,
            status=status,
            is_concurrency_safe=is_concurrency_safe,
            result=result,
            error_message=message,
            duration_ms=duration_ms,
        )
