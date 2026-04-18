from __future__ import annotations

from pydantic import BaseModel

from app.services.finding_runtime.models import ToolExecutionPayload
from app.services.runtime_core.interaction_runtime import InteractionRuntime
from app.services.runtime_core.tool_runtime import RuntimeTool, ToolExecutionContext


class PlanModeInput(BaseModel):
    reason: str | None = None


class EnterPlanModeRuntimeTool(RuntimeTool):
    name = "EnterPlanMode"
    description = "Enter shared plan mode for the current session"
    input_model = PlanModeInput
    should_defer = True
    search_hint = "enter plan mode"

    def __init__(self, session_store):
        super().__init__()
        self._session_store = session_store
        self._interaction_runtime = InteractionRuntime()

    async def execute(self, parsed_input: PlanModeInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        runtime_state = self._session_store.load_runtime_state(context.session_id)
        plan_state = self._interaction_runtime.enter_plan_mode(
            runtime_state,
            agent_type=context.agent_type,
            reason=parsed_input.reason,
        )
        self._session_store.replace_runtime_state(context.session_id, runtime_state)
        return ToolExecutionPayload(
            content="Plan mode enabled",
            output_payload={"plan_mode": plan_state},
            metadata={"interaction": "plan_mode_enter"},
        )


class ExitPlanModeRuntimeTool(RuntimeTool):
    name = "ExitPlanMode"
    description = "Exit shared plan mode for the current session"
    input_model = PlanModeInput
    should_defer = True
    search_hint = "exit plan mode"

    def __init__(self, session_store):
        super().__init__()
        self._session_store = session_store
        self._interaction_runtime = InteractionRuntime()

    async def execute(self, parsed_input: PlanModeInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        runtime_state = self._session_store.load_runtime_state(context.session_id)
        plan_state = self._interaction_runtime.exit_plan_mode(
            runtime_state,
            agent_type=context.agent_type,
            reason=parsed_input.reason,
        )
        self._session_store.replace_runtime_state(context.session_id, runtime_state)
        return ToolExecutionPayload(
            content="Plan mode disabled",
            output_payload={"plan_mode": plan_state},
            metadata={"interaction": "plan_mode_exit"},
        )
