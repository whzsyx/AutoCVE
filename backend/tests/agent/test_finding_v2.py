import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.agents.analysis_workflow import AnalysisWorkflowAgent
from app.services.agent.agents.base import AgentType
from app.services.agent.agents.finding import FindingAgent
from app.services.agent.agents.finding_skill_preloader import PreloadedSkillContext
from app.services.agent.agents.analysis_workflow import ToolInvocation
from app.services.agent.tools.base import AgentTool, ToolResult
from app.services.agent.tools.thinking_tool import ReflectTool, ThinkTool


class DummyWorkflowAgent(AnalysisWorkflowAgent):
    def __init__(self, tools=None):
        llm_service = MagicMock()
        llm_service.chat_completion_stream = MagicMock()
        super().__init__(
            name="Dummy",
            agent_type=AgentType.FINDING,
            llm_service=llm_service,
            tools=tools or {},
            event_emitter=MagicMock(),
            system_prompt="dummy",
            max_iterations=2,
        )

    def _build_initial_message(self, context):
        return "dummy"


class EchoTool(AgentTool):
    def __init__(self, tool_name: str):
        super().__init__()
        self._tool_name = tool_name

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return f"Echo tool {self._tool_name}"

    async def _execute(self, value: str = "", **kwargs) -> ToolResult:
        return ToolResult(success=True, data=f"{self._tool_name}:{value}")


class PathEchoTool(AgentTool):
    def __init__(self, tool_name: str):
        super().__init__()
        self._tool_name = tool_name

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return f"Path echo tool {self._tool_name}"

    async def _execute(self, file_path: str = "", file_paths=None, **kwargs) -> ToolResult:
        if file_paths:
            rendered = ",".join(file_paths)
        else:
            rendered = file_path
        return ToolResult(success=True, data=f"{self._tool_name}:{rendered}")


def test_analysis_workflow_parses_action_batch_payload():
    agent = DummyWorkflowAgent()

    step = agent._parse_llm_response(
        """Thought: inspect multiple directories first
Action Batch:
[
  {"action": "list_files", "action_input": {"directory": "src", "max_depth": 2}},
  {"action": "list_files", "action_input": {"directory": "config", "max_depth": 1}}
]"""
    )

    assert step.thought == "inspect multiple directories first"
    assert [item.action for item in step.actions] == ["list_files", "list_files"]
    assert step.actions[0].action_input == {"directory": "src", "max_depth": 2}
    assert step.actions[1].action_input == {"directory": "config", "max_depth": 1}


def test_analysis_workflow_parses_textual_structured_tool_calls():
    agent = DummyWorkflowAgent()

    step = agent._build_step_from_structured_response(
        'Tool Calls:\n- think({"category":"evaluation","thought":"Need one more pass"})',
        [],
    )

    assert [item.action for item in step.actions] == ["think"]
    assert step.actions[0].action_input == {
        "category": "evaluation",
        "thought": "Need one more pass",
    }


def test_analysis_workflow_synthesizes_thought_for_action_only_structured_response():
    agent = DummyWorkflowAgent()

    step = agent._build_step_from_structured_response(
        'Action: search_code\nAction Input: {"keyword":"glueSource","max_results":10}',
        [],
    )

    assert step.action == "search_code"
    assert step.thought == "Searching the codebase for 'glueSource'."


@pytest.mark.asyncio
async def test_analysis_workflow_emits_completion_events_for_fallback_results():
    agent = DummyWorkflowAgent()
    event_emitter = MagicMock()
    event_emitter.emit = AsyncMock()
    agent.event_emitter = event_emitter

    agent._build_fallback_result = MagicMock(
        return_value={
            "findings": [{"title": "fallback finding"}],
            "summary": "fallback summary",
        }
    )
    agent.stream_llm_call = AsyncMock(
        side_effect=[("", 0), ("", 0), ("", 0), ("", 0), ("", 0)]
    )

    result = await agent.run(
        {
            "project_info": {"name": "demo", "root": "."},
            "config": {},
            "task": "audit",
            "previous_results": {},
        }
    )

    assert result.success is True
    emitted_types = [
        call.args[0].event_type
        for call in event_emitter.emit.await_args_list
    ]
    assert "llm_complete" in emitted_types
    assert "info" in emitted_types


@pytest.mark.asyncio
async def test_analysis_workflow_run_executes_action_batch_and_aggregates_observation():
    agent = DummyWorkflowAgent(
        tools={
            "tool_a": EchoTool("tool_a"),
            "tool_b": EchoTool("tool_b"),
        }
    )

    responses = [
        (
            """Thought: gather two quick signals
Action Batch:
[
  {"action": "tool_a", "action_input": {"value": "alpha"}},
  {"action": "tool_b", "action_input": {"value": "beta"}}
]""",
            0,
        ),
        ('Final Answer: {"findings": [], "summary": "done"}', 0),
    ]

    agent.stream_llm_call = AsyncMock(side_effect=responses)

    result = await agent.run(
        {
            "project_info": {"name": "demo", "root": "."},
            "config": {},
            "task": "audit",
            "previous_results": {},
        }
    )

    assert result.success is True
    assert result.data["summary"] == "done"
    assert "tool_a:alpha" in result.data["steps"][0]["observation"]
    assert "tool_b:beta" in result.data["steps"][0]["observation"]


