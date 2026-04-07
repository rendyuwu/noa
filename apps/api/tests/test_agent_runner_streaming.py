from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from noa_api.core.agent.runner import (
    AgentRunner,
    LLMTurnResponse,
    OpenAICompatibleLLMClient,
)


async def test_agent_runner_passes_text_delta_callback_to_llm_client() -> None:
    seen: list[str] = []

    async def on_text_delta(delta: str) -> None:
        seen.append(delta)

    class _FakeLLMClient:
        async def run_turn(
            self,
            *,
            messages: list[dict[str, object]],
            tools: list[dict[str, object]],
            on_text_delta=None,
        ) -> LLMTurnResponse:
            _ = messages, tools
            assert on_text_delta is not None
            await on_text_delta("Hello")
            await on_text_delta(" world")
            return LLMTurnResponse(text="Hello world", tool_calls=[])

    runner = AgentRunner(
        llm_client=_FakeLLMClient(),
        action_tool_run_service=object(),
        session=None,
    )

    result = await runner.run_turn(
        thread_messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hi"}],
            }
        ],
        available_tool_names=set(),
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
        on_text_delta=on_text_delta,
    )

    assert seen == ["Hello", " world"]
    assert result.text_deltas == ["Hello world"]
    assert result.messages
    assert result.messages[0].parts[0]["text"] == "Hello world"


async def test_openai_compatible_llm_client_streams_when_callback_present() -> None:
    seen: list[str] = []

    async def on_text_delta(delta: str) -> None:
        seen.append(delta)

    @dataclass
    class _Delta:
        content: str | None = None
        tool_calls: Any = None

    @dataclass
    class _Choice:
        delta: _Delta

    @dataclass
    class _Chunk:
        choices: list[_Choice]

    class _FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object):
            self.calls.append(dict(kwargs))
            if kwargs.get("stream") is True:

                async def _gen():
                    yield _Chunk(choices=[_Choice(delta=_Delta(content="Hello"))])
                    yield _Chunk(choices=[_Choice(delta=_Delta(content=" world"))])

                return _gen()

            raise AssertionError("Expected streaming call")

    class _FakeChat:
        def __init__(self) -> None:
            self.completions: Any = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat: Any = _FakeChat()

    client = OpenAICompatibleLLMClient(
        model="gpt-4o-mini",
        api_key="test",
        base_url=None,
        system_prompt="",
    )
    fake = _FakeOpenAI()
    client._client = fake  # type: ignore[attr-defined]

    result = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hi"}],
            }
        ],
        tools=[],
        on_text_delta=on_text_delta,
    )

    assert seen == ["Hello", " world"]
    assert result.text == "Hello world"
    assert fake.chat.completions.calls
    assert fake.chat.completions.calls[0].get("stream") is True


async def test_openai_compatible_llm_client_extracts_reasoning_summary_from_stream() -> (
    None
):
    seen: list[str] = []

    async def on_text_delta(delta: str) -> None:
        seen.append(delta)

    @dataclass
    class _Reasoning:
        summary: str

    @dataclass
    class _Delta:
        content: str | None = None
        tool_calls: Any = None
        reasoning: _Reasoning | None = None

    @dataclass
    class _Choice:
        delta: _Delta

    @dataclass
    class _Chunk:
        choices: list[_Choice]

    class _FakeCompletions:
        async def create(self, **kwargs: object):
            if kwargs.get("stream") is True:

                async def _gen():
                    yield _Chunk(
                        choices=[
                            _Choice(delta=_Delta(reasoning=_Reasoning(summary="Lik")))
                        ]
                    )
                    yield _Chunk(
                        choices=[
                            _Choice(delta=_Delta(reasoning=_Reasoning(summary="ely")))
                        ]
                    )
                    yield _Chunk(
                        choices=[_Choice(delta=_Delta(content="Visible answer."))]
                    )

                return _gen()

            raise AssertionError("Expected streaming call")

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    client = OpenAICompatibleLLMClient(
        model="gpt-4o-mini",
        api_key="test",
        base_url=None,
        system_prompt="",
    )
    client._client = _FakeOpenAI()  # type: ignore[attr-defined]

    result = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hi"}],
            }
        ],
        tools=[],
        on_text_delta=on_text_delta,
    )

    assert seen == ["Visible answer."]
    assert result.text == "Visible answer."
    assert result.reasoning == "Likely"


async def test_openai_compatible_llm_client_preserves_reasoning_chunk_spacing() -> None:
    seen: list[str] = []

    async def on_text_delta(delta: str) -> None:
        seen.append(delta)

    @dataclass
    class _Reasoning:
        summary: str

    @dataclass
    class _Delta:
        content: str | None = None
        tool_calls: Any = None
        reasoning: _Reasoning | None = None

    @dataclass
    class _Choice:
        delta: _Delta

    @dataclass
    class _Chunk:
        choices: list[_Choice]

    class _FakeCompletions:
        async def create(self, **kwargs: object):
            if kwargs.get("stream") is True:

                async def _gen():
                    yield _Chunk(
                        choices=[
                            _Choice(delta=_Delta(reasoning=_Reasoning(summary="User")))
                        ]
                    )
                    yield _Chunk(
                        choices=[
                            _Choice(
                                delta=_Delta(reasoning=_Reasoning(summary=" wants"))
                            )
                        ]
                    )
                    yield _Chunk(
                        choices=[
                            _Choice(delta=_Delta(reasoning=_Reasoning(summary=" to")))
                        ]
                    )
                    yield _Chunk(
                        choices=[_Choice(delta=_Delta(content="Visible answer."))]
                    )

                return _gen()

            raise AssertionError("Expected streaming call")

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    client = OpenAICompatibleLLMClient(
        model="gpt-4o-mini",
        api_key="test",
        base_url=None,
        system_prompt="",
    )
    client._client = _FakeOpenAI()  # type: ignore[attr-defined]

    result = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "Hi"}],
            }
        ],
        tools=[],
        on_text_delta=on_text_delta,
    )

    assert seen == ["Visible answer."]
    assert result.text == "Visible answer."
    assert result.reasoning == "User wants to"


async def test_openai_client_includes_tool_calls_and_tool_results_in_messages() -> None:
    captured: dict[str, object] = {}

    class _FakeCompletions:
        async def create(self, **kwargs: object):
            captured.update(kwargs)

            class _Msg:
                content = "ok"
                tool_calls: list[object] = []

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self) -> None:
            self.chat = _FakeChat()

    client = OpenAICompatibleLLMClient(
        model="gpt-4o-mini",
        api_key="test",
        base_url=None,
        system_prompt="",
    )
    client._client = _FakeOpenAI()  # type: ignore[attr-defined]

    _ = await client.run_turn(
        messages=[
            {
                "role": "user",
                "parts": [{"type": "text", "text": "hi"}],
            },
            {
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-call",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-1",
                        "args": {},
                    }
                ],
            },
            {
                "role": "tool",
                "parts": [
                    {
                        "type": "tool-result",
                        "toolName": "get_current_date",
                        "toolCallId": "tc-1",
                        "result": {"date": "2026-03-12"},
                        "isError": False,
                    }
                ],
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "get_current_date",
                    "description": "",
                    "parameters": {"type": "object"},
                },
            }
        ],
        on_text_delta=None,
    )

    msgs = captured.get("messages")
    assert isinstance(msgs, list)
    assert any(
        isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
        for m in msgs
    )
    assert any(
        isinstance(m, dict)
        and m.get("role") == "tool"
        and m.get("tool_call_id") == "tc-1"
        for m in msgs
    )
