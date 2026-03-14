from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import status
from pydantic import BaseModel, Field

from noa_api.api.error_codes import (
    INVALID_ADD_MESSAGE_ROLE,
    MESSAGE_EDIT_NOT_SUPPORTED,
)
from noa_api.api.error_handling import ApiHTTPException
from noa_api.core.auth.authorization import AuthorizationService, AuthorizationUser


class AssistantMessage(BaseModel):
    role: str
    parts: list[dict[str, Any]]


class AddMessageCommand(BaseModel):
    type: Literal["add-message"]
    message: AssistantMessage
    parent_id: str | None = Field(default=None, alias="parentId")
    source_id: str | None = Field(default=None, alias="sourceId")

    model_config = {"populate_by_name": True}


class ApproveActionCommand(BaseModel):
    type: Literal["approve-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


class DenyActionCommand(BaseModel):
    type: Literal["deny-action"]
    action_request_id: str | None = Field(default=None, alias="actionRequestId")

    model_config = {"populate_by_name": True}


class AddToolResultCommand(BaseModel):
    type: Literal["add-tool-result"]
    tool_call_id: str = Field(alias="toolCallId")
    result: dict[str, Any]

    model_config = {"populate_by_name": True}


AssistantCommand = Annotated[
    AddMessageCommand | ApproveActionCommand | DenyActionCommand | AddToolResultCommand,
    Field(discriminator="type"),
]


class AssistantRequest(BaseModel):
    state: dict[str, Any] = Field(default_factory=dict)
    commands: list[AssistantCommand] = Field(default_factory=list)
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    thread_id: UUID = Field(alias="threadId")

    model_config = {"populate_by_name": True}


class AssistantServiceProtocol(Protocol):
    async def add_message(
        self,
        *,
        owner_user_id: Any,
        thread_id: Any,
        role: str,
        parts: list[dict[str, object]],
    ) -> None: ...

    async def approve_action(
        self,
        *,
        owner_user_id: Any,
        owner_user_email: str | None,
        thread_id: Any,
        action_request_id: str | None,
        is_user_active: bool,
        authorize_tool_access: Any,
    ) -> None: ...

    async def deny_action(
        self,
        *,
        owner_user_id: Any,
        owner_user_email: str | None,
        thread_id: Any,
        action_request_id: str | None,
    ) -> None: ...

    async def add_tool_result(
        self,
        *,
        owner_user_id: Any,
        owner_user_email: str | None,
        thread_id: Any,
        tool_call_id: str,
        result: dict[str, object],
    ) -> None: ...


def validate_commands(commands: list[AssistantCommand]) -> None:
    for command in commands:
        if isinstance(command, AddMessageCommand) and (
            command.parent_id is not None or command.source_id is not None
        ):
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Editing existing messages is not supported yet",
                error_code=MESSAGE_EDIT_NOT_SUPPORTED,
            )
        if isinstance(command, AddMessageCommand) and command.message.role != "user":
            raise ApiHTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only user add-message commands are allowed",
                error_code=INVALID_ADD_MESSAGE_ROLE,
            )


async def apply_commands(
    *,
    commands: list[AssistantCommand],
    assistant_service: AssistantServiceProtocol,
    current_user: AuthorizationUser,
    payload: AssistantRequest,
    authorization_service: AuthorizationService,
) -> None:
    for command in commands:
        if isinstance(command, AddMessageCommand):
            await assistant_service.add_message(
                owner_user_id=current_user.user_id,
                thread_id=payload.thread_id,
                role=command.message.role,
                parts=command.message.parts,
            )
        elif isinstance(command, ApproveActionCommand):
            await assistant_service.approve_action(
                owner_user_id=current_user.user_id,
                owner_user_email=current_user.email,
                thread_id=payload.thread_id,
                action_request_id=command.action_request_id,
                is_user_active=current_user.is_active,
                authorize_tool_access=lambda tool_name: (
                    authorization_service.authorize_tool_access(
                        current_user,
                        tool_name,
                    )
                ),
            )
        elif isinstance(command, DenyActionCommand):
            await assistant_service.deny_action(
                owner_user_id=current_user.user_id,
                owner_user_email=current_user.email,
                thread_id=payload.thread_id,
                action_request_id=command.action_request_id,
            )
        elif isinstance(command, AddToolResultCommand):
            await assistant_service.add_tool_result(
                owner_user_id=current_user.user_id,
                owner_user_email=current_user.email,
                thread_id=payload.thread_id,
                tool_call_id=command.tool_call_id,
                result=command.result,
            )


def should_run_agent(commands: list[AssistantCommand]) -> bool:
    return any(
        (isinstance(command, AddMessageCommand) and command.message.role == "user")
        or isinstance(command, ApproveActionCommand)
        or isinstance(command, AddToolResultCommand)
        for command in commands
    )
