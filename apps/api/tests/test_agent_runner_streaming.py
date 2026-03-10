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
