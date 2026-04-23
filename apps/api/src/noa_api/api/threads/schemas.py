from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from noa_api.storage.postgres.models import Thread


class ThreadResponse(BaseModel):
    id: str
    remote_id: str = Field(alias="remoteId")
    external_id: str | None = Field(alias="externalId")
    status: Literal["regular", "archived"]
    title: str | None
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}


class ThreadListResponse(BaseModel):
    threads: list[ThreadResponse]


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    local_id: str | None = Field(default=None, alias="localId", max_length=255)

    model_config = {"populate_by_name": True}

    @field_validator("title", "local_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                return None
            return normalized
        return value


class UpdateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if normalized == "":
                return None
            return normalized
        return value


class GenerateTitleRequest(BaseModel):
    messages: list[dict[str, object]] = Field(default_factory=list)


class GenerateTitleResponse(BaseModel):
    title: str


def _to_thread_response(thread: Thread) -> ThreadResponse:
    return ThreadResponse(
        id=str(thread.id),
        remoteId=str(thread.id),
        externalId=thread.external_id,
        status="archived" if thread.is_archived else "regular",
        title=thread.title,
        is_archived=thread.is_archived,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )
