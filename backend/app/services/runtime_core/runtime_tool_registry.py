from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.agent.tools.ask_user_runtime_tool import AskUserRuntimeTool
from app.services.agent.tools.base import AgentTool
from app.services.agent.tools.plan_mode_runtime_tool import EnterPlanModeRuntimeTool, ExitPlanModeRuntimeTool
from app.services.agent.tools.todo_runtime_tool import TodoWriteRuntimeTool
from app.services.finding_runtime.models import ToolExecutionPayload
from app.services.finding_runtime.skills import RuntimeSkillTool
from app.services.runtime_core.shell_runtime_tools import (
    BashRuntimeTool,
    PowerShellRuntimeTool,
    detect_bash_executable,
    detect_powershell_executable,
    is_powershell_runtime_tool_enabled,
)
from app.services.runtime_core.tool_runtime import RuntimeTool, ToolExecutionContext, ToolRegistry
from app.services.runtime_core.tool_search_runtime import ToolSearchRuntimeTool


class ReadToolInput(BaseModel):
    file_path: str | None = Field(default=None, description="Path to a file relative to the project root.")
    file_paths: list[str] = Field(default_factory=list, description="Optional batch of related files to read together.")
    start_line: int | None = Field(default=None, description="Optional 1-based start line.")
    end_line: int | None = Field(default=None, description="Optional inclusive end line.")
    max_lines: int = Field(default=400, description="Maximum lines to return per file.")
    max_files: int = Field(default=6, description="Maximum files when batch reading.")


class GlobToolInput(BaseModel):
    path: str = Field(default=".", description="Directory relative to the project root.")
    pattern: str | None = Field(default=None, description="Optional glob pattern, for example **/*.java or *.xml.")
    recursive: bool = Field(default=True, description="Whether to walk child directories.")
    max_results: int = Field(default=120, description="Maximum files to return.")


class GrepToolInput(BaseModel):
    pattern: str = Field(description="Keyword or regular expression to search for.")
    path: str | None = Field(default=None, description="Optional directory relative to the project root.")
    glob: str | None = Field(default=None, description="Optional glob such as *.py or **/*.java.")
    case_sensitive: bool = Field(default=False, description="Whether the search is case sensitive.")
    max_results: int = Field(default=80, description="Maximum number of matches to return.")
    is_regex: bool = Field(default=False, description="Whether pattern should be treated as regex.")


def _result_to_payload(result: Any) -> ToolExecutionPayload:
    output_payload = result.to_dict()
    return ToolExecutionPayload(
        content=result.to_string(),
        output_payload=output_payload,
        metadata={"success": result.success, **(result.metadata or {})},
        is_error=not result.success,
    )