@pytest.mark.asyncio
async def test_finding_agent_prefers_structured_tool_calling_run(monkeypatch):
    llm_service = MagicMock()
    llm_service.chat_completion = AsyncMock(
        side_effect=[
            {
                "content": "先读取主技能文件。",
                "usage": {"total_tokens": 11},
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_bootstrap",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"file_path":"skill_library/code-audit-finding/SKILL.md"}',
                        },
                    }
                ],
            },
            {
                "content": "同时比对控制器和 mapper。",
                "usage": {"total_tokens": 13},
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_batch",
                        "type": "function",
                        "function": {
                            "name": "read_many_files",
                            "arguments": '{"file_paths":["src/controller.java","src/mapper.xml"]}',
                        },
                    }
                ],
            },
            {
                "content": '{"findings": [], "summary": "done"}',
                "usage": {"total_tokens": 7},
                "finish_reason": "stop",
                "tool_calls": [],
            },
        ]
    )
    llm_service.chat_completion_stream = AsyncMock(side_effect=AssertionError("legacy stream path should not be used"))

    agent = FindingAgent(
        llm_service=llm_service,
        tools={
            "read_file": PathEchoTool("read_file"),
            "read_many_files": PathEchoTool("read_many_files"),
        },
        event_emitter=MagicMock(),
    )

    monkeypatch.setattr(
        "app.services.agent.skill_service.SkillService.resolve_agent_skills",
        AsyncMock(
            return_value={
                "metadata": [
                    {
                        "slug": "code-audit-finding",
                        "paths": {"skill_file_path": "skill_library/code-audit-finding/SKILL.md"},
                    }
                ],
                "matched": [
                    {
                        "slug": "code-audit-finding",
                        "paths": {"skill_file_path": "skill_library/code-audit-finding/SKILL.md"},
                    }
                ],
                "route_plan": {"primary_skill": "code-audit-finding"},
            }
        ),
    )

    async def fake_preload(user_id, context):
        del user_id, context
        return PreloadedSkillContext(primary_skill_name="code-audit-finding")

    monkeypatch.setattr(agent._skill_preloader, "preload", fake_preload)

    result = await agent.run(
        {
            "project_info": {"name": "demo", "root": "."},
            "config": {},
            "task": "audit",
            "task_context": "focus on controller to mapper flows",
            "previous_results": {
                "recon": {
                    "data": {
                        "entry_points": [{"type": "http", "file": "src/controller.java", "line": 10}],
                        "priority_paths": ["src/controller.java", "src/mapper.xml"],
                        "project_profile": {"languages": ["Java"], "frameworks": ["Spring"]},
                    }
                }
            },
        }
    )

    assert result.success is True
    assert result.data["summary"] == "done"
    assert llm_service.chat_completion.await_count == 3
    llm_service.chat_completion_stream.assert_not_called()
    assert result.data["steps"][0]["actions"][0]["action"] == "read_file"
    assert result.data["steps"][1]["actions"][0]["action"] == "read_many_files"
    assert "skill_library/code-audit-finding/SKILL.md" in (result.data["steps"][0]["observation"] or "")
    assert "src/controller.java,src/mapper.xml" in (result.data["steps"][1]["observation"] or "")


def test_finding_agent_structured_tool_schemas_exclude_internal_reasoning_tools():
    agent = FindingAgent(
        llm_service=MagicMock(),
        tools={
            "read_file": PathEchoTool("read_file"),
            "read_many_files": PathEchoTool("read_many_files"),
            "think": ThinkTool(),
            "reflect": ReflectTool(),
        },
        event_emitter=MagicMock(),
    )

    schemas = agent._build_structured_tool_schemas()
    schema_names = [
        schema.get("function", {}).get("name", "")
        for schema in schemas
        if isinstance(schema, dict)
    ]

    assert "read_file" in schema_names
    assert "read_many_files" in schema_names
    assert "think" not in schema_names
    assert "reflect" not in schema_names
    assert "load_skill_body" not in schema_names
    assert "skill_resource_lookup" not in schema_names


@pytest.mark.asyncio
async def test_finding_agent_rejects_compatibility_skill_tools():
    llm_service = MagicMock()
    llm_service.chat_completion_stream = MagicMock()
    agent = FindingAgent(
        llm_service=llm_service,
        tools={},
        event_emitter=MagicMock(),
    )

    class Step:
        actions = [
            ToolInvocation(
                action="skill_resource_lookup",
                action_input={"skill_ref": "code-audit-finding", "resource_name": "references", "mode": "list"},
            )
        ]

    observation = await agent._execute_step_actions(Step(), {})

    assert "Do not use skill_resource_lookup for Finding runtime skill loading." in observation


def test_finding_agent_fallback_result_does_not_use_heuristic_findings():
    llm_service = MagicMock()
    llm_service.chat_completion_stream = MagicMock()
    agent = FindingAgent(
        llm_service=llm_service,
        tools={},
        event_emitter=MagicMock(),
    )
    agent._runtime_state = None
    agent._steps = [
        SimpleNamespace(
            thought='Tool Calls:\\n- search_code({"keyword":"glueSource"})',
            action="search_code",
            observation="搜索结果: glueSource",
        )
    ]

    fallback = agent._build_fallback_result()

    assert fallback["findings"] == []
    assert "timed out" in fallback["summary"]


@pytest.mark.asyncio
async def test_finding_skill_preloader_returns_route_metadata_without_preloading_resources(monkeypatch):
    from app.services.agent.agents.finding_skill_preloader import FindingSkillPreloader

    monkeypatch.setattr(
        "app.services.agent.skill_service.SkillService.get_skill_body",
        AsyncMock(side_effect=AssertionError("skill body should not be preloaded")),
    )
    monkeypatch.setattr(
        "app.services.agent.skill_service.SkillService.get_skill_resource",
        AsyncMock(side_effect=AssertionError("skill resources should not be preloaded")),
    )

    preloader = FindingSkillPreloader()
    loaded = await preloader.preload(
        user_id=None,
        context={
            "skill_context": {
                "route_plan": {
                    "primary_skill": "code-audit-finding",
                    "mandatory_reads": [
                        "references/checklists/python.md",
                        "references/frameworks/fastapi.md",
                    ],
                    "recommended_reads": ["references/security/llm_security.md"],
                    "case_candidates": ["references/wooyun/INDEX.md"],
                }
            }
        },
    )

    assert loaded.primary_skill_name == "code-audit-finding"
    assert loaded.primary_skill_body == {}
    assert loaded.mandatory_resources == []
    assert loaded.recommended_resources == []
    assert loaded.case_hints == ["references/wooyun/INDEX.md"]
    assert loaded.to_prompt_block() == ""


def test_candidate_queue_manager_deduplicates_candidates():
    from app.services.agent.agents.finding_candidates import CandidateCase, CandidateQueueManager

    manager = CandidateQueueManager()
    queue = manager.build_queue(
        [
            CandidateCase(
                id="c1",
                vuln_family="idor",
                priority=90,
                entry_point_refs=["src/api.py:10"],
                source_refs=["src/api.py:10"],
                sink_refs=["src/service.py:33"],
                control_refs=[],
                business_flow_notes=[],
            ),
            CandidateCase(
                id="c2",
                vuln_family="idor",
                priority=70,
                entry_point_refs=["src/api.py:10"],
                source_refs=["src/api.py:10"],
                sink_refs=["src/service.py:33"],
                control_refs=[],
                business_flow_notes=[],
            ),
        ]
    )

    assert [candidate.id for candidate in queue] == ["c1"]


def test_evidence_bundle_store_upserts_and_summarizes_bundles():
    from app.services.agent.agents.finding_evidence import EvidenceBundle, EvidenceBundleStore

    store = EvidenceBundleStore()
    bundle_id = store.upsert(
        EvidenceBundle(
            id="ev-1",
            file_path="src/api.py",
            line_start=10,
            line_end=18,
            snippet="dangerous_call(user_input)",
            source_desc="request parameter",
            sink_desc="dangerous_call",
            control_analysis="missing ownership validation",
            business_flow_analysis="tenant update path",
            entry_point_refs=["src/api.py:10"],
            priority_path_refs=["src/api.py"],
            evidence_gaps=["missing_verification"],
            confidence=0.82,
        )
    )

    summary = store.build_candidate_summary(["ev-1"])

    assert bundle_id == "ev-1"
    assert summary["file_paths"] == ["src/api.py"]
    assert summary["entry_point_refs"] == ["src/api.py:10"]
    assert summary["evidence_gaps"] == ["missing_verification"]


def test_finding_controller_builds_coverage_first_runtime_state():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [
                    {"type": "http", "file": "app/api/uploads.py", "line": 11},
                    {"type": "http", "file": "app/api/admin.py", "line": 5},
                ],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    assert runtime_state.plan.strategy == "coverage_first"
    assert runtime_state.coverage.uncovered_priority_paths == [
        "app/api/uploads.py",
        "app/services/authz.py",
    ]
    assert runtime_state.queue
    assert runtime_state.queue[0].vuln_family in {"idor", "auth_bypass"}


