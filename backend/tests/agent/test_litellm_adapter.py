import types
import sys

import pytest

from app.services.llm.adapters.litellm_adapter import LiteLLMAdapter
from app.services.llm.types import LLMConfig, LLMMessage, LLMProvider, LLMRequest


class _FunctionDelta:
    def __init__(self, *, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCallDelta:
    def __init__(self, *, index=0, id=None, type='function', function=None):
        self.index = index
        self.id = id
        self.type = type
        self.function = function


class _Delta:
    def __init__(self, *, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _Choice:
    def __init__(self, *, delta=None, finish_reason=None):
        self.delta = delta or _Delta()
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, *, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _StreamResponse:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk
        return _gen()


@pytest.mark.asyncio
async def test_litellm_adapter_stream_complete_emits_tool_call_before_done(monkeypatch):
    async def fake_acompletion(**kwargs):
        assert kwargs['stream'] is True
        assert kwargs['tools'][0]['function']['name'] == 'Read'
        return _StreamResponse([
            _Chunk(choices=[_Choice(delta=_Delta(content='Need '))]),
            _Chunk(
                choices=[
                    _Choice(
                        delta=_Delta(
                            tool_calls=[
                                _ToolCallDelta(
                                    index=0,
                                    id='call_1',
                                    function=_FunctionDelta(name='Read', arguments='{"file_path":"README'),
                                )
                            ]
                        )
                    )
                ]
            ),
            _Chunk(
                choices=[
                    _Choice(
                        delta=_Delta(
                            tool_calls=[
                                _ToolCallDelta(
                                    index=0,
                                    function=_FunctionDelta(arguments='.md"}'),
                                )
                            ]
                        ),
                        finish_reason='tool_calls',
                    )
                ],
                usage=types.SimpleNamespace(prompt_tokens=12, completion_tokens=7, total_tokens=19),
            ),
        ])

    fake_litellm = types.SimpleNamespace(
        acompletion=fake_acompletion,
        cache=None,
        drop_params=False,
        exceptions=types.SimpleNamespace(
            AuthenticationError=Exception,
            RateLimitError=Exception,
            APIConnectionError=Exception,
            APIError=Exception,
        ),
    )
    monkeypatch.setitem(sys.modules, 'litellm', fake_litellm)

    adapter = LiteLLMAdapter(
        LLMConfig(
            provider=LLMProvider.OPENAI,
            api_key='test-key',
            model='gpt-4o-mini',
        )
    )
    request = LLMRequest(
        messages=[LLMMessage(role='user', content='inspect')],
        tools=[
            {
                'type': 'function',
                'function': {
                    'name': 'Read',
                    'description': 'Read a file',
                    'parameters': {'type': 'object', 'properties': {}},
                },
            }
        ],
        parallel_tool_calls=True,
        stream=True,
    )

    events = []
    async for event in adapter.stream_complete(request):
        events.append(event)

    assert [event['type'] for event in events] == ['token', 'tool_call', 'done']
    assert events[1]['tool_call']['name'] == 'Read'
    assert events[1]['tool_call']['arguments'] == '{"file_path":"README.md"}'
    assert events[2]['finish_reason'] == 'tool_calls'
    assert events[2]['usage']['total_tokens'] == 19
