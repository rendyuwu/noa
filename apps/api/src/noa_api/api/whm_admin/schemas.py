from __future__ import annotations

from pydantic import BaseModel


class ValidateWHMServerResponse(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str