@pytest.mark.asyncio
async def test_finding_agent_prepare_runtime_context_injects_runtime_state(monkeypatch):
    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())

    async def fake_preload(user_id, context):
        del user_id, context
        return PreloadedSkillContext(primary_skill_name="code-audit-finding")

    monkeypatch.setattr(agent._skill_preloader, "preload", fake_preload)

    prepared = await agent._prepare_runtime_context(
        {
            "config": {},
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor"],
            "skill_context": {
                "route_plan": {"primary_skill": "code-audit-finding"},
                "metadata": [
                    {
                        "slug": "code-audit-finding",
                        "paths": {"skill_file_path": "skill_library/code-audit-finding/SKILL.md"},
                    }
                ],
            },
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    assert prepared["preloaded_skill_context"].primary_skill_name == "code-audit-finding"
    assert prepared["finding_runtime_state"].plan.strategy == "coverage_first"
    assert prepared["finding_runtime_state"].queue
    assert prepared["skill_bootstrap_state"]["primary_skill"] == "code-audit-finding"
    assert prepared["skill_bootstrap_state"]["skill_file_path"] == "skill_library/code-audit-finding/SKILL.md"
    assert prepared["skill_bootstrap_state"]["loaded"] is False


@pytest.mark.asyncio
async def test_finding_requires_primary_skill_bootstrap_before_general_reads():
    agent = FindingAgent(
        llm_service=MagicMock(),
        tools={
            "read_file": EchoTool("read_file"),
            "read_many_files": EchoTool("read_many_files"),
            "load_skill_body": EchoTool("load_skill_body"),
        },
        event_emitter=MagicMock(),
    )
    agent.emit_llm_action = AsyncMock()
    agent.execute_tool = AsyncMock(return_value="should not run")
    agent._skill_bootstrap_state = {
        "primary_skill": "code-audit-finding",
        "skill_file_path": "skill_library/code-audit-finding/SKILL.md",
        "loaded": False,
    }

    step = SimpleNamespace(
        actions=[ToolInvocation(action="read_file", action_input={"file_path": "src/api.py"})],
    )

    observation = await agent._execute_step_actions(step, failed_tool_calls={})

    assert "read_file" in observation
    assert "skill_library/code-audit-finding/SKILL.md" in observation
    agent.execute_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_finding_allows_batch_actions_after_primary_skill_bootstrap():
    agent = FindingAgent(
        llm_service=MagicMock(),
        tools={
            "read_many_files": EchoTool("read_many_files"),
            "read_file": EchoTool("read_file"),
        },
        event_emitter=MagicMock(),
    )
    agent.emit_llm_action = AsyncMock()
    agent.execute_tool = AsyncMock(side_effect=["skill bootstrap ok", "batch read ok"])
    agent._skill_bootstrap_state = {
        "primary_skill": "code-audit-finding",
        "skill_file_path": "skill_library/code-audit-finding/SKILL.md",
        "loaded": False,
    }

    step = SimpleNamespace(
        actions=[
            ToolInvocation(
                action="read_file",
                action_input={"file_path": "skill_library/code-audit-finding/SKILL.md"},
            ),
            ToolInvocation(
                action="read_many_files",
                action_input={"file_paths": ["src/api.py", "src/service.py"]},
            ),
        ],
    )

    observation = await agent._execute_step_actions(step, failed_tool_calls={})

    assert "skill bootstrap ok" in observation
    assert "batch read ok" in observation
    assert agent._skill_bootstrap_state["loaded"] is True
    assert agent.execute_tool.await_count == 2


@pytest.mark.asyncio
async def test_finding_allows_read_many_files_to_bootstrap_primary_skill():
    agent = FindingAgent(
        llm_service=MagicMock(),
        tools={
            "read_many_files": EchoTool("read_many_files"),
        },
        event_emitter=MagicMock(),
    )
    agent.emit_llm_action = AsyncMock()
    agent.execute_tool = AsyncMock(return_value="batch bootstrap ok")
    agent._skill_bootstrap_state = {
        "primary_skill": "code-audit-finding",
        "skill_file_path": "skill_library/code-audit-finding/SKILL.md",
        "loaded": False,
    }

    step = SimpleNamespace(
        actions=[
            ToolInvocation(
                action="read_many_files",
                action_input={
                    "file_paths": [
                        "skill_library/code-audit-finding/SKILL.md",
                        "src/api.py",
                    ]
                },
            ),
        ],
    )

    observation = await agent._execute_step_actions(step, failed_tool_calls={})

    assert "batch bootstrap ok" in observation
    assert agent._skill_bootstrap_state["loaded"] is True


def test_finding_loop_detector_blocks_repeated_no_progress_calls():
    from app.services.agent.agents.finding_loop_detector import FindingLoopDetector

    detector = FindingLoopDetector(warn_threshold=2, block_threshold=3)

    first = detector.register("read_file", {"file_path": "app/api.py"}, evidence_delta=0)
    second = detector.register("read_file", {"file_path": "app/api.py"}, evidence_delta=0)
    third = detector.register("read_file", {"file_path": "app/api.py"}, evidence_delta=0)

    assert first.status == "allow"
    assert second.status == "warn"
    assert third.status == "block"


def test_candidate_worker_records_evidence_for_candidate():
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_evidence import EvidenceBundleStore
    from app.services.agent.agents.finding_worker import CandidateWorker

    store = EvidenceBundleStore()
    worker = CandidateWorker(evidence_store=store)
    candidate = CandidateCase(
        id="cand-1",
        vuln_family="idor",
        priority=95,
        entry_point_refs=["app/api/uploads.py:11"],
        source_refs=["app/api/uploads.py:11"],
        sink_refs=["app/services/authz.py:44"],
        control_refs=["app/services/authz.py:44"],
        business_flow_notes=["upload approval flow"],
    )

    result = worker.record_tool_result(
        candidate,
        "read_file",
        {"file_path": "app/api/uploads.py", "start_line": 11, "end_line": 19},
        "文件: app/api/uploads.py\n行数: 11-19\n```python\nowner_id = request.user_id\nservice.approve(upload_id)\n```",
    )

    assert result.evidence_bundle_ids
    summary = store.build_candidate_summary(result.evidence_bundle_ids)
    assert summary["file_paths"] == ["app/api/uploads.py"]
    assert candidate.evidence_bundle_ids == result.evidence_bundle_ids


def test_finding_synthesizer_builds_candidate_findings_from_evidence():
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap
    from app.services.agent.agents.finding_evidence import EvidenceBundle, EvidenceBundleStore
    from app.services.agent.agents.finding_synthesizer import FindingSynthesizer

    store = EvidenceBundleStore()
    store.upsert(
        EvidenceBundle(
            id="ev-1",
            file_path="app/api/uploads.py",
            line_start=11,
            line_end=19,
            snippet="service.approve(upload_id)",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="ownership check is missing in the reviewed path",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/api/uploads.py"],
            evidence_gaps=["dynamic_verification_pending"],
            confidence=0.83,
        )
    )

    candidate = CandidateCase(
        id="cand-1",
        vuln_family="idor",
        priority=95,
        entry_point_refs=["app/api/uploads.py:11"],
        source_refs=["app/api/uploads.py:11"],
        sink_refs=["app/services/authz.py:44"],
        control_refs=["app/services/authz.py:44"],
        business_flow_notes=["upload approval flow"],
        evidence_bundle_ids=["ev-1"],
    )
    runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[candidate],
    )

    synthesized = FindingSynthesizer().synthesize(runtime_state, store)

    assert synthesized["findings"]
    assert synthesized["findings"][0]["vulnerability_type"] == "idor"
    assert synthesized["findings"][0]["file_path"] == "app/api/uploads.py"
    assert synthesized["findings"][0]["verdict"] == "candidate"


@pytest.mark.asyncio
async def test_finding_agent_recover_final_result_prefers_synthesized_evidence(monkeypatch):
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap
    from app.services.agent.agents.finding_evidence import EvidenceBundle

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[
            CandidateCase(
                id="cand-1",
                vuln_family="idor",
                priority=95,
                entry_point_refs=["app/api/uploads.py:11"],
                source_refs=["app/api/uploads.py:11"],
                sink_refs=["app/services/authz.py:44"],
                control_refs=["app/services/authz.py:44"],
                business_flow_notes=["upload approval flow"],
                evidence_bundle_ids=["ev-1"],
            )
        ],
    )
    agent._evidence_store.upsert(
        EvidenceBundle(
            id="ev-1",
            file_path="app/api/uploads.py",
            line_start=11,
            line_end=19,
            snippet="service.approve(upload_id)",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="ownership check is missing in the reviewed path",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/api/uploads.py"],
            evidence_gaps=["dynamic_verification_pending"],
            confidence=0.83,
        )
    )

    async def unexpected_stream(messages):
        raise AssertionError(f"LLM fallback should not run when synthesized evidence exists: {messages}")

    monkeypatch.setattr(agent, "stream_llm_call", unexpected_stream)

    recovered = await agent._recover_final_result()

    assert recovered["findings"]
    assert recovered["findings"][0]["vulnerability_type"] == "idor"


