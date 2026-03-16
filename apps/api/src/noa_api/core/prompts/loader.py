from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noa_api.core.config import Settings


_LAYER_SEPARATOR = "\n\n---\n\n"
_DEFAULT_PROMPT_FILE = "noa-system-prompt.md"
_DEFAULT_PROMPT_SOURCE = f"package:{_DEFAULT_PROMPT_FILE}"


@dataclass(frozen=True, slots=True)
class LoadedSystemPrompt:
    text: str
    fingerprint: str
    sources: tuple[str, ...]


def load_system_prompt(app_settings: Settings) -> LoadedSystemPrompt:
    return _load_system_prompt_cached(
        app_settings.llm_system_prompt,
        app_settings.llm_system_prompt_path,
        tuple(app_settings.llm_system_prompt_extra_paths),
    )


@lru_cache(maxsize=32)
def _load_system_prompt_cached(
    prompt_override: str | None,
    prompt_path: str | None,
    extra_paths: tuple[str, ...],
) -> LoadedSystemPrompt:
    if prompt_override is not None:
        return _build_prompt(
            layers=[
                _require_prompt_text(prompt_override, source="env:LLM_SYSTEM_PROMPT")
            ],
            sources=("env:LLM_SYSTEM_PROMPT",),
        )

    base_source, base_text = _load_base_prompt(prompt_path)
    layers = [base_text]
    sources = [base_source]

    for raw_path in extra_paths:
        source, text = _load_prompt_file(raw_path)
        layers.append(text)
        sources.append(source)

    return _build_prompt(layers=layers, sources=tuple(sources))


def prompt_fingerprint(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def _build_prompt(*, layers: list[str], sources: tuple[str, ...]) -> LoadedSystemPrompt:
    text = _LAYER_SEPARATOR.join(layers)
    return LoadedSystemPrompt(
        text=text,
        fingerprint=prompt_fingerprint(text),
        sources=sources,
    )


def _load_base_prompt(prompt_path: str | None) -> tuple[str, str]:
    if prompt_path is not None:
        return _load_prompt_file(prompt_path)

    prompt = (
        resources.files("noa_api.core.prompts")
        .joinpath(_DEFAULT_PROMPT_FILE)
        .read_text(encoding="utf-8")
    )
    return _DEFAULT_PROMPT_SOURCE, _require_prompt_text(
        prompt, source=_DEFAULT_PROMPT_SOURCE
    )


def _load_prompt_file(raw_path: str) -> tuple[str, str]:
    path = _resolve_path(raw_path)
    if not path.is_file():
        raise FileNotFoundError(f"System prompt file not found: {path}")
    source = f"file:{path}"
    prompt = path.read_text(encoding="utf-8")
    return source, _require_prompt_text(prompt, source=source)


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _require_prompt_text(prompt: str, *, source: str) -> str:
    text = prompt.strip()
    if not text:
        raise ValueError(f"System prompt source is empty: {source}")
    return text
