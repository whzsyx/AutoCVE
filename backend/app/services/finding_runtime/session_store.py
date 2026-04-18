from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.models.audit_session import (
    AuditCheckpoint,
    AuditCheckpointType,
    AuditHandoff,
    AuditMemory,
    AuditSession,
    AuditSessionMessage,
    AuditSessionTurn,
    AuditSkill,
    AuditSkillInvocation,
    AuditToolCall,
)
from app.services.finding_runtime.models import RuntimeMemoryRecord, RuntimeSessionSnapshot, RuntimeSessionState, TranscriptItem
from app.services.finding_runtime.query_state import QueryLoopState
from app.services.runtime_core.session_state import SessionRuntimeState as SharedSessionRuntimeState


class AuditSessionStore:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def create_session(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        runtime_stack: str = "legacy",
        system_prompt: str | None = None,
        recon_payload: dict | None = None,
    ) -> str:
        with self._session_factory() as db:
            session = AuditSession(
                project_id=project_id,
                task_id=task_id,
                runtime_stack=runtime_stack,
                state=RuntimeSessionState.PENDING.value,
                system_prompt=system_prompt,
                recon_payload=recon_payload or {},
                runtime_state_json={},
            )
            db.add(session)
            db.commit()
            db.refresh(session)
            return session.id

    def update_session_state(self, session_id: str, state: RuntimeSessionState | str) -> None:
        with self._session_factory() as db:
            session = db.get(AuditSession, session_id)
            if session is None:
                raise LookupError(f"Unknown audit session: {session_id}")
            session.state = state.value if isinstance(state, RuntimeSessionState) else str(state)
            db.commit()

    def update_system_prompt(self, session_id: str, system_prompt: str | None) -> None:
        with self._session_factory() as db:
            session = db.get(AuditSession, session_id)
            if session is None:
                raise LookupError(f"Unknown audit session: {session_id}")
            session.system_prompt = system_prompt
            db.commit()

    def replace_runtime_state(self, session_id: str, runtime_state: SharedSessionRuntimeState | dict) -> None:
        payload = runtime_state.model_dump(mode="json") if isinstance(runtime_state, SharedSessionRuntimeState) else dict(runtime_state or {})
        payload["session_id"] = session_id
        with self._session_factory() as db:
            session = db.get(AuditSession, session_id)
            if session is None:
                raise LookupError(f"Unknown audit session: {session_id}")
            session.runtime_state_json = payload
            db.commit()

    def load_runtime_state(self, session_id: str) -> SharedSessionRuntimeState:
        with self._session_factory() as db:
            session = db.get(AuditSession, session_id)
            if session is None:
                raise LookupError(f"Unknown audit session: {session_id}")
            payload = dict(session.runtime_state_json or {})
            payload["session_id"] = session_id
            return SharedSessionRuntimeState.model_validate(payload)

    def save_query_loop_state(self, session_id: str, state: QueryLoopState) -> None:
        runtime_state = self.load_runtime_state(session_id)
        runtime_state.metadata["query_loop"] = state.to_payload()
        self.replace_runtime_state(session_id, runtime_state)

    def load_query_loop_state(self, session_id: str) -> QueryLoopState:
        runtime_state = self.load_runtime_state(session_id)
        return QueryLoopState.from_payload(runtime_state.metadata.get("query_loop") or {})

    def append_message(self, session_id: str, item: TranscriptItem) -> str:
        with self._session_factory() as db:
            sequence = self._next_sequence(db, AuditSessionMessage, AuditSessionMessage.session_id, session_id)
            message = AuditSessionMessage(
                session_id=session_id,
                sequence=sequence,
                role=item.role.value,
                content=item.content,
                name=item.name,
                message_metadata=dict(item.metadata),
                payload=dict(item.payload),
            )
            db.add(message)
            db.commit()
            db.refresh(message)
            return message.id

    def get_message(self, message_id: str) -> AuditSessionMessage | None:
        with self._session_factory() as db:
            return db.get(AuditSessionMessage, message_id)

    def open_turn(self, session_id: str, *, model_name: str | None = None) -> str:
        with self._session_factory() as db:
            sequence = self._next_sequence(db, AuditSessionTurn, AuditSessionTurn.session_id, session_id)
            turn = AuditSessionTurn(session_id=session_id, sequence=sequence, model_name=model_name)
            db.add(turn)
            db.commit()
            db.refresh(turn)
            return turn.id

    def close_turn(self, turn_id: str, *, status: str = "completed") -> None:
        with self._session_factory() as db:
            turn = db.get(AuditSessionTurn, turn_id)
            if turn is None:
                raise LookupError(f"Unknown audit session turn: {turn_id}")
            turn.status = status
            db.commit()

    def replace_skills(self, session_id: str, skills: list[dict], matched_skill_refs: set[str] | None = None) -> None:
        matched_skill_refs = matched_skill_refs or set()
        with self._session_factory() as db:
            db.execute(delete(AuditSkill).where(AuditSkill.session_id == session_id))
            for item in skills:
                skill_ref = str(item.get("slug") or item.get("id") or item.get("name") or "").strip()
                if not skill_ref:
                    continue
                db.add(
                    AuditSkill(
                        session_id=session_id,
                        skill_ref=skill_ref,
                        name=str(item.get("name") or skill_ref),
                        description=str(item.get("description") or "") or None,
                        source_type=str(item.get("source_type") or "") or None,
                        enabled=True,
                        matched=skill_ref in matched_skill_refs,
                        skill_metadata=dict(item),
                    )
                )
            db.commit()

    def list_skills(self, session_id: str) -> list[AuditSkill]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(AuditSkill)
                    .where(AuditSkill.session_id == session_id)
                    .order_by(AuditSkill.created_at)
                )
            )

    def replace_memories(self, session_id: str, memories: list[RuntimeMemoryRecord]) -> None:
        with self._session_factory() as db:
            db.execute(delete(AuditMemory).where(AuditMemory.session_id == session_id))
            for index, item in enumerate(memories, start=1):
                db.add(
                    AuditMemory(
                        session_id=session_id,
                        sequence=index,
                        memory_kind=item.memory_kind,
                        title=item.title,
                        source_type=item.source_type,
                        source_ref=item.source_ref,
                        content=item.content,
                        relevance_score=item.relevance_score,
                        metadata_json=dict(item.metadata),
                    )
                )
            db.commit()

    def list_memories(self, session_id: str) -> list[AuditMemory]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(AuditMemory)
                    .where(AuditMemory.session_id == session_id)
                    .order_by(AuditMemory.sequence)
                )
            )

    def create_handoff(self, *, session_id: str, target: str, status: str = "pending", payload: dict | None = None) -> str:
        with self._session_factory() as db:
            handoff_payload = dict(payload or {})
            handoff = AuditHandoff(
                session_id=session_id,
                target=target,
                status=status,
                payload=handoff_payload,
            )
            db.add(handoff)
            db.flush()

            sequence = self._next_sequence(db, AuditSessionMessage, AuditSessionMessage.session_id, session_id)
            db.add(
                AuditSessionMessage(
                    session_id=session_id,
                    sequence=sequence,
                    role="handoff",
                    content=str(handoff_payload.get("summary") or f"Handoff queued for {target}."),
                    name="verification_handoff",
                    message_metadata={"kind": "handoff", "status": status},
                    payload={"handoff_id": handoff.id, "target": target, **handoff_payload},
                )
            )
            db.commit()
            db.refresh(handoff)
            return handoff.id

    def list_handoffs(self, session_id: str) -> list[AuditHandoff]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(AuditHandoff)
                    .where(AuditHandoff.session_id == session_id)
                    .order_by(AuditHandoff.created_at)
                )
            )

    def start_skill_invocation(
        self,
        *,
        session_id: str,
        turn_id: str,
        skill_ref: str,
        input_payload: dict,
    ) -> str:
        with self._session_factory() as db:
            sequence = self._next_sequence(db, AuditSkillInvocation, AuditSkillInvocation.session_id, session_id)
            invocation = AuditSkillInvocation(
                session_id=session_id,
                turn_id=turn_id,
                sequence=sequence,
                skill_ref=skill_ref,
                input_payload=dict(input_payload or {}),
                output_payload={},
            )
            db.add(invocation)
            db.commit()
            db.refresh(invocation)
            return invocation.id

    def complete_skill_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        output_payload: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._session_factory() as db:
            invocation = db.get(AuditSkillInvocation, invocation_id)
            if invocation is None:
                raise LookupError(f"Unknown audit skill invocation: {invocation_id}")
            invocation.status = status
            invocation.output_payload = dict(output_payload or {})
            invocation.error_message = error_message
            db.commit()

    def list_skill_invocations(self, session_id: str) -> list[AuditSkillInvocation]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(AuditSkillInvocation)
                    .where(AuditSkillInvocation.session_id == session_id)
                    .order_by(AuditSkillInvocation.sequence)
                )
            )

    def start_tool_call(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_use_id: str,
        tool_name: str,
        input_payload: dict,
        is_concurrency_safe: bool,
    ) -> str:
        with self._session_factory() as db:
            sequence = self._next_sequence(db, AuditToolCall, AuditToolCall.session_id, session_id)
            tool_call = AuditToolCall(
                session_id=session_id,
                turn_id=turn_id,
                sequence=sequence,
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                status="running",
                is_concurrency_safe=is_concurrency_safe,
                input_payload=dict(input_payload or {}),
            )
            db.add(tool_call)
            db.commit()
            db.refresh(tool_call)
            return tool_call.id

    def complete_tool_call(
        self,
        tool_call_id: str,
        *,
        status: str,
        output_payload: dict | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        with self._session_factory() as db:
            tool_call = db.get(AuditToolCall, tool_call_id)
            if tool_call is None:
                raise LookupError(f"Unknown audit tool call: {tool_call_id}")
            tool_call.status = status
            tool_call.output_payload = dict(output_payload or {})
            tool_call.error_message = error_message
            tool_call.duration_ms = duration_ms
            tool_call.completed_at = datetime.now(timezone.utc)
            db.commit()

    def list_tool_calls(self, session_id: str) -> list[AuditToolCall]:
        with self._session_factory() as db:
            return list(
                db.scalars(
                    select(AuditToolCall)
                    .where(AuditToolCall.session_id == session_id)
                    .order_by(AuditToolCall.sequence)
                )
            )

    def create_checkpoint(
        self,
        *,
        session_id: str,
        turn_id: str | None,
        checkpoint_type: AuditCheckpointType,
        state_payload: dict,
    ) -> str:
        with self._session_factory() as db:
            checkpoint = AuditCheckpoint(
                session_id=session_id,
                turn_id=turn_id,
                checkpoint_type=checkpoint_type.value,
                state_payload=state_payload,
            )
            db.add(checkpoint)
            db.commit()
            db.refresh(checkpoint)
            return checkpoint.id

    def load_session_snapshot(self, session_id: str) -> RuntimeSessionSnapshot:
        with self._session_factory() as db:
            session = db.get(AuditSession, session_id)
            if session is None:
                raise LookupError(f"Unknown audit session: {session_id}")

            messages = list(
                db.scalars(
                    select(AuditSessionMessage)
                    .where(AuditSessionMessage.session_id == session_id)
                    .order_by(AuditSessionMessage.sequence)
                )
            )
            turns = list(
                db.scalars(
                    select(AuditSessionTurn)
                    .where(AuditSessionTurn.session_id == session_id)
                    .order_by(AuditSessionTurn.sequence)
                )
            )
            checkpoints = list(
                db.scalars(
                    select(AuditCheckpoint)
                    .where(AuditCheckpoint.session_id == session_id)
                    .order_by(AuditCheckpoint.created_at)
                )
            )
            tool_calls = list(
                db.scalars(
                    select(AuditToolCall)
                    .where(AuditToolCall.session_id == session_id)
                    .order_by(AuditToolCall.sequence)
                )
            )
            skills = list(
                db.scalars(
                    select(AuditSkill)
                    .where(AuditSkill.session_id == session_id)
                    .order_by(AuditSkill.created_at)
                )
            )
            skill_invocations = list(
                db.scalars(
                    select(AuditSkillInvocation)
                    .where(AuditSkillInvocation.session_id == session_id)
                    .order_by(AuditSkillInvocation.sequence)
                )
            )
            memories = list(
                db.scalars(
                    select(AuditMemory)
                    .where(AuditMemory.session_id == session_id)
                    .order_by(AuditMemory.sequence)
                )
            )
            handoffs = list(
                db.scalars(
                    select(AuditHandoff)
                    .where(AuditHandoff.session_id == session_id)
                    .order_by(AuditHandoff.created_at)
                )
            )
            return RuntimeSessionSnapshot(
                session=session,
                messages=messages,
                turns=turns,
                checkpoints=checkpoints,
                tool_calls=tool_calls,
                skills=skills,
                skill_invocations=skill_invocations,
                memories=memories,
                handoffs=handoffs,
            )

    @staticmethod
    def _next_sequence(db, model, field, session_id: str) -> int:
        current = db.scalar(select(func.max(model.sequence)).where(field == session_id))
        return (current or 0) + 1