def test_finding_controller_creates_worker_sessions_with_independent_budgets():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    assert runtime_state.active_candidate_id
    first_candidate = runtime_state.queue[0]
    session = runtime_state.worker_sessions[first_candidate.id]
    assert session.candidate_id == first_candidate.id
    assert session.remaining_budget == runtime_state.plan.worker_budget
    assert session.followup_rounds_left == runtime_state.plan.max_followup_rounds_per_candidate
    assert session.message_history
    assert first_candidate.vuln_family in session.brief
    assert first_candidate.sink_refs[0] in session.brief


def test_finding_controller_rotates_candidates_explicitly_and_marks_reason():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    first_id = runtime_state.active_candidate_id
    next_candidate = controller.rotate_candidate(runtime_state, "budget exhausted")

    assert next_candidate is not None
    assert next_candidate.id != first_id
    assert runtime_state.worker_sessions[first_id].status == "rotated"
    assert runtime_state.worker_sessions[first_id].rotation_reason == "budget exhausted"
    assert runtime_state.active_candidate_id == next_candidate.id


def test_finding_controller_updates_worker_budget_and_rotates_when_exhausted():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    first_id = runtime_state.active_candidate_id
    session = runtime_state.worker_sessions[first_id]
    session.remaining_budget = 1

    controller.consume_worker_budget(runtime_state, first_id, spent=1, reason="worker budget exhausted")

    assert runtime_state.worker_sessions[first_id].status == "budget_exhausted"
    assert runtime_state.active_candidate_id != first_id


