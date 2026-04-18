import asyncio

import pytest

from app.services.agent.core.errors import LLMRateLimitError
from app.services.llm.service import LLMService


class _ConcurrencyProbeAdapter:
    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, request):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.05)
            return type(
                "Response",
                (),
                {
                    "content": "ok",
                    "model": "stub-model",
                    "usage": None,
                    "finish_reason": "stop",
                },
            )()
        finally:
            self.in_flight -= 1


class _RetryProbeAdapter:
    def __init__(self):
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            raise LLMRateLimitError("rate limited")
        return type(
            "Response",
            (),
            {
                "content": "recovered",
                "model": "stub-model",
                "usage": None,
                "finish_reason": "stop",
            },
        )()


class _ToolingProbeAdapter:
    def __init__(self):
        self.request = None

    async def complete(self, request):
        self.request = request
        return type(
            "Response",
            (),
            {
                "content": "tooling",
                "model": "stub-model",
                "usage": None,
                "finish_reason": "stop",
            },
        )()



class _StreamingProbeAdapter:
    def __init__(self):
        self.request = None
        self.complete_called = False

    async def complete(self, request):
        self.complete_called = True
        raise AssertionError("chat_completion_stream should use adapter.stream_complete")

    async def stream_complete(self, request):
        self.request = request
        yield {"type": "token", "content": "Need ", "accumulated": "Need "}
        yield {
            "type": "tool_call",
            "tool_call": {
                "id": "call_1",
                "type": "function",
                "name": "Read",
                "arguments": "{\"file_path\":\"README.md\"}",
            },
        }
        yield {
            "type": "done",
            "content": "Need tool",
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        }


@pytest.mark.asyncio
async def test_llm_service_respects_user_configured_llm_concurrency(monkeypatch):
    adapter = _ConcurrencyProbeAdapter()
    service = LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "openai",
                "llmApiKey": "test-key",
                "llmModel": "test-model",
            },
            "otherConfig": {
                "llmConcurrency": 1,
                "llmGapMs": 0,
            },
        }
    )

    monkeypatch.setattr("app.services.llm.service.LLMFactory.create_adapter", lambda config: adapter)

    await asyncio.gather(
        service.chat_completion(messages=[{"role": "user", "content": "first"}]),
        service.chat_completion(messages=[{"role": "user", "content": "second"}]),
    )

    assert adapter.max_in_flight == 1


@pytest.mark.asyncio
async def test_llm_service_retries_rate_limit_errors(monkeypatch):
    adapter = _RetryProbeAdapter()
    service = LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "openai",
                "llmApiKey": "test-key",
                "llmModel": "test-model",
            },
            "otherConfig": {
                "llmConcurrency": 1,
                "llmGapMs": 0,
            },
        }
    )

    monkeypatch.setattr("app.services.llm.service.LLMFactory.create_adapter", lambda config: adapter)

    result = await service.chat_completion(messages=[{"role": "user", "content": "retry me"}])

    assert result["content"] == "recovered"
    assert adapter.calls == 2


@pytest.mark.asyncio
async def test_llm_service_passes_tools_and_parallel_tool_calls(monkeypatch):
    adapter = _ToolingProbeAdapter()
    service = LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "openai",
                "llmApiKey": "test-key",
                "llmModel": "test-model",
            }
        }
    )

    monkeypatch.setattr("app.services.llm.service.LLMFactory.create_adapter", lambda config: adapter)

    result = await service.chat_completion(
        messages=[{"role": "user", "content": "use tools"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_many_files",
                    "description": "Read multiple files",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        parallel_tool_calls=True,
    )

    assert result["content"] == "tooling"
    assert adapter.request is not None
    assert adapter.request.tools[0]["function"]["name"] == "read_many_files"
    assert adapter.request.parallel_tool_calls is True


def test_llm_service_uses_runtime_env_fallbacks_for_provider_config():
    service = LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "claude",
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "env-claude-key",
                    "ANTHROPIC_BASE_URL": "https://pureopus.cc",
                    "ANTHROPIC_MODEL": "claude-opus-4-6",
                    "API_TIMEOUT_MS": "3000000",
                },
            }
        }
    )

    config = service.get_agent_config()

    assert config.provider.value == "claude"
    assert config.api_key == "env-claude-key"
    assert config.base_url == "https://pureopus.cc"
    assert config.model == "claude-opus-4-6"
    assert config.timeout == 3000



@pytest.mark.asyncio
async def test_llm_service_chat_completion_stream_preserves_provider_tool_call_events(monkeypatch):
    adapter = _StreamingProbeAdapter()
    service = LLMService(
        user_config={
            "llmConfig": {
                "llmProvider": "openai",
                "llmApiKey": "test-key",
                "llmModel": "test-model",
            }
        }
    )

    monkeypatch.setattr("app.services.llm.service.LLMFactory.create_adapter", lambda config: adapter)

    events = []
    async for event in service.chat_completion_stream(
        messages=[{"role": "user", "content": "use tools"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        parallel_tool_calls=True,
    ):
        events.append(event)

    assert [event["type"] for event in events] == ["token", "tool_call", "done"]
    assert events[1]["tool_call"]["name"] == "Read"
    assert events[2]["finish_reason"] == "tool_calls"
    assert adapter.request is not None
    assert adapter.request.parallel_tool_calls is True
    assert adapter.complete_called is False
