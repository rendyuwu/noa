from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, cast

from noa_api.core.config import Settings, get_required_llm_api_key
from noa_api.core.prompts.loader import load_system_prompt


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LLMTurnResponse:
    text: str
    tool_calls: list[LLMToolCall]
    reasoning: str = ""


class LLMClientProtocol(Protocol):
    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMTurnResponse: ...


class OpenAICompatibleLLMClient:
    def __init__(
        self, *, model: str, api_key: str, base_url: str | None, system_prompt: str
    ) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def run_turn(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
        on_text_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMTurnResponse:
        from noa_api.core.agent.message_codec import (
            _extract_reasoning_summary,
            _safe_json_object,
            _to_openai_chat_messages,
        )

        llm_messages = _to_openai_chat_messages(
            messages=messages,
            system_prompt=self._system_prompt,
        )

        if on_text_delta is None:
            request_kwargs: dict[str, Any] = {
                "model": self._model,
                "temperature": 0,
                "messages": cast(Any, llm_messages),
            }
            if tools:
                request_kwargs["tools"] = cast(Any, tools)
                request_kwargs["tool_choice"] = "auto"

            response: Any = await self._client.chat.completions.create(**request_kwargs)

            choice: Any = response.choices[0].message
            text = getattr(choice, "content", "") or ""
            reasoning = _extract_reasoning_summary(getattr(choice, "reasoning", None))
            tool_calls: list[LLMToolCall] = []
            for call in getattr(choice, "tool_calls", None) or []:
                function = getattr(call, "function", None)
                if function is None:
                    continue
                name = getattr(function, "name", None)
                if not isinstance(name, str) or not name:
                    continue
                args_raw = getattr(function, "arguments", None)
                args = _safe_json_object(
                    args_raw if isinstance(args_raw, str) else None
                )
                tool_calls.append(LLMToolCall(name=name, arguments=args))

            return LLMTurnResponse(
                text=text, tool_calls=tool_calls, reasoning=reasoning
            )

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": cast(Any, llm_messages),
            "stream": True,
        }
        if tools:
            request_kwargs["tools"] = cast(Any, tools)
            request_kwargs["tool_choice"] = "auto"

        stream: Any = await self._client.chat.completions.create(
            **cast(Any, request_kwargs)
        )

        text_chunks: list[str] = []
        reasoning_chunks: list[str] = []
        tool_call_acc: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            choices = getattr(chunk, "choices", None)
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                text_chunks.append(content)
                await on_text_delta(content)

            reasoning = _extract_reasoning_summary(
                getattr(delta, "reasoning", None),
                preserve_whitespace=True,
            )
            if reasoning:
                reasoning_chunks.append(reasoning)

            delta_tool_calls = getattr(delta, "tool_calls", None)
            if not isinstance(delta_tool_calls, list) or not delta_tool_calls:
                continue

            for call in delta_tool_calls:
                index = getattr(call, "index", None)
                if index is None:
                    continue
                try:
                    idx = int(index)
                except (TypeError, ValueError):
                    continue

                acc = tool_call_acc.setdefault(idx, {"name": "", "arguments": ""})
                function = getattr(call, "function", None)
                if function is None:
                    continue

                name = getattr(function, "name", None)
                if isinstance(name, str) and name:
                    acc["name"] = name
                arguments = getattr(function, "arguments", None)
                if isinstance(arguments, str) and arguments:
                    acc["arguments"] += arguments

        text = "".join(text_chunks)
        reasoning = "".join(reasoning_chunks).strip()
        tool_calls: list[LLMToolCall] = []
        for _, value in sorted(tool_call_acc.items()):
            name = value.get("name")
            if not name:
                continue
            args = _safe_json_object(value.get("arguments"))
            tool_calls.append(LLMToolCall(name=name, arguments=args))

        return LLMTurnResponse(text=text, tool_calls=tool_calls, reasoning=reasoning)


def create_default_llm_client(app_settings: Settings) -> LLMClientProtocol:
    api_key = get_required_llm_api_key(app_settings)

    prompt = load_system_prompt(app_settings)
    return OpenAICompatibleLLMClient(
        model=app_settings.llm_model,
        api_key=api_key,
        base_url=app_settings.llm_base_url,
        system_prompt=prompt.text,
    )


def _split_text_deltas(text: str, *, chunk_size: int = 24) -> list[str]:
    if not text:
        return []
    return [
        text[index : index + chunk_size] for index in range(0, len(text), chunk_size)
    ]