def test_finding_controller_schedules_followup_round_before_full_exhaustion():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )

    first_id = runtime_state.active_candidate_id
    first_candidate = runtime_state.queue[0]
    first_candidate.evidence_bundle_ids.append("ev-1")
    session = runtime_state.worker_sessions[first_id]
    session.remaining_budget = 1
    session.followup_rounds_left = 1

    controller.consume_worker_budget(runtime_state, first_id, spent=1, reason="worker budget exhausted")

    assert runtime_state.worker_sessions[first_id].status == "needs_followup"
    assert runtime_state.worker_sessions[first_id].followup_rounds_left == 0
    assert runtime_state.active_candidate_id != first_id


@pytest.mark.asyncio
async def test_finding_agent_builds_iteration_messages_from_active_candidate_local_context(monkeypatch):
    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())

    async def fake_preload(user_id, context):
        del user_id, context
        return PreloadedSkillContext(primary_skill_name="code-audit-finding")

    monkeypatch.setattr(agent._skill_preloader, "preload", fake_preload)

    context = await agent._prepare_runtime_context(
        {
            "project_info": {"name": "demo", "root": "."},
            "config": {},
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor"],
            "skill_context": {"route_plan": {"primary_skill": "code-audit-finding"}},
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )
    agent._conversation_history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": agent._build_initial_message(context)},
        {"role": "assistant", "content": "global unrelated reasoning"},
    ]
    active_id = agent._runtime_state.active_candidate_id
    session = agent._runtime_state.worker_sessions[active_id]
    session.message_history.extend(
        [
            {"role": "assistant", "content": "candidate specific thought"},
            {"role": "user", "content": "Observation:\nread app/api/uploads.py"},
        ]
    )

    messages = agent._build_iteration_messages()

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "global unrelated reasoning" not in "\n".join(item["content"] for item in messages if item["role"] != "system")
    assert any("candidate specific thought" in item["content"] for item in messages)
    assert any(active_id in item["content"] for item in messages if item["role"] == "user")


def test_finding_agent_records_local_candidate_history():
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState, WorkerSession
    from app.services.agent.agents.finding_coverage import CoverageMap
    from app.services.agent.agents.finding_candidates import CandidateCase

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    candidate = CandidateCase(
        id="cand-1",
        vuln_family="idor",
        priority=90,
        entry_point_refs=["app/api/uploads.py:11"],
        source_refs=["app/api/uploads.py:11"],
        sink_refs=["app/services/authz.py:44"],
        control_refs=[],
        business_flow_notes=[],
    )
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[candidate],
        worker_sessions={
            "cand-1": WorkerSession(
                candidate_id="cand-1",
                brief="brief",
                max_budget=4,
                remaining_budget=4,
                status="active",
                message_history=[{"role": "user", "content": "brief"}],
                followup_rounds_left=1,
            )
        },
        active_candidate_id="cand-1",
    )

    agent._record_active_candidate_message("assistant", "candidate thought")
    agent._record_active_candidate_message("user", "Observation:\nchunk")

    history = agent._runtime_state.worker_sessions["cand-1"].message_history

    assert history[-2]["content"] == "candidate thought"
    assert history[-1]["content"] == "Observation:\nchunk"


@pytest.mark.asyncio
async def test_finding_agent_iteration_messages_include_global_budget_and_report_phase_guidance(monkeypatch):
    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent.config.max_iterations = 32
    agent._iteration = 26

    async def fake_preload(user_id, context):
        del user_id, context
        return PreloadedSkillContext(primary_skill_name="code-audit-finding")

    monkeypatch.setattr(agent._skill_preloader, "preload", fake_preload)

    context = await agent._prepare_runtime_context(
        {
            "project_info": {"name": "demo", "root": "."},
            "config": {},
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor"],
            "skill_context": {"route_plan": {"primary_skill": "code-audit-finding"}},
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )
    agent._conversation_history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": agent._build_initial_message(context)},
    ]

    messages = agent._build_iteration_messages()
    context_message = next(item["content"] for item in messages if item["role"] == "user" and "Active candidate local context" in item["content"])

    assert '"current_iteration": 26' in context_message
    assert '"max_iterations": 32' in context_message
    assert '"rounds_left": 6' in context_message
    assert '"phase": "report_finalization"' in context_message
    assert "Do not expand to new candidates" in context_message


def test_finding_agent_preemptive_finalization_prompt_escalates_by_remaining_rounds():
    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())

    assert agent._build_preemptive_finalization_prompt(7) == ""
    assert "Stop expanding coverage" in agent._build_preemptive_finalization_prompt(6)
    assert "Merge the strongest existing evidence now" in agent._build_preemptive_finalization_prompt(3)
    assert "Return Final Answer immediately" in agent._build_preemptive_finalization_prompt(1)


def test_finding_agent_aborts_llm_failures_immediately_in_final_only_mode():
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        phase="report_finalization",
        phase_reason="closed_exploit_chain_candidate",
    )

    assert agent._should_abort_after_llm_failure("[LLM调用错误: timeout] 请重试。", 1) is True

    agent._runtime_state.phase = "evidence_collection"
    assert agent._should_abort_after_llm_failure("[LLM调用错误: timeout] 请重试。", 1) is False


