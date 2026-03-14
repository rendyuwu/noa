from __future__ import annotations

from structlog.contextvars import clear_contextvars, get_contextvars

from noa_api.core.logging_context import bind_log_context, log_context


def test_bind_log_context_ignores_none_values() -> None:
    clear_contextvars()

    bind_log_context(user_id="user-1", thread_id=None, tool_name="demo")

    context = get_contextvars()
    assert context["user_id"] == "user-1"
    assert context["tool_name"] == "demo"
    assert "thread_id" not in context


def test_log_context_resets_bound_values_after_exit() -> None:
    clear_contextvars()
    bind_log_context(request_id="req-1", user_id="user-1")

    with log_context(user_id="user-2", thread_id="thread-1", tool_name="demo"):
        context = get_contextvars()
        assert context["request_id"] == "req-1"
        assert context["user_id"] == "user-2"
        assert context["thread_id"] == "thread-1"
        assert context["tool_name"] == "demo"

    context = get_contextvars()
    assert context["request_id"] == "req-1"
    assert context["user_id"] == "user-1"
    assert "thread_id" not in context
    assert "tool_name" not in context
