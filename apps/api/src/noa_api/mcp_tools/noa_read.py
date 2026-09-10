"""NOA's own READ tool (`noa_get_action_result`).

Every other tool here asks a hosting system a question. This one asks NOA: **what happened to
the change I asked for?** It is the read side of the approval loop — the gate that opens the
request opens a request, an operator answers it from the card, and this is how the model finds
out, from the row that *is* the authorization rather than from anything it was told.

**The caller is the access control**. The requester comes from the MCP access token
(`current_mcp_identity`), never from an argument, and the repository puts it in the
`WHERE` — so a request that is not the caller's is not fetched at all. A foreign id and an
unknown id answer the same bytes, which is what stops this tool being an enumeration oracle
and what stops a prompt injection pulling another operator's action into the transcript.

**One refusal code for the whole family.** `action_request_not_found` covers absent, foreign,
requester-deleted *and* malformed — the last one because the identifier is taken as a `str` and
parsed here rather than declared as a UUID in the schema. A schema-level UUID would answer a
pydantic-shaped error for a malformed id, which is a second envelope for "there is no such
request", and one code is what the one-seam rule settled for the RBAC gate one layer up.

**What the model is not told.** The operator's reason never appears here and
neither does the in-process preflight evidence. Neither is stripped: `ActionResultView`
has no field for either, so this module cannot emit what it never loads — see
`core.approvals.results`.

Registration declares `ToolRisk.READ`, so `ToolRunAuditMiddleware` writes this
call's own `tool_runs` row beside the RBAC gate (the audit-every-read rule, the one-seam rule).
Reading an approval is itself an
audited read.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from pydantic import Field

from core.approvals.errors import ActionRequestNotFoundError
from core.db.lifecycle import ToolRisk
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext, build_action_result_service
from noa_api.mcp_tools.results import ToolPayload, sanitize_tool_errors, tool_failure, tool_ok

TOOL_NOA_GET_ACTION_RESULT = "noa_get_action_result"

# Read off the class rather than retyped, so this tool and the decision endpoint answer one
# code for
# one fact: "no such request, or not yours". The *message* is this surface's own — the endpoint
# says "not yours to decide", and nothing is being decided here.
ERROR_ACTION_REQUEST_NOT_FOUND = ActionRequestNotFoundError.error_code

MESSAGE_ACTION_REQUEST_NOT_FOUND = (
    "No approval request with that id belongs to you. Check the id, or ask for the change "
    "again to open a new request."
)

DESCRIPTION_NOA_GET_ACTION_RESULT = (
    "Look up what happened to a change that was submitted for approval: whether the operator "
    "approved, denied or let it expire, and how far its execution got. Use it when the "
    "operator asks about a change you already submitted, and pass the approval request id "
    "that was returned when it was submitted. Only requests you submitted are visible. "
    "Read-only: it changes nothing and it does not approve anything."
)


@sanitize_tool_errors(TOOL_NOA_GET_ACTION_RESULT)
async def noa_get_action_result(
    *,
    action_request_id: str,
    context: McpToolContext,
) -> ToolPayload:
    """What became of one approval request, for the operator who opened it.

    The identifier is parsed before any I/O, and a malformed one is refused with the same
    code and the same message an unknown id gets: there is no request behind either, and two
    codes would tell a caller which of their guesses was well-formed.

    Then one database session: read the caller's request, and — inside the same session —
    make it terminal if its deadline has passed, so a PENDING nobody may act on any more is
    never served. Both are `core.approvals.results`' to order; the tool supplies the
    caller and shapes the answer.
    """
    request_id = _parse_action_request_id(action_request_id)
    if request_id is None:
        return _not_found()

    identity = current_mcp_identity()

    async with context.session_factory() as session:
        service = build_action_result_service(context, session)
        view = await service.result_for(
            action_request_id=request_id,
            # The token's caller, never an argument: an argument-supplied requester would be
            # an argument-supplied authorization, and this is the surface the requester-match
            # rule names.
            requester_user_id=identity.user_id,
        )

    if view is None:
        return _not_found()
    return tool_ok(**view.as_payload())


def _parse_action_request_id(action_request_id: str) -> UUID | None:
    """The id as a `UUID`, or `None` when it is not one."""
    try:
        return UUID(action_request_id.strip())
    except (AttributeError, ValueError):
        return None


def _not_found() -> ToolPayload:
    """The one refusal this tool has."""
    return tool_failure(ERROR_ACTION_REQUEST_NOT_FOUND, MESSAGE_ACTION_REQUEST_NOT_FOUND)


def register_noa_read_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register NOA's own READ tools on `server`; return each name with its risk (the MCP
    server's contract, risk and status kept separate).

    One entry, and the MCP server's contract lists no other tool about NOA itself. It sits in
    its own module
    rather than beside a system's tools because its subject is the approval gate, not WHM,
    Proxmox or PMG.
    """

    @server.tool(
        name=TOOL_NOA_GET_ACTION_RESULT,
        description=DESCRIPTION_NOA_GET_ACTION_RESULT,
        # Standard MCP hint. The guarantee that matters is structural — this tool has no
        # writer behind it at all (`core.approvals.results` holds only `SELECT`s, and the one
        # write on the path can set `EXPIRED` and nothing else).
        annotations={"readOnlyHint": True},
    )
    async def noa_get_action_result_tool(
        action_request_id: Annotated[
            str,
            Field(
                description=(
                    "The approval request id NOA returned when the change was submitted for "
                    "approval. Pass it exactly as it was given."
                )
            ),
        ],
    ) -> ToolPayload:
        return await noa_get_action_result(
            action_request_id=action_request_id,
            context=context,
        )

    return {TOOL_NOA_GET_ACTION_RESULT: ToolRisk.READ}


__all__ = [
    "DESCRIPTION_NOA_GET_ACTION_RESULT",
    "ERROR_ACTION_REQUEST_NOT_FOUND",
    "MESSAGE_ACTION_REQUEST_NOT_FOUND",
    "TOOL_NOA_GET_ACTION_RESULT",
    "noa_get_action_result",
    "register_noa_read_tools",
]