def test_finding_agent_enters_report_phase_when_closed_chain_candidate_exists():
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap
    from app.services.agent.agents.finding_evidence import EvidenceBundle

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    candidate = CandidateCase(
        id="cand-1",
        vuln_family="idor",
        priority=95,
        entry_point_refs=["app/api/uploads.py:11"],
        source_refs=["app/api/uploads.py:11"],
        sink_refs=["app/services/authz.py:44"],
        control_refs=["app/services/authz.py:44"],
        business_flow_notes=["upload approval flow"],
        evidence_bundle_ids=["ev-1", "ev-2"],
    )
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[candidate],
        active_candidate_id="cand-1",
    )
    agent._evidence_store.upsert(
        EvidenceBundle(
            id="ev-1",
            file_path="app/api/uploads.py",
            line_start=11,
            line_end=19,
            snippet="load target account",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="ownership check is missing in the reviewed path",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/api/uploads.py"],
            evidence_gaps=[],
            confidence=0.92,
        )
    )
    agent._evidence_store.upsert(
        EvidenceBundle(
            id="ev-2",
            file_path="app/services/authz.py",
            line_start=44,
            line_end=51,
            snippet="service.approve(upload_id)",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="approval executes without caller ownership validation",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/services/authz.py"],
            evidence_gaps=[],
            confidence=0.94,
        )
    )

    agent._update_runtime_phase(current_iteration=5)

    assert agent._runtime_state.phase == "report_finalization"
    assert agent._runtime_state.phase_reason == "closed_exploit_chain_candidate"


def test_finding_agent_report_phase_freezes_candidate_rotation():
    from app.services.agent.agents.finding_controller import FindingController

    controller = FindingController()
    runtime_state = controller.build_runtime_state(
        {
            "target_files": ["app/api/uploads.py"],
            "focus_vulnerabilities": ["idor", "auth_bypass"],
            "recon_data": {
                "entry_points": [{"type": "http", "file": "app/api/uploads.py", "line": 11}],
                "priority_paths": ["app/api/uploads.py", "app/services/authz.py"],
                "project_profile": {"languages": ["Python"], "frameworks": ["FastAPI"]},
            },
        }
    )
    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._runtime_state = runtime_state
    current_id = runtime_state.active_candidate_id

    agent._enter_report_finalization_phase("tail_budget")
    agent._advance_candidate("should not rotate while finalizing")

    assert runtime_state.phase == "report_finalization"
    assert runtime_state.phase_reason == "tail_budget"
    assert runtime_state.active_candidate_id == current_id
    assert runtime_state.rotation_history == []


def test_analysis_workflow_can_ignore_structured_tool_calls_when_tools_disabled():
    agent = DummyWorkflowAgent()

    step = agent._build_step_from_structured_response(
        'Tool Calls:\n- read_file({"file_path":"demo.py"})',
        [],
        allow_tool_calls=False,
    )

    assert step.action in {"", None}
    assert step.actions == []


@pytest.mark.asyncio
async def test_finding_agent_request_structured_step_omits_tools_in_final_only_mode():
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap

    llm_service = MagicMock()
    llm_service.chat_completion = AsyncMock(
        return_value={
            "content": 'Final Answer: {"findings": [], "summary": "done"}',
            "tool_calls": [],
            "usage": {"total_tokens": 9},
            "finish_reason": "stop",
        }
    )
    agent = FindingAgent(llm_service=llm_service, tools={"read_file": PathEchoTool("read_file")}, event_emitter=MagicMock())
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        phase="report_finalization",
        phase_reason="closed_exploit_chain_candidate",
    )
    agent._iteration = 10

    await agent._request_structured_step(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "return final"},
        ]
    )

    call_kwargs = llm_service.chat_completion.await_args.kwargs
    assert call_kwargs["tools"] == []


@pytest.mark.asyncio
async def test_finding_agent_final_only_mode_blocks_tool_execution():
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState, WorkerSession
    from app.services.agent.agents.finding_coverage import CoverageMap

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[
            CandidateCase(
                id="cand-1",
                vuln_family="idor",
                priority=95,
                entry_point_refs=["app/api/uploads.py:11"],
                source_refs=["app/api/uploads.py:11"],
                sink_refs=["app/services/authz.py:44"],
                control_refs=["app/services/authz.py:44"],
                business_flow_notes=[],
            )
        ],
        worker_sessions={
            "cand-1": WorkerSession(
                candidate_id="cand-1",
                brief="brief",
                max_budget=4,
                remaining_budget=4,
                status="finalizing",
                message_history=[{"role": "user", "content": "brief"}],
                followup_rounds_left=1,
            )
        },
        active_candidate_id="cand-1",
        phase="report_finalization",
        phase_reason="closed_exploit_chain_candidate",
    )
    agent.execute_tool = AsyncMock(side_effect=AssertionError("tools should not execute in final-only mode"))

    step = SimpleNamespace(
        action="read_file",
        action_input={"file_path": "app/api/uploads.py"},
        actions=[ToolInvocation(action="read_file", action_input={"file_path": "app/api/uploads.py"})],
    )

    observation = await agent._execute_step_actions(step, {})

    assert "Finalization lock active" in observation
    agent.execute_tool.assert_not_awaited()


