from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from noa_api.api.error_codes import (
    ACTION_REQUEST_ALREADY_DECIDED,
    ACTION_REQUEST_NOT_FOUND,
    CHANGE_APPROVAL_REQUIRED,
    INVALID_ACTION_REQUEST_ID,
    INVALID_TOOL_CALL_ID,
    MISSING_ACTION_REQUEST_ID,
    MISSING_TOOL_CALL_ID,
    TOOL_ACCESS_DENIED,
    TOOL_CALL_NOT_AWAITING_RESULT,
    TOOL_CALL_NOT_FOUND,
    UNKNOWN_TOOL_CALL_ID,
    USER_PENDING_APPROVAL,
)


class AssistantDomainError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        error_code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.error_code = error_code


def assistant_http_error(
    *,
    status_code: int,
    detail: str,
    error_code: str | None = None,
) -> HTTPException:
    headers = {"x-error-code": error_code} if error_code is not None else None
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def assistant_domain_error(
    *,
    status_code: int,
    detail: str,
    error_code: str | None = None,
) -> AssistantDomainError:
    return AssistantDomainError(
        status_code=status_code,
        detail=detail,
        error_code=error_code,
    )


def to_assistant_http_error(exc: AssistantDomainError) -> HTTPException:
    return assistant_http_error(
        status_code=exc.status_code,
        detail=exc.detail,
        error_code=exc.error_code,
    )


def parse_tool_call_id(raw: str | None) -> UUID:
    if raw is None:
        raise missing_tool_call_id_error()
    try:
        return UUID(raw)
    except ValueError as exc:
        raise invalid_tool_call_id_error() from exc


def missing_tool_call_id_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing toolCallId",
        error_code=MISSING_TOOL_CALL_ID,
    )


def invalid_tool_call_id_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid toolCallId",
        error_code=INVALID_TOOL_CALL_ID,
    )


def unknown_tool_call_id_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unknown tool call id",
        error_code=UNKNOWN_TOOL_CALL_ID,
    )


def tool_call_not_found_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Tool call not found",
        error_code=TOOL_CALL_NOT_FOUND,
    )


def tool_call_not_awaiting_result_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_409_CONFLICT,
        detail="Tool call is not awaiting result",
        error_code=TOOL_CALL_NOT_AWAITING_RESULT,
    )


def parse_action_request_id(raw: str | None) -> UUID:
    if raw is None:
        raise missing_action_request_id_error()
    try:
        return UUID(raw)
    except ValueError as exc:
        raise invalid_action_request_id_error() from exc


def missing_action_request_id_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing actionRequestId",
        error_code=MISSING_ACTION_REQUEST_ID,
    )


def invalid_action_request_id_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid actionRequestId",
        error_code=INVALID_ACTION_REQUEST_ID,
    )


def action_request_not_found_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Action request not found",
        error_code=ACTION_REQUEST_NOT_FOUND,
    )


def action_request_already_decided_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_409_CONFLICT,
        detail="Action request already decided",
        error_code=ACTION_REQUEST_ALREADY_DECIDED,
    )


def user_pending_approval_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User pending approval",
        error_code=USER_PENDING_APPROVAL,
    )


def change_approval_required_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Only CHANGE actions require approval",
        error_code=CHANGE_APPROVAL_REQUIRED,
    )


def tool_access_denied_error() -> AssistantDomainError:
    return assistant_domain_error(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Tool access denied",
        error_code=TOOL_ACCESS_DENIED,
    )
