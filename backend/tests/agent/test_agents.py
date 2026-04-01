import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.agent.agents.analysis import AnalysisAgent
from app.services.agent.agents.base import AgentConfig, AgentPattern, AgentResult, AgentType
from app.services.agent.agents.recon import ReconAgent


class TestReconAgent:
    @pytest.fixture
    def recon_agent(self, mock_llm_service, mock_event_emitter):
        mock_llm_service.chat_completion_stream = MagicMock()
        return ReconAgent(
            llm_service=mock_llm_service,
            tools={},
            event_emitter=mock_event_emitter,
        )

    def test_recon_agent_normalizes_navigation_contract(self, recon_agent):
        result = recon_agent._normalize_recon_result(
            {
                "tech_stack": {"languages": ["Python"], "frameworks": ["Flask"]},
                "high_risk_areas": ["src/api.py"],
                "recommended_tools": {"must_use": ["semgrep_scan"], "recommended": ["bandit_scan"]},
            },
            config={"target_files": ["src/api.py"]},
        )

        assert result["project_profile"]["languages"] == ["Python"]
        assert result["priority_paths"] == ["src/api.py"]
        assert result["recommended_scanners"]["must_use"] == ["semgrep_scan"]
        assert result["audit_targets"]["target_files"] == ["src/api.py"]

    def test_recon_agent_summary_fallback_returns_new_schema(self, recon_agent):
        recon_agent._steps = [
            SimpleNamespace(
                thought="found flask routes",
                observation="requirements.txt\nsrc/api.py\nflask\nsqlite\n",
            )
        ]

        result = recon_agent._summarize_from_steps(config={"target_files": ["src/api.py"]})

        assert "project_profile" in result
        assert "priority_paths" in result
        assert "audit_targets" in result
        assert "tech_stack" not in result


class TestAnalysisAgent:
    @pytest.fixture
    def analysis_agent(self, temp_project_dir, mock_llm_service, mock_event_emitter):
        from app.services.agent.tools import FileReadTool, FileSearchTool, PatternMatchTool

        tools = {
            "read_file": FileReadTool(temp_project_dir),
            "search_code": FileSearchTool(temp_project_dir),
            "pattern_match": PatternMatchTool(temp_project_dir),
        }

        return AnalysisAgent(
            llm_service=mock_llm_service,
            tools=tools,
            event_emitter=mock_event_emitter,
        )

    def test_analysis_agent_instantiates(self, analysis_agent):
        assert analysis_agent.name == "Analysis"

    def test_analysis_agent_preserves_tooling(self, analysis_agent):
        assert set(analysis_agent.tools.keys()) == {"read_file", "search_code", "pattern_match"}


class TestAgentResult:
    def test_agent_result_success(self):
        result = AgentResult(
            success=True,
            data={"findings": []},
            iterations=5,
            tool_calls=10,
        )

        assert result.success is True
        assert result.iterations == 5
        assert result.tool_calls == 10

    def test_agent_result_failure(self):
        result = AgentResult(
            success=False,
            error="Test error",
        )

        assert result.success is False
        assert result.error == "Test error"

    def test_agent_result_to_dict(self):
        result = AgentResult(
            success=True,
            data={"key": "value"},
            iterations=3,
        )

        data = result.to_dict()

        assert data["success"] is True
        assert data["iterations"] == 3


class TestAgentConfig:
    def test_agent_config_defaults(self):
        config = AgentConfig(
            name="Test",
            agent_type=AgentType.RECON,
        )

        assert config.pattern == AgentPattern.REACT
        assert config.max_iterations == 20
        assert config.temperature == 0.1

    def test_agent_config_custom(self):
        config = AgentConfig(
            name="Custom",
            agent_type=AgentType.ANALYSIS,
            pattern=AgentPattern.PLAN_AND_EXECUTE,
            max_iterations=50,
            temperature=0.5,
        )

        assert config.pattern == AgentPattern.PLAN_AND_EXECUTE
        assert config.max_iterations == 50
        assert config.temperature == 0.5
