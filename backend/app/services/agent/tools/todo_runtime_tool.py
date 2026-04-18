from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.finding_runtime.models import ToolExecutionPayload
from app.services.runtime_core.interaction_runtime import InteractionRuntime
from app.services.runtime_core.tool_runtime import RuntimeTool, ToolExecutionContext


class TodoWriteInput(BaseModel):
    title: str = Field(..., min_length=1)
    details: str | None = None


class TodoWriteRuntimeTool(RuntimeTool):
    name = "TodoWrite"
    description = "Create a runtime todo item for the current agent"
    input_model = TodoWriteInput
    should_defer = True
    search_hint = "record a todo or plan step"

    def __init__(self, session_store):
        super().__init__()
        self._session_store = session_store
        self._interaction_runtime = InteractionRuntime()

    async def execute(self, parsed_input: TodoWriteInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        runtime_state = self._session_store.load_runtime_state(context.session_id)
        todo = self._interaction_runtime.create_todo(
            runtime_state,
            agent_type=context.agent_type,
            title=parsed_input.title,
            details=parsed_input.details,
        )
        self._session_store.replace_runtime_state(context.session_id, runtime_state)
        return ToolExecutionPayload(
            content=f"Todo recorded: {todo['title']}",
            output_payload={"todo": todo},
            metadata={"interaction": "todo"},
        )
