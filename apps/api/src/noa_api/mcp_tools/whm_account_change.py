"""WHM account CHANGE tools: `whm_suspend_account` (T22).

**The first CHANGE tool NOA exposes**, and therefore the first call that runs the whole gate
over the real mount: `tools/call` → in-process preflight → `action_requests(PENDING)` → the
approval card → an operator's cookie POST (T37) → the executor (T38) → the runner at the bottom
of this module. Everything above the runner was already built and, until now, vacuous.

**Two halves, on opposite sides of V22's boundary, in one module.** The tool is what the LLM can
reach and it changes nothing; the runner performs the suspension and is reachable only from
`core.approvals.execution`, which is reachable only from an approval. They live together because
they are two moments of one workflow and because the runner acts on the evidence the tool
gathered — splitting them would put the before-state and the change that answers it in two files
that can drift. What keeps the split honest is that neither calls the other:
`whm_suspend_account` holds no reference to the runner, and the runner is dispatched by tool name
from a registry the MCP path never reads (`noa_api.mcp_tools.change_runners`).

**The preflight runs inside the call** (C9, V17), and it is `fetch_whm_accounts` — T20/T21's
internal, not a second copy of "resolve a server and list its accounts" (V66). The evidence it
produces is born in-process, lives milliseconds, belongs to the same user, and reaches the
operator's card through `approval_context` rather than through a transcript. That is the whole of
DECISIONS §3.2: no evidence store, no freshness window, no `require_preflight` protocol.

**An account that is already suspended is answered, not gated.** The preflight is what discovers
it, and asking an operator to authorise a change that would do nothing is worse than saying so.
No `action_requests` row is written on that path, so the only trace is a structured log line —
which is the right amount of trace for a call that changed nothing (a CHANGE tool's `tools/call`
writes no `tool_runs` row either, T73).

**The runner acts on the server the card described, not on the operator's word.** `server_ref` is
whatever the model passed, and inventory can be edited between a request and its approval;
`evidence["server_id"]` is the machine the preflight actually read and the operator actually saw
(V33). Re-resolving the string here would be a second resolution that can disagree with the one
the decision rests on.

**The suspension note is the operator's reason.** WHM's `suspendacct` takes one, and C8's single
field is the only text NOA has that belongs there — the LLM never authored it, never relayed it
and never saw it, and it is read from `action_requests.reason` after the decision committed
(`core.approvals.execution`). The consequence is deliberate and bounded: WHM echoes that note
back as `suspendreason`, so `whm_search_accounts` withholds the field from the rows it returns to
a model (`ACCOUNT_FIELDS_WITHHELD_FROM_MODEL`), and nothing here puts it in a payload either —
not the no-op answer, not the runner's. `whm_list_accounts`' parked table keeps the column,
because that page is behind the operator's own cookie.

**Postflight, and its third answer.** A change WHM accepted is re-read to confirm it took. Two
outcomes are obvious — suspended, or not suspended and therefore a failure — and the third is the
one worth naming: the mutation succeeded and the confirming read did not answer. That is recorded
as a change that happened and was *not verified*, never as a failure and never as a silent pass.
V62's rule one system over: verification-unavailable is not verification.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastmcp import FastMCP
from pydantic import Field

from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.db.lifecycle import ToolRisk
from core.integrations.whm.accounts import WHMAccount, normalize_whm_account_list
from core.integrations.whm.client import WHMClient
from noa_api.mcp_tools.change_gate import build_change_gate_response, open_change_request
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)
from noa_api.mcp_tools.whm_read import MESSAGE_LIST_ACCOUNTS_FAILED, fetch_whm_accounts

TOOL_WHM_SUSPEND_ACCOUNT = "whm_suspend_account"

# The evidence keys the tool writes and the runner reads back. Constants because they cross a
# boundary in time as well as in code — the tool writes them into `approval_context` JSONB and
# the runner reads them minutes later — and a misspelt key in JSONB reads as an absent one (V66,
# the argument `core.approvals.context` makes one level up).
EVIDENCE_SERVER_ID = "server_id"
EVIDENCE_SERVER_NAME = "server"
EVIDENCE_ACCOUNT = "account"

ERROR_USERNAME_REQUIRED = "username_required"
ERROR_ACCOUNT_NOT_FOUND = "account_not_found"
# The approved change names a server that is no longer resolvable — deleted, or the evidence no
# longer parses. Distinct from the tool-time resolution failures, which the model can fix by
# asking again: by the time this fires an operator has already approved something.
ERROR_SERVER_UNAVAILABLE = "whm_server_unavailable"
# WHM accepted the suspension and the confirming read says the account is still live.
ERROR_POSTFLIGHT_FAILED = "postflight_failed"

MESSAGE_USERNAME_REQUIRED = "A cPanel account username is required."
MESSAGE_SERVER_UNAVAILABLE = (
    "The WHM server this change was approved for is no longer available. Contact an administrator."
)
MESSAGE_SUSPEND_FAILED = "WHM did not suspend the account."
MESSAGE_POSTFLIGHT_FAILED = "WHM accepted the suspension but the account is not suspended."

# The account was already suspended when the preflight looked: nothing was asked of an operator
# and nothing was changed.
STATUS_NO_OP = "no_op"
STATUS_CHANGED = "changed"

# The postflight read could not answer. The change happened; whether it took is unconfirmed.
VERIFICATION_UNAVAILABLE = "unavailable"

# One structured event per call that found nothing to do, so "why is there no approval card" is
# answerable from the logs. Identifiers only, never the account payload (V8).
LOG_SUSPEND_NO_OP = "whm_suspend_account_no_op"

# The change ran and could not be confirmed. Warning, because an operator may want to look.
LOG_SUSPEND_UNVERIFIED = "whm_suspend_account_unverified"

DESCRIPTION_WHM_SUSPEND_ACCOUNT = (
    "Suspend one cPanel account on one WHM server. This changes a live system, so it does not "
    "run when you call it: NOA checks the account, opens an approval request, and answers with "
    "the address of a card where an operator decides. Call `whm_search_accounts` first to get "
    "the exact username; never guess one. Read the outcome with `noa_get_action_result`, and "
    "never report the account as suspended without it."
)

logger = structlog.get_logger(__name__)


async def collect_account_state(
    *,
    server_ref: str,
    username: str,
    context: McpToolContext,
) -> ToolPayload:
    """One account's current state on one WHM server. Internal — ⊥ an MCP tool (C9, V17).

    The before-state an operator authorises against, and the same function T23 will call. Not
    decorated with `sanitize_tool_errors`: its caller is an exposed tool that already is, and a
    second boundary would turn a `NoaError` into a payload the caller then has to unwrap twice
    (`fetch_whm_accounts`' rule, one module over).

    **Built on `fetch_whm_accounts`** rather than beside it. WHM has no per-account read NOA
    needs here — `listaccts` answers for the whole server and the field list NOA speaks about is
    already pinned against it — so a second resolve-and-list would be two spellings of one
    round trip, and the day they disagree the card describes a machine the tool did not read.

    Failures travel back as payloads rather than exceptions, because each is something the model
    can act on (V18, V19): a `server_ref` that named nothing or several things keeps the
    resolver's own code and its `choices`, and a WHM that refused keeps `WHMClient`'s stable code
    — those strings say which system to fix.

    The match is exact on `user`. A CHANGE that guessed which account an operator meant is what
    C10 exists to prevent, and `whm_search_accounts` is the discovery step in front of it.
    """
    listed = await fetch_whm_accounts(server_ref=server_ref, context=context)
    if listed.get("ok") is not True:
        # Already a structured failure with its own code and `choices`. Re-wrapping renames it.
        return listed

    account = match_account(listed.get("accounts"), username=username)
    if account is None:
        return tool_failure(
            ERROR_ACCOUNT_NOT_FOUND,
            f"No cPanel account named `{username}` exists on this WHM server.",
        )

    return tool_ok(
        **{
            EVIDENCE_SERVER_ID: listed.get(EVIDENCE_SERVER_ID),
            EVIDENCE_SERVER_NAME: listed.get(EVIDENCE_SERVER_NAME),
            EVIDENCE_ACCOUNT: account,
        }
    )


def match_account(accounts: object, *, username: str) -> WHMAccount | None:
    """The one normalised row whose `user` is exactly `username`, or `None`.

    Exact, case-sensitive: cPanel usernames are case-sensitive and a CHANGE tool that
    case-folded would suspend `acme` when an operator approved `Acme`.
    """
    for account in accounts if isinstance(accounts, list) else []:
        if isinstance(account, dict) and account.get("user") == username:
            return account
    return None


@sanitize_tool_errors(TOOL_WHM_SUSPEND_ACCOUNT)
async def whm_suspend_account(
    *,
    server_ref: str,
    username: str,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one cPanel account to be suspended; suspend nothing (T22 — V16, V17, V23).

    Four things happen and none of them is a change: a malformed request is refused, the account
    is read, the call decides whether there is anything to do, and the question is opened.

    - a blank or whitespace-only `username` is refused before any I/O (V21). The schema cannot
      express it — `min_length` counts whitespace — so this is the gate, and `listaccts` would
      otherwise be fetched in order to match nothing.
    - the preflight's failures pass straight through with their own codes and `choices` (V18).
    - an account that is **already suspended** is answered `no_op` and no request is opened. That
      payload names the account and the server and carries nothing else: it is transcript (V26),
      and the account summary holds WHM's own `suspendreason`, which as of T22 is the operator's
      reason (C8).
    - otherwise `open_change_request` writes the PENDING row with the preflight as evidence (V33,
      V35) and `build_change_gate_response` turns its id into the card's address and the iframe
      (V24, V25).

    No `reason` parameter, and nowhere to add one: the word is typed by an operator on the card,
    after this result has been rendered and forgotten (C8, V15, V43). The gate refuses a
    reason-shaped argument as well (`assert_no_reason_argument`), so the boundary holds even for
    a caller that reaches this function directly rather than over MCP.
    """
    normalized_username = username.strip()
    if not normalized_username:
        return tool_failure(ERROR_USERNAME_REQUIRED, MESSAGE_USERNAME_REQUIRED)

    state = await collect_account_state(
        server_ref=server_ref, username=normalized_username, context=context
    )
    if state.get("ok") is not True:
        return state

    account = state[EVIDENCE_ACCOUNT]
    server_name = state.get(EVIDENCE_SERVER_NAME)
    if account.get("suspended") is True:
        logger.info(
            LOG_SUSPEND_NO_OP,
            tool=TOOL_WHM_SUSPEND_ACCOUNT,
            server_id=state.get(EVIDENCE_SERVER_ID),
            username=normalized_username,
        )
        return tool_ok(
            status=STATUS_NO_OP,
            server=server_name,
            username=normalized_username,
            suspended=True,
            message=f"`{normalized_username}` is already suspended; nothing to approve.",
        )

    opened = await open_change_request(
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        arguments={"server_ref": server_ref, "username": normalized_username},
        evidence={
            EVIDENCE_SERVER_ID: state.get(EVIDENCE_SERVER_ID),
            EVIDENCE_SERVER_NAME: server_name,
            EVIDENCE_ACCOUNT: account,
        },
        context=context,
    )
    return build_change_gate_response(opened, tool_name=TOOL_WHM_SUSPEND_ACCOUNT, context=context)


def build_whm_suspend_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that suspends, reachable only after an operator approved (T22, T38 — V22, V46).

    A closure over the tool context rather than a class: what it needs is the same session
    factory, cipher and client factory the tool used, and holding them by reference is what makes
    the change go through the production decrypt site rather than a second one.
    """

    async def run(request: ChangeExecutionRequest) -> ToolPayload:
        """Suspend the account this approved request names, and say what happened.

        Answers the ordinary tool envelope (`noa_api.mcp_tools.results`), because the executor
        classifies the run and bounds the summary off it. It does not raise, for the reason a
        tool does not (V19): the executor catches, but what it can record then is coarser than
        what this knew.

        The server and the account both come from the **evidence**, never from the arguments:
        that is what the operator saw on the card, and `evidence["server_id"]` is the machine the
        preflight actually read (V33).

        The session closes before the WHM round trips, T21's rule — and here it matters twice
        over, because the executor's own session is open for the whole of this call.
        """
        server_id = _uuid_or_none(request.evidence.get(EVIDENCE_SERVER_ID))
        account = request.evidence.get(EVIDENCE_ACCOUNT)
        username = account.get("user") if isinstance(account, dict) else None
        if server_id is None or not isinstance(username, str) or not username:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

        async with context.session_factory() as session:
            repository = context.whm_server_repository_factory(session)
            server = await repository.get_by_id(server_id)
            if server is None:
                return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
            client = context.whm_client_factory(server, cipher=context.secret_cipher)
            server_name = server.name

        # C8's single field, written where WHM keeps a suspension note. The operator typed it,
        # the LLM never saw it, and it is not echoed back in the payload below — that payload
        # becomes the receipt's after-state, which T63 reads out to a model (V76).
        mutation = await client.suspend_account(username=username, reason=request.reason)
        if mutation.get("ok") is not True:
            return _passthrough_failure(mutation, fallback=MESSAGE_SUSPEND_FAILED)

        return await _verify_suspended(
            client,
            username=username,
            server_name=server_name,
            action_request_id=request.action_request_id,
        )

    return run


def build_whm_account_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tools (T22; T23 lands beside it)."""
    return {TOOL_WHM_SUSPEND_ACCOUNT: build_whm_suspend_runner(context=context)}


def register_whm_account_change_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the WHM account CHANGE tools; return each name with its risk (I.mcp, V20).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call (T73) — it opens an approval request and executes nothing, and V46's row belongs to the
    executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for this name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_WHM_SUSPEND_ACCOUNT,
        description=DESCRIPTION_WHM_SUSPEND_ACCOUNT,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split
        # that matters is the approval gate (V16); the classification that matters is the risk
        # returned below.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def whm_suspend_account_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which WHM server: its id, its name in NOA, or its hostname. Call "
                    "`whm_list_servers` first if the operator has not named one."
                )
            ),
        ],
        username: Annotated[
            str,
            Field(
                description=(
                    "The exact cPanel account username to suspend, as `whm_search_accounts` "
                    "reports it in `user`. Must not be blank, and must not be guessed."
                )
            ),
        ],
    ) -> ToolAnswer:
        # Annotated with the union on purpose: the success path answers content blocks — the
        # text block and the approval iframe, in that order (V24, V25) — while every refusal
        # answers the envelope every tool shares.
        return await whm_suspend_account(server_ref=server_ref, username=username, context=context)

    return {TOOL_WHM_SUSPEND_ACCOUNT: ToolRisk.CHANGE}


