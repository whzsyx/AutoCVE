from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timezone

from app.models.audit_session import AuditCheckpointType
from app.services.agent.json_parser import AgentJsonParser
from app.services.finding_runtime.models import (
    RuntimeContinueReason,
    RuntimeMessageRole,
    RuntimeModelResponse,
    RuntimeStopReason,
    ToolCallRequest,
    TranscriptItem,
    TurnExecutionResult,
)
from app.services.finding_runtime.query_attachments import (
    build_between_turn_attachments,
    materialize_pending_tool_use_summary,
    start_pending_tool_use_summary,
)
from app.services.finding_runtime.compaction.auto_compact import auto_compact_if_needed
from app.services.finding_runtime.compaction.compact import compact_conversation
from app.services.finding_runtime.compaction.models import AutoCompactTrackingState
from app.services.finding_runtime.compaction.post_compact import build_post_compact_messages
from app.services.finding_runtime.query_context import (
    append_system_context,
    apply_history_snip,
    apply_microcompact,
    apply_tool_result_budget,
    evaluate_blocking_limit,
    get_messages_after_compact_boundary,
    prepend_user_context,
    apply_context_collapse_if_needed,
)
from app.services.finding_runtime.query_degradation import handle_recoverable_response
from app.services.finding_runtime.query_messages import normalize_messages_for_model
from app.services.finding_runtime.query_state import QueryLoopState
from app.services.finding_runtime.query_stop_hooks import (
    build_stop_hook_artifact_messages,
    build_stop_hook_messages,
    evaluate_post_tool_hooks,
    evaluate_stop_hooks,
)
from app.services.runtime_core.hook_policy import collect_turn_hook_events
from app.services.finding_runtime.query_token_budget import evaluate_token_budget_continuation
from app.services.finding_runtime.query_transitions import (
    build_continue_state,
    build_terminal_state,
    hydrate_query_loop_state,
)