def test_finding_agent_checkpoint_persists_closed_candidate_findings(tmp_path):
    from app.services.agent.agents.finding_candidates import CandidateCase
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState, WorkerSession
    from app.services.agent.agents.finding_coverage import CoverageMap
    from app.services.agent.agents.finding_evidence import EvidenceBundle

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._task_id = "task-1"
    agent._current_project_root = str(tmp_path)
    candidate = CandidateCase(
        id="cand-1",
        vuln_family="idor",
        priority=95,
        entry_point_refs=["app/api/uploads.py:11"],
        source_refs=["app/api/uploads.py:11"],
        sink_refs=["app/services/authz.py:44"],
        control_refs=["app/services/authz.py:44"],
        business_flow_notes=["upload approval flow"],
        evidence_bundle_ids=["ev-1", "ev-2"],
    )
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        queue=[candidate],
        worker_sessions={
            "cand-1": WorkerSession(
                candidate_id="cand-1",
                brief="brief",
                max_budget=4,
                remaining_budget=2,
                status="active",
                message_history=[{"role": "user", "content": "brief"}],
                followup_rounds_left=1,
            )
        },
        active_candidate_id="cand-1",
        phase="report_finalization",
        phase_reason="closed_exploit_chain_candidate",
    )
    agent._evidence_store.upsert(
        EvidenceBundle(
            id="ev-1",
            file_path="app/api/uploads.py",
            line_start=11,
            line_end=19,
            snippet="load target account",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="ownership check is missing in the reviewed path",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/api/uploads.py"],
            evidence_gaps=[],
            confidence=0.92,
        )
    )
    agent._evidence_store.upsert(
        EvidenceBundle(
            id="ev-2",
            file_path="app/services/authz.py",
            line_start=44,
            line_end=51,
            snippet="service.approve(upload_id)",
            source_desc="request path upload_id",
            sink_desc="approval state change",
            control_analysis="approval executes without caller ownership validation",
            business_flow_analysis="upload approval flow",
            entry_point_refs=["app/api/uploads.py:11"],
            priority_path_refs=["app/services/authz.py"],
            evidence_gaps=[],
            confidence=0.94,
        )
    )

    checkpoint_path = agent._persist_candidate_checkpoint(candidate, trigger="closed_exploit_chain_candidate")
    payload = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))

    assert payload["trigger"] == "closed_exploit_chain_candidate"
    assert payload["findings"]
    assert payload["findings"][0]["vulnerability_type"] == "idor"


@pytest.mark.asyncio
async def test_finding_agent_recover_final_result_prefers_checkpoint_findings(tmp_path, monkeypatch):
    from app.services.agent.agents.finding_controller import AuditPlan, FindingRuntimeState
    from app.services.agent.agents.finding_coverage import CoverageMap

    agent = FindingAgent(llm_service=MagicMock(), tools={}, event_emitter=MagicMock())
    agent._task_id = "task-1"
    agent._current_project_root = str(tmp_path)
    agent._runtime_state = FindingRuntimeState(
        plan=AuditPlan(focus_vulnerabilities=["idor"]),
        coverage=CoverageMap(entry_points=["app/api/uploads.py:11"]),
        phase="report_finalization",
        phase_reason="closed_exploit_chain_candidate",
    )
    checkpoint_file = Path(agent._checkpoint_file_path())
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_file.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "phase": "report_finalization",
                "trigger": "closed_exploit_chain_candidate",
                "findings": [
                    {
                        "vulnerability_type": "idor",
                        "severity": "high",
                        "title": "Recovered finding",
                        "description": "Recovered from checkpoint",
                        "file_path": "app/api/uploads.py",
                        "line_start": 11,
                        "line_end": 19,
                        "source": "request path upload_id",
                        "sink": "approval state change",
                        "suggestion": "Validate ownership",
                        "confidence": 0.93,
                        "verdict": "candidate",
                        "impact": "Unauthorized approval",
                        "cve_justification": "Closed source-to-sink path",
                        "verification_notes": "Recovered from checkpoint",
                        "exploit_chain": [],
                        "poc": {},
                        "entry_point_refs": ["app/api/uploads.py:11"],
                        "priority_path_refs": ["app/api/uploads.py"],
                        "business_flow_notes": ["upload approval flow"],
                        "evidence_gaps": [],
                    }
                ],
                "summary": "Recovered from checkpoint",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def unexpected_stream(messages):
        raise AssertionError(f"LLM recovery should not run when checkpoint findings exist: {messages}")

    monkeypatch.setattr(agent, "stream_llm_call", unexpected_stream)

    recovered = await agent._recover_final_result()

    assert recovered["findings"]
    assert recovered["findings"][0]["title"] == "Recovered finding"