# --- Internals ---


async def _verify_suspended(
    client: WHMClient,
    *,
    username: str,
    server_name: str,
    action_request_id: UUID,
) -> ToolPayload:
    """Re-read the account and say whether the suspension took (V62's rule, one system over).

    Three answers, and the middle one is why this is a function rather than a boolean:

    - the account reads suspended → done, and verified;
    - the account reads live → WHM accepted a call that did not take, which is a failure;
    - the read itself did not answer → the change happened and is **unverified**. Reporting that
      as a failure would send an operator to suspend an account that may already be suspended;
      reporting it as a plain success would claim a confirmation nobody has.
    """
    result = await client.list_accounts()
    verified = (
        match_account(normalize_whm_account_list(result.get("accounts")), username=username)
        if result.get("ok") is True
        else None
    )

    if verified is None:
        logger.warning(
            LOG_SUSPEND_UNVERIFIED,
            tool=TOOL_WHM_SUSPEND_ACCOUNT,
            action_request_id=str(action_request_id),
            username=username,
            cause=str(result.get("error_code") or MESSAGE_LIST_ACCOUNTS_FAILED),
        )
        return tool_ok(
            status=STATUS_CHANGED,
            server=server_name,
            username=username,
            verified=False,
            verification=VERIFICATION_UNAVAILABLE,
            message=(
                f"WHM accepted the suspension of `{username}`, but the confirming read did not "
                "answer. Check the account on the server."
            ),
        )

    if verified.get("suspended") is not True:
        return tool_failure(ERROR_POSTFLIGHT_FAILED, MESSAGE_POSTFLIGHT_FAILED)

    return tool_ok(
        status=STATUS_CHANGED,
        server=server_name,
        username=username,
        suspended=True,
        verified=True,
        message=f"`{username}` is suspended.",
    )


