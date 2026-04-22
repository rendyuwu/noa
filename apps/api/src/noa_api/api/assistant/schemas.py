from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AssistantThreadStateMessage(BaseModel):
    id: str
    role: str
    parts: list[dict[str, Any]]


class AssistantWorkflowTodo(BaseModel):
    content: str
    status: str
    priority: str


class AssistantPendingApproval(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    tool_name: str = Field(alias="toolName")
    risk: str
    arguments: dict[str, Any]
    status: str

    model_config = {"populate_by_name": True}


class AssistantActionRequest(BaseModel):
    action_request_id: str = Field(alias="actionRequestId")
    tool_name: str = Field(alias="toolName")
    risk: str
    arguments: dict[str, Any]
    status: str
    lifecycle_status: str = Field(alias="lifecycleStatus")

    model_config = {"populate_by_name": True}


class AssistantThreadStateResponse(BaseModel):
    messages: list[AssistantThreadStateMessage]
    workflow: list[AssistantWorkflowTodo] = Field(default_factory=list)
    pending_approvals: list[AssistantPendingApproval] = Field(
        default_factory=list,
        alias="pendingApprovals",
    )
    action_requests: list[AssistantActionRequest] = Field(
        default_factory=list,
        alias="actionRequests",
    )
    is_running: bool = Field(alias="isRunning")
    run_status: str | None = Field(default=None, alias="runStatus")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    waiting_for_approval: bool = Field(default=False, alias="waitingForApproval")
    last_error_reason: str | None = Field(default=None, alias="lastErrorReason")

    model_config = {"populate_by_name": True}


class AssistantRunAckResponse(BaseModel):
    thread_id: str = Field(alias="threadId")
    active_run_id: str | None = Field(default=None, alias="activeRunId")
    run_status: str | None = Field(default=None, alias="runStatus")

    model_config = {"populate_by_name": True}
