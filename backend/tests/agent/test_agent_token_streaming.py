import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings
from app.services.agent.agents.base import AgentConfig, AgentResult, AgentType, BaseAgent


class _CollectingEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, event_data):
        self.events.append(event_data)


class _StreamingLLM:
    def get_agent_timeout_config(self):
        return {
            "llm_first_token_timeout": 1,
            "llm_stream_timeout": 1,
            "agent_timeout": 10,
            "sub_agent_timeout": 10,
            "tool_timeout": 10,
        }

    async def chat_completion_stream(self, **kwargs):
        accumulated = ""
        for token in ["a", "b", "c", "d", "e"]:
            accumulated += token
            yield {"type": "token", "content": token, "accumulated": accumulated}
        yield {"type": "done", "content": accumulated, "usage": {"total_tokens": 5}}


class _TestAgent(BaseAgent):
    async def run(self, input_data):
        return AgentResult(success=True)


@pytest.mark.asyncio
async def test_stream_llm_call_batches_thinking_token_events(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_TOKEN_EVENT_CHUNK_SIZE", 2)
    monkeypatch.setattr(settings, "AGENT_TOKEN_EVENT_FLUSH_INTERVAL_MS", 60_000)
    emitter = _CollectingEmitter()
    agent = _TestAgent(
        config=AgentConfig(name="Test", agent_type=AgentType.ANALYSIS),
        llm_service=_StreamingLLM(),
        tools={},
        event_emitter=emitter,
    )

    content, total_tokens = await agent.stream_llm_call([{"role": "user", "content": "hi"}])

    token_events = [event for event in emitter.events if event.event_type == "thinking_token"]

    assert content == "abcde"
    assert total_tokens == 5
    assert [event.metadata["token"] for event in token_events] == ["ab", "cd", "e"]
    assert all("accumulated" not in event.metadata for event in token_events)
