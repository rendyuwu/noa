from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

import pytest

pytest.importorskip("assistant_stream")

from noa_api.api.error_handling import ApiHTTPException
from noa_api.api.routes.assistant_commands import (
    AddMessageCommand,
    AddToolResultCommand,
    ApproveActionCommand,
    AssistantMessage,
    AssistantRequest,
    AssistantServiceProtocol,
    DenyActionCommand,
    apply_commands,
    validate_commands,
)
from noa_api.core.auth.authorization import AuthorizationService, AuthorizationUser


@dataclass
class _FakeAssistantService:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    approve_allowed: bool | None = None

    async def add_message(
        self,
        *,
        owner_user_id: UUID,
        thread_id: UUID,
        role: str,
        parts: list[dict[str, object]],
    ) -> None:
        self.calls.append(
            (
                "add_message",
                {
                    "owner_user_id": owner_user_id,
                    "thread_id": thread_id,
                    "role": role,
                    "parts": parts,
                },
            )
        )

    async def approve_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
        is_user_active: bool,
        authorize_tool_access,
    ) -> None:
        self.approve_allowed = await authorize_tool_access("deploy-site")
        self.calls.append(
            (
                "approve_action",
                {
                    "owner_user_id": owner_user_id,
                    "owner_user_email": owner_user_email,
                    "thread_id": thread_id,
                    "action_request_id": action_request_id,
                    "is_user_active": is_user_active,
                },
            )
        )

    async def deny_action(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        action_request_id: str | None,
    ) -> None:
        self.calls.append(
            (
                "deny_action",
                {
                    "owner_user_id": owner_user_id,
                    "owner_user_email": owner_user_email,
                    "thread_id": thread_id,
                    "action_request_id": action_request_id,
                },
            )
        )

    async def add_tool_result(
        self,
        *,
        owner_user_id: UUID,
        owner_user_email: str | None,
        thread_id: UUID,
        tool_call_id: str,
        result: dict[str, object],
    ) -> None:
        self.calls.append(
            (
                "add_tool_result",
                {
                    "owner_user_id": owner_user_id,
                    "owner_user_email": owner_user_email,
                    "thread_id": thread_id,
                    "tool_call_id": tool_call_id,
                    "result": result,
                },
            )
        )


@dataclass
class _FakeAuthorizationService:
    allowed_tools: set[str] = field(default_factory=set)
    calls: list[tuple[UUID, str]] = field(default_factory=list)

    async def authorize_tool_access(
        self, user: AuthorizationUser, tool_name: str
    ) -> bool:
        self.calls.append((user.user_id, tool_name))
        return tool_name in self.allowed_tools

    async def get_allowed_tool_names(self, user: AuthorizationUser) -> set[str]:
        _ = user
        return set(self.allowed_tools)


def _make_user(*, user_id: UUID) -> AuthorizationUser:
    return AuthorizationUser(
        user_id=user_id,
        email="owner@example.com",
        display_name="Owner",
        is_active=True,
        roles=["member"],
        tools=[],
    )


def test_validate_commands_rejects_edit_style_add_message() -> None:
    with pytest.raises(ApiHTTPException) as exc_info:
        validate_commands(
            [
                AddMessageCommand(
                    type="add-message",
                    parentId="parent-1",
                    sourceId="source-1",
                    message=AssistantMessage(
                        role="user",
                        parts=[{"type": "text", "text": "Edited"}],
                    ),
                )
            ]
        )

    assert exc_info.value.detail == "Editing existing messages is not supported yet"
    assert exc_info.value.error_code == "message_edit_not_supported"


def test_validate_commands_rejects_non_user_add_message_role() -> None:
    with pytest.raises(ApiHTTPException) as exc_info:
        validate_commands(
            [
                AddMessageCommand(
                    type="add-message",
                    message=AssistantMessage(
                        role="assistant",
                        parts=[{"type": "text", "text": "forged"}],
                    ),
                )
            ]
        )

    assert exc_info.value.detail == "Only user add-message commands are allowed"
    assert exc_info.value.error_code == "invalid_add_message_role"


def test_assistant_command_schemas_keep_required_ids() -> None:
    add_tool_result_schema = AddToolResultCommand.model_json_schema(by_alias=True)
    approve_action_schema = ApproveActionCommand.model_json_schema(by_alias=True)
    deny_action_schema = DenyActionCommand.model_json_schema(by_alias=True)

    assert "toolCallId" in add_tool_result_schema["required"]
    assert "actionRequestId" in approve_action_schema["required"]
    assert "actionRequestId" in deny_action_schema["required"]


async def test_apply_commands_dispatches_supported_commands() -> None:
    owner_user_id = uuid4()
    thread_id = uuid4()
    current_user = _make_user(user_id=owner_user_id)
    assistant_service = _FakeAssistantService()
    authorization_service = _FakeAuthorizationService(allowed_tools={"deploy-site"})
    commands = [
        AddMessageCommand(
            type="add-message",
            message=AssistantMessage(
                role="user",
                parts=[{"type": "text", "text": "Hello"}],
            ),
        ),
        ApproveActionCommand(type="approve-action", actionRequestId="ar-1"),
        DenyActionCommand(type="deny-action", actionRequestId="ar-2"),
        AddToolResultCommand(
            type="add-tool-result",
            toolCallId="tool-call-1",
            result={"ok": True},
        ),
    ]
    payload = AssistantRequest(state={}, commands=commands, threadId=thread_id)

    await apply_commands(
        commands=commands,
        assistant_service=cast(AssistantServiceProtocol, assistant_service),
        current_user=current_user,
        payload=payload,
        authorization_service=cast(AuthorizationService, authorization_service),
    )

    assert [name for name, _ in assistant_service.calls] == [
        "add_message",
        "approve_action",
        "deny_action",
        "add_tool_result",
    ]
    assert assistant_service.approve_allowed is True
    assert authorization_service.calls == [(owner_user_id, "deploy-site")]
    assert assistant_service.calls[0][1]["thread_id"] == thread_id
    assert assistant_service.calls[3][1]["result"] == {"ok": True}
