from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.registry import require_matching_preflight
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.server_ref import resolve_whm_server_ref


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def validate_matching_preflight(
    *,
    tool_name: str,
    args: dict[str, object],
    working_messages: list[dict[str, object]],
    requested_server_id: str | None = None,
) -> SanitizedToolError | None:
    return require_matching_preflight(
        tool_name=tool_name,
        args=args,
        working_messages=working_messages,
        requested_server_id=requested_server_id,
    )


async def resolve_requested_server_id(
    *, args: dict[str, object], session: AsyncSession | None
) -> str | None:
    requested_server_ref = _normalized_text(args.get("server_ref"))
    if requested_server_ref is None or session is None:
        return None
    if not hasattr(session, "execute"):
        return None

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(requested_server_ref, repo=repo)
    if not resolution.ok or resolution.server_id is None:
        return None
    return str(resolution.server_id)
