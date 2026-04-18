from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services.agent.tools.base import AgentTool, ToolResult
from app.services.finding_runtime.session_store import AuditSessionStore
from app.services.runtime_core.runtime_tool_registry import build_runtime_tool_registry


class FakeAgentTool(AgentTool):
    def __init__(self, name: str):
        super().__init__()
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Tool {self._name}"

    async def _execute(self, **kwargs):
        return ToolResult(success=True, data=kwargs)


def build_store() -> AuditSessionStore:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return AuditSessionStore(session_factory=session_factory)


def test_runtime_tool_registry_builder_exposes_shared_runtime_tools_for_agent():
    store = build_store()
    registry = build_runtime_tool_registry(
        session_store=store,
        agent_tools={
            "read_file": FakeAgentTool("read_file"),
            "read_many_files": FakeAgentTool("read_many_files"),
            "list_files": FakeAgentTool("list_files"),
            "search_code": FakeAgentTool("search_code"),
        },
        agent_type="recon",
        user_id="user-1",
    )

    tool_names = [item["name"] for item in registry.describe_tools()]
    skill_tool = registry.get("Skill")

    assert "Read" in tool_names
    assert "Glob" in tool_names
    assert "Grep" in tool_names
    assert "Write" in tool_names
    assert "Skill" in tool_names
    assert "TodoWrite" in tool_names
    assert "AskUser" in tool_names
    assert "EnterPlanMode" in tool_names
    assert "ExitPlanMode" in tool_names
    assert skill_tool is not None
    assert getattr(skill_tool, "_agent_type") == "recon"
