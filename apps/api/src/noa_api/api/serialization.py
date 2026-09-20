"""Wire formats shared by the admin routers.

One function and one model, here rather than duplicated because several routers now render the
same nullable timestamp and the same empty success body: `admin_users.py` has since the user
routes landed, `mcp_tokens.py` does for six columns at once. Two private copies would be two
places for a format to drift, and the panel parses both with the same `formatDate` /
`formatRelativeTime` helpers.

Explicit rather than left to FastAPI's datetime encoder, which is the property worth keeping:
the wire format is decided in NOA's own code, so a serializer setting changed elsewhere cannot
silently move it — and `null` stays `null`, which is what the panel derives "never used" and
"never expires" from.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


def iso_or_none(value: datetime | None) -> str | None:
    """ISO-8601 string, `None` preserved."""
    return value.isoformat() if value is not None else None


class OkResponse(BaseModel):
    """`{ok: true}`. The row is gone, so there is nothing to return."""

    ok: bool
