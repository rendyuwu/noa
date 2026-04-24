from __future__ import annotations

from noa_api.api.assistant.assistant_streaming import (
    append_fallback_error_message,
    remove_streaming_placeholder,
)


def test_remove_streaming_placeholder_strips_assistant_streaming_message() -> None:
    messages = [
        {
            "id": "assistant-streaming",
            "role": "assistant",
            "parts": [{"type": "text", "text": "..."}],
        },
        {
            "id": "persisted",
            "role": "assistant",
            "parts": [{"type": "text", "text": "Saved"}],
        },
    ]

    assert remove_streaming_placeholder(messages) == [
        {
            "id": "persisted",
            "role": "assistant",
            "parts": [{"type": "text", "text": "Saved"}],
        }
    ]


def test_append_fallback_error_message_removes_placeholder_before_appending() -> None:
    messages = [
        {
            "id": "assistant-streaming",
            "role": "assistant",
            "parts": [{"type": "text", "text": "partial"}],
        },
        {
            "id": "persisted",
            "role": "user",
            "parts": [{"type": "text", "text": "Hello"}],
        },
    ]

    result = append_fallback_error_message(
        messages,
        "Assistant run failed. Please try again.",
    )

    assert len(result) == 2
    assert result[0] == {
        "id": "persisted",
        "role": "user",
        "parts": [{"type": "text", "text": "Hello"}],
    }
    assert result[1]["id"].startswith("assistant-run-error-")
    assert result[1]["role"] == "assistant"
    assert result[1]["parts"] == [
        {"type": "text", "text": "Assistant run failed. Please try again."}
    ]


def test_append_fallback_error_message_handles_non_message_items_safely() -> None:
    result = append_fallback_error_message(
        [
            "unexpected",
            {
                "id": "assistant-streaming",
                "role": "assistant",
                "parts": [{"type": "text", "text": "partial"}],
            },
        ],
        "Assistant run failed. Please try again.",
    )

    assert result[0] == "unexpected"
    assert result[1]["role"] == "assistant"
    assert result[1]["parts"] == [
        {"type": "text", "text": "Assistant run failed. Please try again."}
    ]