class CanonicalReadTool(RuntimeTool):
    name = "Read"
    description = (
        "Read one file or a small batch of closely related files from the project. "
        "Prefer this for controllers, services, config, SQL, XML, and skill reference files."
    )
    input_model = ReadToolInput

    def __init__(self, *, read_tool: AgentTool | None, read_many_tool: AgentTool | None = None):
        self._read_tool = read_tool
        self._read_many_tool = read_many_tool

    def validate_input(self, raw_input: dict[str, Any]) -> ReadToolInput:
        payload = dict(raw_input or {})
        normalized = {
            "file_path": payload.get("file_path") or payload.get("path") or payload.get("file"),
            "file_paths": list(payload.get("file_paths") or payload.get("paths") or []),
            "start_line": payload.get("start_line") or payload.get("from_line"),
            "end_line": payload.get("end_line") or payload.get("to_line"),
            "max_lines": payload.get("max_lines") or payload.get("limit") or 400,
            "max_files": payload.get("max_files") or 6,
        }
        if not normalized["file_path"] and normalized["file_paths"]:
            normalized["file_path"] = normalized["file_paths"][0]
        return ReadToolInput.model_validate(normalized)

    def is_concurrency_safe(self, parsed_input: Any) -> bool:
        return True

    async def execute(self, parsed_input: ReadToolInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        del context
        file_paths = [item for item in parsed_input.file_paths if str(item or "").strip()]
        if len(file_paths) > 1:
            if self._read_many_tool is None:
                raise ValueError("Batch file reading is not available in this runtime.")
            result = await self._read_many_tool.execute(
                file_paths=file_paths,
                start_line=parsed_input.start_line,
                end_line=parsed_input.end_line,
                max_lines=parsed_input.max_lines,
                max_files=parsed_input.max_files,
            )
            return _result_to_payload(result)

        if self._read_tool is None or not parsed_input.file_path:
            raise ValueError("Read requires file_path or file_paths.")
        result = await self._read_tool.execute(
            file_path=parsed_input.file_path,
            start_line=parsed_input.start_line,
            end_line=parsed_input.end_line,
            max_lines=parsed_input.max_lines,
        )
        return _result_to_payload(result)


class CanonicalGlobTool(RuntimeTool):
    name = "Glob"
    description = "List files under the project root with an optional glob filter."
    input_model = GlobToolInput

    def __init__(self, *, list_tool: AgentTool):
        self._list_tool = list_tool

    def validate_input(self, raw_input: dict[str, Any]) -> GlobToolInput:
        payload = dict(raw_input or {})
        normalized = {
            "path": payload.get("path") or payload.get("directory") or ".",
            "pattern": payload.get("pattern") or payload.get("glob"),
            "recursive": payload.get("recursive", True),
            "max_results": payload.get("max_results") or payload.get("max_files") or 120,
        }
        return GlobToolInput.model_validate(normalized)

    def is_concurrency_safe(self, parsed_input: Any) -> bool:
        return True

    async def execute(self, parsed_input: GlobToolInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        del context
        result = await self._list_tool.execute(
            directory=parsed_input.path,
            pattern=parsed_input.pattern,
            recursive=parsed_input.recursive,
            max_files=parsed_input.max_results,
        )
        return _result_to_payload(result)


class CanonicalGrepTool(RuntimeTool):
    name = "Grep"
    description = "Search code or config text across the repository with regex or keyword matching."
    input_model = GrepToolInput

    def __init__(self, *, search_tool: AgentTool):
        self._search_tool = search_tool

    def validate_input(self, raw_input: dict[str, Any]) -> GrepToolInput:
        payload = dict(raw_input or {})
        normalized = {
            "pattern": payload.get("pattern") or payload.get("query") or payload.get("keyword"),
            "path": payload.get("path") or payload.get("directory"),
            "glob": payload.get("glob") or payload.get("file_pattern"),
            "case_sensitive": payload.get("case_sensitive", False),
            "max_results": payload.get("max_results") or payload.get("limit") or 80,
            "is_regex": payload.get("is_regex", False),
        }
        return GrepToolInput.model_validate(normalized)

    def is_concurrency_safe(self, parsed_input: Any) -> bool:
        return True

    async def execute(self, parsed_input: GrepToolInput, context: ToolExecutionContext) -> ToolExecutionPayload:
        del context
        result = await self._search_tool.execute(
            keyword=parsed_input.pattern,
            file_pattern=parsed_input.glob,
            directory=parsed_input.path,
            case_sensitive=parsed_input.case_sensitive,
            max_results=parsed_input.max_results,
            is_regex=parsed_input.is_regex,
        )
        return _result_to_payload(result)


def _infer_project_root(agent_tools: dict[str, AgentTool]) -> str | None:
    for key in ("read_file", "list_files", "search_code"):
        tool = agent_tools.get(key)
        project_root = getattr(tool, "project_root", None)
        if isinstance(project_root, str) and project_root.strip():
            return project_root
    return None


def build_runtime_tool_registry(*, session_store, agent_tools: dict[str, AgentTool], agent_type: str, user_id: str | None = None) -> ToolRegistry:
    tools: list[RuntimeTool] = []

    read_tool = agent_tools.get("read_file")
    if isinstance(read_tool, AgentTool):
        read_many_tool = agent_tools.get("read_many_files")
        tools.append(
            CanonicalReadTool(
                read_tool=read_tool,
                read_many_tool=read_many_tool if isinstance(read_many_tool, AgentTool) else None,
            )
        )

    list_tool = agent_tools.get("list_files")
    if isinstance(list_tool, AgentTool):
        tools.append(CanonicalGlobTool(list_tool=list_tool))

    search_tool = agent_tools.get("search_code")
    if isinstance(search_tool, AgentTool):
        tools.append(CanonicalGrepTool(search_tool=search_tool))

    project_root = _infer_project_root(agent_tools)
    shell_backend = agent_tools.get("sandbox_exec") if isinstance(agent_tools.get("sandbox_exec"), AgentTool) else None
    if project_root:
        bash_executable = detect_bash_executable()
        if bash_executable or shell_backend is not None:
            tools.append(BashRuntimeTool(project_root=project_root, backend_tool=shell_backend, executable=bash_executable))
        if is_powershell_runtime_tool_enabled():
            powershell_executable = detect_powershell_executable()
            if powershell_executable or shell_backend is not None:
                tools.append(PowerShellRuntimeTool(project_root=project_root, backend_tool=shell_backend, executable=powershell_executable))

    tools.append(
        RuntimeSkillTool(
            session_store=session_store,
            agent_type=agent_type,
            user_id=user_id,
        )
    )
    tools.extend(
        [
            TodoWriteRuntimeTool(session_store),
            AskUserRuntimeTool(session_store),
            EnterPlanModeRuntimeTool(session_store),
            ExitPlanModeRuntimeTool(session_store),
        ]
    )

    registry = ToolRegistry(tools)
    if registry.has_deferred_tools():
        registry.register(ToolSearchRuntimeTool(session_store=session_store, registry_getter=lambda: registry))
    return registry