class QueryLoop:
    def __init__(self, *, session_store, model_client, tool_registry=None, tool_orchestrator=None, event_sink=None):
        self._session_store = session_store
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._tool_orchestrator = tool_orchestrator
        self._event_sink = event_sink

    async def run_turn(self, *, session_id: str, model_name: str) -> TurnExecutionResult:
        snapshot = self._session_store.load_session_snapshot(session_id)
        runtime_state = self._session_store.load_runtime_state(session_id)
        state = self._load_query_loop_state(session_id=session_id, snapshot=snapshot)
        state = self._merge_runtime_query_context_pipeline(state, runtime_state)
        state = materialize_pending_tool_use_summary(state)
        turn_id = self._session_store.open_turn(session_id, model_name=model_name)
        tool_definitions = self._tool_registry.describe_tools() if self._tool_registry is not None else []
        transcript = list(state.messages)
        prepared_messages = get_messages_after_compact_boundary(transcript, state)
        prepared_messages = apply_tool_result_budget(prepared_messages, state)
        prepared_messages = apply_history_snip(prepared_messages, state)
        prepared_messages = apply_microcompact(prepared_messages, state)
        prepared_messages, state = apply_context_collapse_if_needed(prepared_messages, state)
        auto_compact_decision = auto_compact_if_needed(
            prepared_messages,
            state,
            tracking=self._extract_auto_compact_tracking(state),
            compactor=lambda messages, state, **kwargs: compact_conversation(
                messages,
                state,
                model_client=self._model_client,
                **kwargs,
            ),
        )
        if inspect.isawaitable(auto_compact_decision):
            auto_compact_decision = await auto_compact_decision
        prepared_messages, state = self._apply_auto_compact_decision(
            state=state,
            prepared_messages=prepared_messages,
            decision=auto_compact_decision,
        )
        effective_system_prompt = append_system_context(snapshot.session.system_prompt, runtime_state)
        prepared_messages = prepend_user_context(prepared_messages, runtime_state)
        prepared_messages = normalize_messages_for_model(prepared_messages)
        blocking_limit = evaluate_blocking_limit(prepared_messages, state)
        if blocking_limit.get("blocked"):
            return self._finalize_terminal_result(
                session_id=session_id,
                turn_id=turn_id,
                state=state,
                messages=state.messages,
                stop_reason=RuntimeStopReason.BLOCKING_LIMIT,
                status="blocking_limit",
                checkpoint_extra={
                    "phase": "blocking_limit_preflight",
                    "blocking_limit": {
                        "current_chars": int(blocking_limit.get("current_chars") or 0),
                        "max_chars": int(blocking_limit.get("max_chars") or 0),
                    },
                },
            )
        assistant_stream_started = False
        assistant_stream_sequence = (snapshot.messages[-1].sequence if snapshot.messages else 0) + 1
        assistant_stream_placeholder_id = f"streaming-{session_id}-{turn_id}"

        async def handle_model_stream_event(event: dict[str, object]) -> None:
            nonlocal assistant_stream_started
            if not self._event_sink:
                return
            event_type = str(event.get("type") or "")
            if event_type == "token":
                if not assistant_stream_started:
                    assistant_stream_started = True
                    await self._emit_event({
                        "type": "assistant_start",
                        "message": {
                            "id": assistant_stream_placeholder_id,
                            "session_id": session_id,
                            "sequence": assistant_stream_sequence,
                            "role": "assistant",
                            "content": "",
                            "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                            "payload": {},
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    })
                await self._emit_event({
                    "type": "token",
                    "content": str(event.get("content") or ""),
                    "accumulated": str(event.get("accumulated") or ""),
                })
                return
            if event_type == "error":
                await self._emit_event({
                    "type": "error",
                    "message_text": str(event.get("user_message") or event.get("error") or "Streaming failed"),
                })

        try:
            if self._event_sink is not None:
                model_response = self._normalize_model_response(
                    await self._model_client.complete_stream(
                        system_prompt=effective_system_prompt,
                        recon_payload=snapshot.session.recon_payload or {},
                        transcript=prepared_messages,
                        model_name=model_name,
                        tool_definitions=tool_definitions,
                        max_output_tokens_override=state.max_output_tokens_override,
                        on_event=handle_model_stream_event,
                    )
                )
            else:
                model_response = self._normalize_model_response(
                    await self._model_client.complete(
                        system_prompt=effective_system_prompt,
                        recon_payload=snapshot.session.recon_payload or {},
                        transcript=prepared_messages,
                        model_name=model_name,
                        tool_definitions=tool_definitions,
                        max_output_tokens_override=state.max_output_tokens_override,
                    )
                )
        except asyncio.CancelledError:
            return self._finalize_terminal_result(
                session_id=session_id,
                turn_id=turn_id,
                state=state,
                messages=state.messages,
                stop_reason=RuntimeStopReason.ABORTED_STREAMING,
                status="aborted_streaming",
            )
        except Exception as exc:
            return self._finalize_terminal_result(
                session_id=session_id,
                turn_id=turn_id,
                state=state,
                messages=state.messages,
                stop_reason=RuntimeStopReason.MODEL_ERROR,
                status="model_error",
                checkpoint_extra={"phase": "model", "error": str(exc)},
            )

        assistant_message_id = None
        working_messages = list(state.messages)
        if model_response.content:
            assistant_item = TranscriptItem(
                role=RuntimeMessageRole.ASSISTANT,
                content=model_response.content,
                metadata={"streaming": assistant_stream_started} if assistant_stream_started else {},
                payload={"usage": dict(model_response.usage or {})} if model_response.usage else {},
            )
            assistant_message_id = self._session_store.append_message(session_id, assistant_item)
            working_messages.append(assistant_item)
            if self._event_sink is not None:
                assistant_message = self._session_store.get_message(assistant_message_id)
                if assistant_message is not None:
                    if not assistant_stream_started:
                        await self._emit_event({
                            "type": "assistant_start",
                            "message": {
                                "id": assistant_stream_placeholder_id,
                                "session_id": session_id,
                                "sequence": assistant_message.sequence,
                                "role": "assistant",
                                "content": "",
                                "metadata": {"kind": "direct_audit_assistant_message", "streaming": True},
                                "payload": {},
                                "created_at": assistant_message.created_at.isoformat(),
                            },
                        })
                        await self._emit_event({
                            "type": "token",
                            "content": assistant_message.content,
                            "accumulated": assistant_message.content,
                        })
                    await self._emit_event({
                        "type": "done",
                        "message": {
                            "id": assistant_message.id,
                            "session_id": assistant_message.session_id,
                            "sequence": assistant_message.sequence,
                            "role": assistant_message.role,
                            "content": assistant_message.content,
                            "metadata": dict(assistant_message.message_metadata or {}),
                            "payload": dict(assistant_message.payload or {}),
                            "created_at": assistant_message.created_at.isoformat(),
                        },
                        "usage": dict(model_response.usage or {}),
                    })

        raw_tool_calls = list(model_response.tool_calls or [])
        if not raw_tool_calls and model_response.content and tool_definitions and self._tool_orchestrator is not None:
            raw_tool_calls = self._extract_text_tool_calls(model_response.content)

        tool_requests = [
            ToolCallRequest(
                id=item.get("id") or f"tool-use-{index + 1}",
                name=item["name"],
                input=dict(item.get("input") or {}),
            )
            for index, item in enumerate(raw_tool_calls)
        ]
        tool_result_message_ids: list[str] = []
        tool_call_ids: list[str] = []
        stop_reason: RuntimeStopReason | None = None
        transition: RuntimeContinueReason | None = None
        checkpoint_extra: dict[str, object] | None = None

        if tool_requests:
            for request in tool_requests:
                tool_use_item = TranscriptItem(
                    role=RuntimeMessageRole.TOOL_USE,
                    content=request.name,
                    name=request.name,
                    payload={
                        "tool_use_id": request.id,
                        "tool_name": request.name,
                        "input": request.input,
                    },
                )
                self._session_store.append_message(session_id, tool_use_item)
                working_messages.append(tool_use_item)

            if self._tool_orchestrator is None:
                if tool_definitions:
                    raise RuntimeError("Tool calls were returned but no tool orchestrator is configured")
                return self._finalize_terminal_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    state=state,
                    messages=working_messages,
                    stop_reason=RuntimeStopReason.COMPLETED,
                    status="tool_calls_ignored",
                    assistant_message_id=assistant_message_id,
                    ignored_tool_calls=[request.name for request in tool_requests],
                )

            try:
                records = await self._tool_orchestrator.execute_tool_calls(
                    session_id=session_id,
                    turn_id=turn_id,
                    tool_calls=tool_requests,
                    session=snapshot.session,
                    recon_payload=snapshot.session.recon_payload or {},
                )
            except asyncio.CancelledError:
                return self._finalize_terminal_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    state=state,
                    messages=working_messages,
                    stop_reason=RuntimeStopReason.ABORTED_TOOLS,
                    status="aborted_tools",
                    assistant_message_id=assistant_message_id,
                    checkpoint_extra={"phase": "tool_execution"},
                )
            except Exception as exc:
                return self._finalize_terminal_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    state=state,
                    messages=working_messages,
                    stop_reason=RuntimeStopReason.MODEL_ERROR,
                    status="tool_execution_failed",
                    assistant_message_id=assistant_message_id,
                    checkpoint_extra={"phase": "tool_execution", "error": str(exc)},
                )
            for record in records:
                tool_call_ids.append(record.tool_call_id)
                tool_result_item = TranscriptItem(
                    role=RuntimeMessageRole.TOOL_RESULT,
                    content=record.result.content,
                    name=record.request.name,
                    metadata={
                        "status": record.status,
                        "is_error": record.result.is_error,
                        "duration_ms": record.duration_ms,
                    },
                    payload={
                        "tool_use_id": record.request.id,
                        "tool_call_id": record.tool_call_id,
                        "tool_name": record.request.name,
                        "input": record.request.input,
                        "output": record.result.output_payload,
                        "error_message": record.error_message,
                    },
                )
                tool_result_message_ids.append(self._session_store.append_message(session_id, tool_result_item))
                working_messages.append(tool_result_item)

            post_tool_snapshot = self._session_store.load_session_snapshot(session_id)
            turn_hook_events = collect_turn_hook_events(checkpoints=post_tool_snapshot.checkpoints, turn_id=turn_id)
            post_tool_hook = evaluate_post_tool_hooks(
                runtime_state=runtime_state,
                records=records,
                hook_events=turn_hook_events,
            )
            if inspect.isawaitable(post_tool_hook):
                post_tool_hook = await post_tool_hook
            post_tool_emitted_events = list(post_tool_hook.get("emitted_hook_events") or [])
            if post_tool_emitted_events:
                self._persist_runtime_hook_events(
                    session_id=session_id,
                    turn_id=turn_id,
                    hook_events=post_tool_emitted_events,
                )
                post_tool_artifacts = build_stop_hook_artifact_messages(post_tool_hook)
                if post_tool_artifacts:
                    self._append_transcript_items(session_id=session_id, items=post_tool_artifacts)
                    working_messages.extend(post_tool_artifacts)
            if post_tool_hook.get("hook_stopped"):
                return self._finalize_terminal_result(
                    session_id=session_id,
                    turn_id=turn_id,
                    state=state,
                    messages=working_messages,
                    stop_reason=RuntimeStopReason.HOOK_STOPPED,
                    status="hook_stopped",
                    assistant_message_id=assistant_message_id,
                    tool_call_ids=tool_call_ids,
                    tool_result_message_ids=tool_result_message_ids,
                    checkpoint_extra={"hook_stop_reason": post_tool_hook.get("stop_reason")},
                )

            attachment_messages = build_between_turn_attachments(
                state=state,
                records=records,
                session_snapshot=post_tool_snapshot,
            )
            pending_tool_use_summary = start_pending_tool_use_summary(
                state=state,
                records=records,
                session_snapshot=post_tool_snapshot,
            )
            transition = RuntimeContinueReason.NEXT_TURN
            next_messages = [*working_messages, *list(attachment_messages or [])]
            next_state = build_continue_state(state, messages=next_messages, transition=transition)
            next_state.pending_tool_use_summary = pending_tool_use_summary
            self._session_store.save_query_loop_state(session_id, next_state)
            self._session_store.close_turn(turn_id, status="tool_results_ready")
        else:
            degradation = await handle_recoverable_response(
                state=state,
                working_messages=working_messages,
                model_response=model_response,
                model=str(getattr(snapshot.session, "model_name", "") or state.tool_use_context.get("main_loop_model") or ""),
                model_client=self._model_client,
            )
            if degradation is not None and degradation.next_state is not None:
                transition = degradation.next_state.transition
                checkpoint_extra = dict(degradation.checkpoint_payload or {})
                self._session_store.save_query_loop_state(session_id, degradation.next_state)
                self._session_store.close_turn(turn_id, status="recovery_pending")
                self._write_checkpoint(
                    session_id=session_id,
                    turn_id=turn_id,
                    stop_reason=None,
                    transition=transition,
                    assistant_message_id=assistant_message_id,
                    tool_call_ids=[],
                    extra_state_payload=checkpoint_extra,
                )
                return TurnExecutionResult(
                    turn_id=turn_id,
                    stop_reason=None,
                    assistant_message_id=assistant_message_id,
                    tool_call_ids=[],
                    tool_result_message_ids=[],
                    transition=transition,
                )
            if degradation is not None and degradation.stop_reason is not None:
                stop_reason = degradation.stop_reason
            else:
                stop_hook_result = evaluate_stop_hooks(
                    runtime_state=runtime_state,
                    messages=working_messages,
                    model_response=model_response,
                    hook_events=collect_turn_hook_events(checkpoints=self._session_store.load_session_snapshot(session_id).checkpoints, turn_id=turn_id),
                )
                if inspect.isawaitable(stop_hook_result):
                    stop_hook_result = await stop_hook_result
                emitted_hook_events = list(stop_hook_result.get("emitted_hook_events") or [])
                if emitted_hook_events:
                    self._persist_runtime_hook_events(
                        session_id=session_id,
                        turn_id=turn_id,
                        hook_events=emitted_hook_events,
                    )
                artifact_messages = build_stop_hook_artifact_messages(stop_hook_result)
                if artifact_messages:
                    self._append_transcript_items(session_id=session_id, items=artifact_messages)
                    working_messages.extend(artifact_messages)
                blocking_errors = list(stop_hook_result.get("blocking_errors") or [])
                if blocking_errors:
                    transition = RuntimeContinueReason.STOP_HOOK_BLOCKING
                    next_messages = [*working_messages, *build_stop_hook_messages(blocking_errors)]
                    next_state = build_continue_state(state, messages=next_messages, transition=transition)
                    next_state.stop_hook_active = True
                    self._session_store.save_query_loop_state(session_id, next_state)
                    self._session_store.close_turn(turn_id, status="stop_hook_blocking")
                    self._write_checkpoint(
                        session_id=session_id,
                        turn_id=turn_id,
                        stop_reason=None,
                        transition=transition,
                        assistant_message_id=assistant_message_id,
                        tool_call_ids=[],
                    )
                    return TurnExecutionResult(
                        turn_id=turn_id,
                        stop_reason=None,
                        assistant_message_id=assistant_message_id,
                        tool_call_ids=[],
                        tool_result_message_ids=[],
                        transition=transition,
                    )
                if stop_hook_result.get("prevent_continuation"):
                    stop_reason = RuntimeStopReason.STOP_HOOK_PREVENTED
                else:
                    token_budget_result = evaluate_token_budget_continuation(
                        runtime_state=runtime_state,
                        state=state,
                        model_response=model_response,
                    )
                    if token_budget_result.get("should_continue"):
                        transition = RuntimeContinueReason.TOKEN_BUDGET_CONTINUATION
                        nudge_message = TranscriptItem(
                            role=RuntimeMessageRole.USER,
                            content=str(token_budget_result.get("message") or "").strip(),
                            name="token_budget_nudge",
                            metadata={"synthetic": True, "kind": "token_budget_continuation"},
                        )
                        next_state = build_continue_state(state, messages=[*working_messages, nudge_message], transition=transition)
                        self._session_store.save_query_loop_state(session_id, next_state)
                        self._session_store.close_turn(turn_id, status="token_budget_continuation")
                        self._write_checkpoint(
                            session_id=session_id,
                            turn_id=turn_id,
                            stop_reason=None,
                            transition=transition,
                            assistant_message_id=assistant_message_id,
                            tool_call_ids=[],
                        )
                        return TurnExecutionResult(
                            turn_id=turn_id,
                            stop_reason=None,
                            assistant_message_id=assistant_message_id,
                            tool_call_ids=[],
                            tool_result_message_ids=[],
                            transition=transition,
                        )
                    stop_reason = self._coerce_stop_reason(model_response.stop_reason)
            return self._finalize_terminal_result(
                session_id=session_id,
                turn_id=turn_id,
                state=state,
                messages=working_messages,
                stop_reason=stop_reason,
                status="completed",
                assistant_message_id=assistant_message_id,
                checkpoint_extra=checkpoint_extra,
            )

        self._write_checkpoint(
            session_id=session_id,
            turn_id=turn_id,
            stop_reason=stop_reason,
            transition=transition,
            assistant_message_id=assistant_message_id,
            tool_call_ids=tool_call_ids,
            extra_state_payload=checkpoint_extra,
        )
        return TurnExecutionResult(
            turn_id=turn_id,
            stop_reason=stop_reason,
            assistant_message_id=assistant_message_id,
            tool_call_ids=tool_call_ids,
            tool_result_message_ids=tool_result_message_ids,
            transition=transition,
        )

    @staticmethod
    def _extract_auto_compact_tracking(state: QueryLoopState) -> AutoCompactTrackingState | None:
        tracking = dict(state.auto_compact_tracking or {})
        if not tracking:
            return None
        return AutoCompactTrackingState(
            compacted=bool(tracking.get("compacted") or False),
            turn_counter=int(tracking.get("turn_counter") or state.turn_count),
            turn_id=str(tracking.get("turn_id") or f"turn-{state.turn_count}"),
            consecutive_failures=(
                int(tracking.get("consecutive_failures"))
                if tracking.get("consecutive_failures") is not None
                else None
            ),
        )

    @staticmethod
    def _apply_auto_compact_decision(
        *,
        state: QueryLoopState,
        prepared_messages: list[TranscriptItem],
        decision,
    ) -> tuple[list[TranscriptItem], QueryLoopState]:
        tracking = dict(state.auto_compact_tracking or {})
        if getattr(decision, "consecutive_failures", None) is not None:
            tracking["consecutive_failures"] = decision.consecutive_failures
        next_messages = list(prepared_messages)
        if getattr(decision, "was_compacted", False) and getattr(decision, "compaction_result", None) is not None:
            result = decision.compaction_result
            next_messages = build_post_compact_messages(result)
            tracking.update({
                "compacted": True,
                "turn_counter": int(state.turn_count),
                "turn_id": f"turn-{state.turn_count}",
                "consecutive_failures": 0,
                "boundary_name": result.boundary_marker.name,
                "summary_message_name": result.summary_messages[-1].name if result.summary_messages else None,
            })
            for key in ("auto_compact_threshold", "effective_context_window"):
                if result.boundary_marker.metadata.get(key) is not None:
                    tracking[key] = result.boundary_marker.metadata.get(key)
        return next_messages, QueryLoopState(
            messages=list(next_messages),
            tool_use_context=dict(state.tool_use_context),
            auto_compact_tracking=tracking or None,
            context_collapse_state=dict(state.context_collapse_state or {}) if state.context_collapse_state is not None else None,
            max_output_tokens_recovery_count=state.max_output_tokens_recovery_count,
            has_attempted_reactive_compact=state.has_attempted_reactive_compact,
            max_output_tokens_override=state.max_output_tokens_override,
            pending_tool_use_summary=dict(state.pending_tool_use_summary or {}) if state.pending_tool_use_summary is not None else None,
            stop_hook_active=state.stop_hook_active,
            turn_count=state.turn_count,
            transition=state.transition,
        )

    def _load_query_loop_state(self, *, session_id: str, snapshot) -> QueryLoopState:
        state = self._session_store.load_query_loop_state(session_id)
        if state.messages:
            return state
        transcript = [
            TranscriptItem(
                role=RuntimeMessageRole(message.role),
                content=message.content,
                name=message.name,
                metadata=dict(message.message_metadata or {}),
                payload=dict(message.payload or {}),
            )
            for message in snapshot.messages
        ]
        if snapshot.turns:
            state.turn_count = len(snapshot.turns) + 1
        return hydrate_query_loop_state(state, messages=transcript)


    @staticmethod
    def _merge_runtime_query_context_pipeline(state: QueryLoopState, runtime_state) -> QueryLoopState:
        query_context = dict(getattr(runtime_state, "metadata", {}).get("query_context") or {})
        pipeline = dict(query_context.get("pipeline") or {})
        if not pipeline:
            return state
        merged_tool_use_context = dict(state.tool_use_context or {})
        existing_pipeline = dict(merged_tool_use_context.get("query_context_pipeline") or {})
        for key, value in pipeline.items():
            if isinstance(value, dict) and isinstance(existing_pipeline.get(key), dict):
                merged_value = dict(value)
                merged_value.update(existing_pipeline.get(key) or {})
                existing_pipeline[key] = merged_value
            else:
                existing_pipeline.setdefault(key, value)
        merged_tool_use_context["query_context_pipeline"] = existing_pipeline
        state.tool_use_context = merged_tool_use_context
        return state
    @staticmethod
    def _normalize_model_response(response) -> RuntimeModelResponse:
        if isinstance(response, RuntimeModelResponse):
            return response
        payload = dict(response or {})
        return RuntimeModelResponse(
            content=str(payload.get("content") or ""),
            tool_calls=list(payload.get("tool_calls") or []),
            stop_reason=str(payload.get("stop_reason")) if payload.get("stop_reason") is not None else None,
            recoverable_error_kind=str(payload.get("recoverable_error_kind")) if payload.get("recoverable_error_kind") else None,
            recoverable_error_message=str(payload.get("recoverable_error_message")) if payload.get("recoverable_error_message") else None,
            usage=dict(payload.get("usage") or {}),
        )

    def _finalize_terminal_result(
        self,
        *,
        session_id: str,
        turn_id: str,
        state: QueryLoopState,
        messages: list[TranscriptItem],
        stop_reason: RuntimeStopReason,
        status: str,
        assistant_message_id: str | None = None,
        tool_call_ids: list[str] | None = None,
        tool_result_message_ids: list[str] | None = None,
        ignored_tool_calls: list[str] | None = None,
        checkpoint_extra: dict[str, object] | None = None,
    ) -> TurnExecutionResult:
        terminal_state = build_terminal_state(state, messages=messages)
        terminal_state.pending_tool_use_summary = state.pending_tool_use_summary
        self._session_store.save_query_loop_state(session_id, terminal_state)
        self._session_store.close_turn(turn_id, status=status)
        self._write_checkpoint(
            session_id=session_id,
            turn_id=turn_id,
            stop_reason=stop_reason,
            transition=None,
            assistant_message_id=assistant_message_id,
            tool_call_ids=list(tool_call_ids or []),
            ignored_tool_calls=ignored_tool_calls,
            extra_state_payload=checkpoint_extra,
        )
        return TurnExecutionResult(
            turn_id=turn_id,
            stop_reason=stop_reason,
            assistant_message_id=assistant_message_id,
            tool_call_ids=list(tool_call_ids or []),
            tool_result_message_ids=list(tool_result_message_ids or []),
            transition=None,
        )

    def _append_transcript_items(
        self,
        *,
        session_id: str,
        items: list[TranscriptItem],
    ) -> None:
        for item in items:
            self._session_store.append_message(session_id, item)

    async def _emit_event(self, event: dict[str, object]) -> None:
        if self._event_sink is None:
            return
        maybe_awaitable = self._event_sink(event)
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

    def _persist_runtime_hook_events(
        self,
        *,
        session_id: str,
        turn_id: str,
        hook_events: list[dict[str, object]],
    ) -> None:
        for event in hook_events:
            payload = {"kind": "runtime_hook", **dict(event or {})}
            self._session_store.create_checkpoint(
                session_id=session_id,
                turn_id=turn_id,
                checkpoint_type=AuditCheckpointType.AUTO,
                state_payload=payload,
            )

    def _write_checkpoint(
        self,
        *,
        session_id: str,
        turn_id: str,
        stop_reason: RuntimeStopReason | None,
        transition: RuntimeContinueReason | None,
        assistant_message_id: str | None,
        tool_call_ids: list[str],
        ignored_tool_calls: list[str] | None = None,
        extra_state_payload: dict[str, object] | None = None,
    ) -> None:
        payload = {
            "stop_reason": stop_reason.value if stop_reason is not None else None,
            "transition": transition.value if transition is not None else None,
            "assistant_message_id": assistant_message_id,
            "tool_call_ids": list(tool_call_ids),
        }
        if ignored_tool_calls is not None:
            payload["ignored_tool_calls"] = list(ignored_tool_calls)
        if extra_state_payload:
            payload.update(dict(extra_state_payload))
        self._session_store.create_checkpoint(
            session_id=session_id,
            turn_id=turn_id,
            checkpoint_type=AuditCheckpointType.AUTO,
            state_payload=payload,
        )

    @staticmethod
    def _coerce_stop_reason(value: str | None) -> RuntimeStopReason:
        if not value:
            return RuntimeStopReason.COMPLETED
        normalized = str(value).strip()
        aliases = {
            "assistant_turn_complete": RuntimeStopReason.COMPLETED,
            "user_follow_up_required": RuntimeStopReason.COMPLETED,
            "model_stop": RuntimeStopReason.COMPLETED,
            "tool_execution_continue": RuntimeStopReason.COMPLETED,
            "max_turns_exceeded": RuntimeStopReason.MAX_TURNS,
            "stop": RuntimeStopReason.COMPLETED,
        }
        if normalized in aliases:
            return aliases[normalized]
        try:
            return RuntimeStopReason(normalized)
        except ValueError:
            return RuntimeStopReason.COMPLETED

    @staticmethod
    def _extract_text_tool_calls(content: str) -> list[dict[str, object]]:
        text = (content or '').strip()
        if not text:
            return []

        tool_call_match = re.search(r'Tool Call:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$', text, re.DOTALL)
        if tool_call_match:
            tool_name = tool_call_match.group(1).strip()
            payload_text = tool_call_match.group(2).strip()
            parsed_payload = AgentJsonParser.parse_any(payload_text, default={})
            if isinstance(parsed_payload, dict) and isinstance(parsed_payload.get('input'), dict):
                tool_input = dict(parsed_payload.get('input') or {})
            elif isinstance(parsed_payload, dict):
                tool_input = parsed_payload
            else:
                tool_input = {}
            return [{'id': 'text-tool-call-1', 'name': tool_name, 'input': tool_input}]

        action_match = re.search(r'Action:\s*([A-Za-z_][A-Za-z0-9_]*)\s*Action Input:\s*(.*)$', text, re.DOTALL)
        if action_match:
            tool_name = action_match.group(1).strip()
            parsed_payload = AgentJsonParser.parse_any(action_match.group(2).strip(), default={})
            if isinstance(parsed_payload, dict):
                return [{'id': 'text-tool-call-1', 'name': tool_name, 'input': dict(parsed_payload)}]
        return []











