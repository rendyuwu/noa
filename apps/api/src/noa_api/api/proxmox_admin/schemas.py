from __future__ import annotations

from pydantic import BaseModel


class ValidateProxmoxServerResponse(BaseModel):
    ok: bool
    error_code: str | None = None
    message: str