def _passthrough_failure(result: dict[str, object], *, fallback: str) -> ToolPayload:
    """A `WHMClient` failure as a tool failure, keeping the code that names the remedy."""
    message = result.get("message")
    spoken = message if isinstance(message, str) and message.strip() else None
    return tool_failure(str(result.get("error_code") or ERROR_UNKNOWN), spoken or fallback)


def _uuid_or_none(value: Any) -> UUID | None:
    """The evidence's `server_id` as a `UUID`, or `None` when it is not one.

    It round-tripped through JSONB as a string, and a value that no longer parses is a request
    NOA refuses rather than guesses at.
    """
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


__all__ = [
    "DESCRIPTION_WHM_SUSPEND_ACCOUNT",
    "ERROR_ACCOUNT_NOT_FOUND",
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_SERVER_UNAVAILABLE",
    "ERROR_USERNAME_REQUIRED",
    "EVIDENCE_ACCOUNT",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "LOG_SUSPEND_NO_OP",
    "LOG_SUSPEND_UNVERIFIED",
    "MESSAGE_POSTFLIGHT_FAILED",
    "MESSAGE_SERVER_UNAVAILABLE",
    "MESSAGE_SUSPEND_FAILED",
    "MESSAGE_USERNAME_REQUIRED",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "TOOL_WHM_SUSPEND_ACCOUNT",
    "VERIFICATION_UNAVAILABLE",
    "build_whm_account_change_runners",
    "build_whm_suspend_runner",
    "collect_account_state",
    "match_account",
    "register_whm_account_change_tools",
    "whm_suspend_account",
]
