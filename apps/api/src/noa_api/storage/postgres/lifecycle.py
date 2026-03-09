from __future__ import annotations

from enum import StrEnum


class ToolRisk(StrEnum):
    READ = "READ"
    CHANGE = "CHANGE"


class ActionRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class ToolRunStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
