from __future__ import annotations

import pytest
from pydantic import SecretStr

from noa_api.core.config import Settings

_BASE = {
    "environment": "development",
    "llm_api_key": SecretStr("test-key"),
}


def _make_settings(**overrides: object) -> Settings:
    return Settings.model_validate({**_BASE, **overrides})


@pytest.mark.parametrize(
    "raw, expected",
    [
        # JSON array (C9 format)
        ('["http://localhost:3000"]', ["http://localhost:3000"]),
        (
            '["http://localhost:3000", "https://example.com"]',
            ["http://localhost:3000", "https://example.com"],
        ),
        # Comma-separated fallback
        ("http://localhost:3000", ["http://localhost:3000"]),
        (
            "http://localhost:3000, https://example.com",
            ["http://localhost:3000", "https://example.com"],
        ),
        # Empty / whitespace
        ("", []),
        ("   ", []),
        # None
        (None, []),
    ],
)
def test_normalize_cors_origins(raw: str | None, expected: list[str]) -> None:
    """CORS origins validator handles JSON arrays per C9 (V57)."""
    settings = _make_settings(api_cors_allowed_origins=raw)
    assert settings.api_cors_allowed_origins == expected


def test_normalize_cors_origins_strips_whitespace_in_json_array() -> None:
    settings = _make_settings(
        api_cors_allowed_origins='["  http://localhost:3000  ", " https://example.com "]'
    )
    assert settings.api_cors_allowed_origins == [
        "http://localhost:3000",
        "https://example.com",
    ]


def test_normalize_cors_origins_accepts_list_directly() -> None:
    settings = _make_settings(
        api_cors_allowed_origins=["http://localhost:3000", "https://example.com"]
    )
    assert settings.api_cors_allowed_origins == [
        "http://localhost:3000",
        "https://example.com",
    ]
