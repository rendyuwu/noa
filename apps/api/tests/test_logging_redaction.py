from __future__ import annotations

from typing import Any

from noa_api.core.logging import _redact_event_dict


def _redact(**kwargs: Any) -> dict[str, Any]:
    return _redact_event_dict(None, "info", kwargs)


def test_redacts_top_level_sensitive_keys() -> None:
    result = _redact(
        event="login_attempt",
        password="hunter2",
        token="abc123",
        username="admin",
    )
    assert result["password"] == "[redacted]"
    assert result["token"] == "[redacted]"
    assert result["username"] == "admin"
    assert result["event"] == "login_attempt"


def test_redacts_nested_dict_sensitive_keys() -> None:
    result = _redact(
        event="tool_call",
        args={"server_ref": "whm1", "api_token": "secret-tok", "reason": "test"},
    )
    assert result["args"]["api_token"] == "[redacted]"
    assert result["args"]["server_ref"] == "whm1"
    assert result["args"]["reason"] == "test"


def test_redacts_deeply_nested() -> None:
    result = _redact(
        event="deep",
        outer={"inner": {"ssh_private_key": "-----BEGIN RSA-----", "host": "10.0.0.1"}},
    )
    assert result["outer"]["inner"]["ssh_private_key"] == "[redacted]"
    assert result["outer"]["inner"]["host"] == "10.0.0.1"


def test_redacts_list_of_dicts() -> None:
    result = _redact(
        event="batch",
        items=[
            {"name": "a", "password": "pw1"},
            {"name": "b", "secret": "s2"},
        ],
    )
    assert result["items"][0]["password"] == "[redacted]"
    assert result["items"][0]["name"] == "a"
    assert result["items"][1]["secret"] == "[redacted]"
    assert result["items"][1]["name"] == "b"


def test_leaves_non_sensitive_unchanged() -> None:
    result = _redact(
        event="normal_log",
        user_id="u-1",
        thread_id="t-1",
        level="info",
    )
    assert result["user_id"] == "u-1"
    assert result["thread_id"] == "t-1"
    assert result["level"] == "info"
    assert result["event"] == "normal_log"


def test_handles_empty_nested_dict() -> None:
    result = _redact(event="empty", data={})
    assert result["data"] == {}


def test_handles_mixed_types() -> None:
    result = _redact(
        event="mixed",
        data={
            "count": 42,
            "password": "secret",
            "tags": ["a", "b"],
            "nested": {"api_token": "tok", "ok": True},
        },
    )
    assert result["data"]["count"] == 42
    assert result["data"]["password"] == "[redacted]"
    assert result["data"]["tags"] == ["a", "b"]
    assert result["data"]["nested"]["api_token"] == "[redacted]"
    assert result["data"]["nested"]["ok"] is True
