"""WHM account CHANGE tools: `whm_suspend_account`, `whm_unsuspend_account`.

**The first CHANGE tool NOA exposed**, and therefore the first call that runs the whole gate
over the real mount: `tools/call` → in-process preflight → `action_requests(PENDING)` → the
approval card → an operator's cookie POST → the executor → the runner one module
over. Everything above the runner was already built and, until the suspend tool, vacuous.

**Two halves, on opposite sides of the cookie/CSRF boundary, and now in two modules.** A tool is
what the LLM can reach and it changes nothing; a runner performs the change and is reachable only
from `core.approvals.execution`, which is reachable only from an approval. They lived together while
they fitted, because they are two moments of one workflow and a runner acts on the evidence its tool
gathered. They stopped fitting: this module reached the 900-line budget exactly, so the runners
moved to `whm_account_change_runner.py`, the split the Proxmox and PMG tools were built with from
the start. What keeps it honest is unchanged and is not the file boundary — neither tool holds a
reference to a runner, and a runner is dispatched by tool name from a registry the MCP path never
reads (`noa_api.mcp_tools.change_runners`).

Every constant stayed here, including the ones only the runners use, so the split moved code and
not vocabulary: a reader following a code out of a receipt still lands on the module that defines
it, and the runner module imports one direction only, which is what keeps the aggregate registrar
from being a cycle.

**Two tools, one shape, and the shape is shared rather than mirrored.** Suspend and unsuspend are
not merged — opposite risk directions, clearer as two names (DECISIONS section 9) — but everything
between the two names is one implementation: `collect_account_state` is the preflight for
both, and one module over, the resolution of an approved request into the client that performs it
and the postflight that confirms it are each written once, differing only in the value
`suspended` must hold when the change took. What is deliberately written twice is the surface a
model reads — the two tool functions, their descriptions and their registrations — because those
genuinely differ and a shared spelling of them would be one sentence trying to describe two
opposite acts.

**The preflight runs inside the call**, and it is `fetch_whm_accounts` — the read tools'
internal, not a second copy of "resolve a server and list its accounts". The evidence it
produces is born in-process, lives milliseconds, belongs to the same user, and reaches the
operator's card through `approval_context` rather than through a transcript. That is the whole of
DECISIONS section 3.2: no evidence store, no freshness window, no `require_preflight` protocol.

**An account already in the state the change would produce is answered, not gated.** The preflight
is what discovers it — already suspended for the suspend tool, not suspended at all for the
unsuspend one — and asking an operator to authorise a change that would do nothing is worse than
saying so. No `action_requests` row is written on that path, so the only trace is a structured log
line — which is the right amount of trace for a call that changed nothing (a CHANGE tool's
`tools/call` writes no `tool_runs` row either).

**A locked suspension is refused before a card exists**. WHM's `unsuspendacct` refuses an
account whose suspension is locked, so opening a request for one costs an operator a decision and
buys a run that fails — the no-op argument above, one state over. The lock is already on the
normalised summary (`core.integrations.whm.accounts`, which reads `is_locked` and falls back to
the older `suspendlock`), so the preflight that reads the account reads the lock with it.

The guard fires on a **positive** lock only, and that bound is deliberate rather than an
oversight: `listaccts` omits the field entirely on cPanel versions that do not have it, and
refusing every unsuspend on those servers would cost more than the failure it prevents. WHM's own
refusal at execute time stays the authoritative one — it arrives as `whm_api_error` carrying
WHM's `reason`, which names the remedy — and this guard is the cheap early half of it.

**An account whose suspension state WHM did not spell readably is refused too**, and that refusal
is the no-op argument's other edge: both tools decide whether there is anything to approve by
reading one boolean, so a value the normaliser could not read makes both answers wrong in opposite
directions — a second card for an account already suspended, or "nothing to approve" about an
account nobody read. The guard sits in `collect_account_state`, the shared preflight, because the
field is read once there and the wrong answers are two.

**A credential that does not own the account is refused before a card exists**, the same argument
one step harder, and `whm_account_owner_gate` holds all of it: cPanel gates an account write on
*ownership* rather than on the token's ACL (measured), so the preflight compares the account's
`owner` against the resolved row's `api_username`. Two things this module decides rather than
that one. The guard sits in `_open_account_change`, the single door both tools reach
`open_change_request` through, so a third account CHANGE tool cannot be written without it; and
it runs **again** in the runner, off the stored evidence, because a row is editable between a
request and its decision and repointing `api_username` after the card was rendered would
otherwise substitute the acting identity silently.

**The card and the receipt name the credential, not just the machine**: the row's
`name`, its `api_username`, the host out of its `base_url`, and the account's `owner`. A
privileged write whose credential is not recorded is not auditable, and what an audit needs is
which identity acted — a username, never the token.

**A runner acts on the server the card described, not on the operator's word.** `server_ref` is
whatever the model passed, and inventory can be edited between a request and its approval;
`evidence["server_id"]` is the machine the preflight actually read and the operator actually saw —
resolution comes from the evidence, never the arguments. Re-resolving the string there would be a
second resolution that can disagree with the one the decision rests on.

**The suspension note is the operator's reason, and only suspend has one.** WHM's `suspendacct`
takes a note, and the reason rule's single field is the only text NOA has that belongs there — the
LLM never authored it, never relayed it and never saw it, and it is read from
`action_requests.reason` after the decision committed (`core.approvals.execution`). What that costs
is two return paths, and both are closed: WHM echoes the note back as `suspendreason`, so
`whm_search_accounts` withholds the field from the rows it hands a model
(`ACCOUNT_FIELDS_WITHHELD_FROM_MODEL`); and `tool_runs.result_summary` is derived from a runner's
payload and read back by `noa_get_action_result`, so no payload here carries the note — not a no-op
answer, not a runner's. `whm_list_accounts`' parked table keeps the column, because that page is
behind the operator's own cookie.

`unsuspendacct` takes no note, so **the unsuspend tool writes nothing out and no path back to a
model opens on that side.** It does meet a case the suspend tool could not: an account being
unsuspended *is* suspended when the preflight reads it, so its summary carries `suspendreason` — an
operator's earlier words. That summary goes onto the row as evidence, where requester-match and the
card are its only readers (a model cannot reach it: `ActionResultView` has no field for evidence).
What is not closed by construction is this tool's own answers, which do land in a transcript — so
the no-op and the locked refusal are built from the username and the server name, never from the
summary.

**Postflight, and its third answer.** A change WHM accepted is re-read to confirm it took, and it
is re-read **through the credential that wrote it**: the identity that performed the change is the
one that confirms it. A reseller token's `listaccts` sees its own accounts (77 of 77 on the
measured host) and its own account is the only one in question, so moving the confirming
read to a root credential "so it can see everything" would answer "did it take" from an identity
that did not perform the write. One `_ChangeTarget`, one client, all three phases. Two
outcomes are obvious — the account reached the state that was asked for, or it did not and the
change is therefore a failure — and the third is the one worth naming: the mutation succeeded and
the confirming read did not answer. That is recorded as a change that happened and was *not
verified*, never as a failure and never as a silent pass. The verdict rule one system over:
verification-unavailable is not verification.
"""

from __future__ import annotations

from typing import Annotated, Final

import structlog
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.whm.accounts import WHMAccount, account_suspension_state
from noa_api.api.request_context import LOG_REQUEST_ID, SCOPE_STATE_REQUEST_ID
from noa_api.mcp_tools.change_gate import build_change_gate_response, open_change_request
from noa_api.mcp_tools.change_target import (
    # Hoisted to `change_target` with the release-and-allow tool, when the firewall runner became
    # the second caller of the same refusals and the same status words. Re-exported below, so every
    # name this module already published keeps working from here — including the two the runners one
    # module over are now the only readers of, because a code's home is where a reader looking it up
    # expects to find it.
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)
from noa_api.mcp_tools.whm_account_owner_gate import (
    # Hoisted with the owner-vs-credential guard, when the runner became the second caller of the
    # same refusal — the shape `change_target` was hoisted in. Re-exported below, so every name this
    # module publishes keeps working from here.
    ERROR_ACCOUNT_OWNER_UNKNOWN,
    ERROR_WRONG_CREDENTIAL_FOR_OWNER,
    EVIDENCE_API_USERNAME,
    EVIDENCE_HOST,
    EVIDENCE_OWNER,
    EVIDENCE_UNRECORDED,
    LOG_OWNERSHIP_REFUSED,
    SITE_PREFLIGHT,
    classify_ownership,
    recorded,
    refuse_unproven_ownership,
)
from noa_api.mcp_tools.whm_read import fetch_whm_accounts

TOOL_WHM_SUSPEND_ACCOUNT = "whm_suspend_account"
TOOL_WHM_UNSUSPEND_ACCOUNT = "whm_unsuspend_account"

# The evidence keys the tools write and the runners read back. Constants because they cross a
# boundary in time as well as in code — a tool writes them into `approval_context` JSONB and
# the runner reads them minutes later — and a misspelt key in JSONB reads as an absent one
# (reusable functions over duplication,
# the argument `core.approvals.context` makes one level up).
EVIDENCE_SERVER_ID = "server_id"
EVIDENCE_SERVER_NAME = "server"
EVIDENCE_ACCOUNT = "account"

ERROR_USERNAME_REQUIRED = "username_required"
ERROR_ACCOUNT_NOT_FOUND = "account_not_found"
# WHM accepted the mutation and the confirming read says it did not take.
ERROR_POSTFLIGHT_FAILED = "postflight_failed"
# The account's suspension is locked, and `unsuspendacct` refuses a locked account. A
# refusal rather than an approval request: the card would buy a decision and a failed run.
ERROR_SUSPENSION_LOCKED = "account_suspension_locked"
# WHM answered, and its `suspended` value was none of the spellings NOA reads. One code for both
# sites that meet it, because it is one fact: the preflight refuses with it rather than opening a
# card it cannot say is needed, and the postflight carries it as the cause of an unavailable
# verification rather than as a verdict. Absence is not `false` in either direction.
ERROR_SUSPENSION_STATE_UNREADABLE = "suspension_state_unreadable"

MESSAGE_USERNAME_REQUIRED = "A cPanel account username is required."
MESSAGE_SUSPEND_FAILED = "WHM did not suspend the account."
MESSAGE_UNSUSPEND_FAILED = "WHM did not unsuspend the account."
MESSAGE_POSTFLIGHT_SUSPEND_FAILED = "WHM accepted the suspension but the account is not suspended."
MESSAGE_POSTFLIGHT_UNSUSPEND_FAILED = (
    "WHM accepted the unsuspension but the account is still suspended."
)

# One structured event per call that found nothing to do, so "why is there no approval card" is
# answerable from the logs. Identifiers only, never the account payload.
LOG_SUSPEND_NO_OP = "whm_suspend_account_no_op"
LOG_UNSUSPEND_NO_OP = "whm_unsuspend_account_no_op"

# The same question with a different answer: there was something to do and WHM would refuse it.
LOG_UNSUSPEND_LOCKED = "whm_unsuspend_account_suspension_locked"

# One structured event per preflight that refused, and it is the only trace a refused CHANGE call
# leaves at all. Nothing else on this path writes one: `WHMClient` reports a timeout as a payload
# rather than an exception, so the error sanitiser — which logs only what raised — never sees it;
# the audit middleware writes no `tool_runs` row for a CHANGE tool by design; and a refusal opens
# no `action_requests` row. Without this line an operator who read "Request timed out" in chat has
# nothing to grep. The request id is not passed: `RequestContextMiddleware` binds it into
# structlog's contextvars for every HTTP request, the mounted MCP app included, and structlog's
# default processor chain merges it onto every event — so the id on this line is the same one the
# response carries in `x-request-id`.
LOG_PREFLIGHT_FAILED = "whm_account_preflight_failed"

# The change ran and could not be confirmed. Warning, because an operator may want to look.
LOG_SUSPEND_UNVERIFIED = "whm_suspend_account_unverified"
LOG_UNSUSPEND_UNVERIFIED = "whm_unsuspend_account_unverified"

DESCRIPTION_WHM_SUSPEND_ACCOUNT = (
    "Suspend one cPanel account on one WHM server. This changes a live system, so it does not "
    "run when you call it: NOA checks the account, opens an approval request, and answers with "
    "the address of a card where an operator decides. Call `whm_search_accounts` first to get "
    "the exact username and the account's `owner`; never guess either. `server_ref` is the "
    "credential for that owner, not the machine — see its own description; WHM only lets an "
    "account's owner suspend it. Read the outcome with "
    "`noa_get_action_result`, and never report the account as suspended without it."
)

DESCRIPTION_WHM_UNSUSPEND_ACCOUNT = (
    "Lift the suspension on one cPanel account on one WHM server. This changes a live system, "
    "so it does not run when you call it: NOA checks the account, opens an approval request, "
    "and answers with the address of a card where an operator decides. Call "
    "`whm_search_accounts` first to get the exact username and the account's `owner`; never "
    "guess either. `server_ref` is the credential for that owner, not the machine — see its own "
    "description; WHM only lets an account's owner lift its suspension. Read the outcome with "
    "`noa_get_action_result`, and never report the "
    "account as active without it."
)

# Both tools take the same `server_ref`, and it means the same thing in both. One string so the
# two schemas cannot drift into describing one argument two ways.
#
# It does **not** mean what it means on the read tools, and this is where a model learns that
# (owner-as-`server_ref`). Both branches are stated because neither covers the other: reseller
# rows are named
# after their credential (the name == `api_username` rule) and hidden from `whm_list_servers`
# (filtered from its output), while the root
# rows cannot all be called `root` — so for the 56 of 451 measured `owner=root` accounts
# (measured live on the host)
# the machine's own row is the answer. `refuse_unproven_ownership` carries the rest, including
# why "pass the owner" alone would be false.
SERVER_REF_DESCRIPTION: Final = (
    "Which WHM credential performs the change. For an account change that is the credential "
    "whose API username is the account's OWNER — read `owner` off `whm_search_accounts` first. "
    "If the owner is a reseller, NOA holds a server named exactly after it, so pass that owner "
    "name (those rows are not in `whm_list_servers`, so you will not have seen it there). If "
    "the owner is `root`, the machine's own row is the credential: pass its id, name or "
    "hostname from `whm_list_servers`. WHM refuses an account write from any other credential, "
    "so a wrong value here buys a refusal rather than an approval request."
)

logger = structlog.get_logger(__name__)


async def collect_account_state(
    *,
    tool_name: str,
    server_ref: str,
    username: str,
    context: McpToolContext,
) -> ToolPayload:
    """One account's current state on one WHM server. Internal — never an MCP tool.

    The before-state an operator authorises against, and the same function both account CHANGE
    tools call. Not decorated with `sanitize_tool_errors`: its callers are exposed
    tools that already are, and a second boundary would turn a `NoaError` into a payload the
    caller then has to unwrap twice (`fetch_whm_accounts`' rule, one module over).

    **Built on `fetch_whm_accounts`** rather than beside it. WHM has no per-account read NOA
    needs here — `listaccts` answers for the whole server and the field list NOA speaks about is
    already pinned against it — so a second resolve-and-list would be two spellings of one
    round trip, and the day they disagree the card describes a machine the tool did not read.

    Failures travel back as payloads rather than exceptions, because each is something the model
    can act on: a `server_ref` that named nothing or several things keeps the
    resolver's own code and its `choices`, and a WHM that refused keeps `WHMClient`'s stable code
    — those strings say which system to fix.

    The match is exact on `user`. A CHANGE that guessed which account an operator meant is what
    refusing to guess exists to prevent, and `whm_search_accounts` is the discovery step in front of
    it.

    **An account whose suspension state WHM did not spell readably is refused here**, and here is
    the reason it is one guard rather than two: both callers branch on that field immediately and
    in opposite directions, so an unread value becomes a wrong answer on both sides — the suspend
    tool would open a second card for an account already suspended, and the unsuspend tool would
    answer "not suspended; nothing to approve" about an account nobody read. That is the benign
    value standing in for a non-answer, which is refused everywhere else in this system. A third
    account CHANGE tool inherits the guard by calling this function at all, the argument
    `_open_account_change` makes for the ownership compare.

    **Every refusal leaves one log line**, because this is the only place a refused CHANGE call
    can leave one — see `LOG_PREFLIGHT_FAILED` for what else is silent on this path, and for why
    the request id needs no argument. `tool_name` is a parameter for that line alone: the failures
    travel back unchanged and neither caller renames them.

    **The credential comes back beside the account.** `api_username` and the host are the row's,
    captured by `fetch_whm_accounts` off the row that won resolution rather than read again here;
    `owner` is the account's, lifted out of the summary onto the payload because the ownership
    compare and the audit trail both ask for it by name and neither should have to know the shape of
    a `listaccts` row (owner-as-`server_ref`, the four recorded fields). Raw as their sources gave
    them — `None` when a source did not answer — because the compare has to be able to tell a name
    it could not read from one it read and disliked; the word for a non-answer is written where the
    evidence is built.
    """
    listed = await fetch_whm_accounts(server_ref=server_ref, context=context)
    if listed.get("ok") is not True:
        # Already a structured failure with its own code and `choices`. Re-wrapping renames it.
        return _refused_preflight(listed, tool_name=tool_name, server_ref=server_ref)

    account = match_account(listed.get("accounts"), username=username)
    if account is None:
        return _refused_preflight(
            tool_failure(
                ERROR_ACCOUNT_NOT_FOUND,
                f"No cPanel account named `{username}` exists on this WHM server.",
            ),
            tool_name=tool_name,
            server_ref=server_ref,
        )

    if account_suspension_state(account) is None:
        return _refused_preflight(
            tool_failure(
                ERROR_SUSPENSION_STATE_UNREADABLE,
                f"WHM did not report whether `{username}` is suspended, so NOA cannot say what "
                "this change would do. Check the account on the server.",
            ),
            tool_name=tool_name,
            server_ref=server_ref,
        )

    return tool_ok(
        **{
            EVIDENCE_SERVER_ID: listed.get(EVIDENCE_SERVER_ID),
            EVIDENCE_SERVER_NAME: listed.get(EVIDENCE_SERVER_NAME),
            EVIDENCE_API_USERNAME: listed.get(EVIDENCE_API_USERNAME),
            EVIDENCE_HOST: listed.get(EVIDENCE_HOST),
            EVIDENCE_OWNER: account.get(EVIDENCE_OWNER),
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
    """Ask for one cPanel account to be suspended; suspend nothing.

    Four things happen and none of them is a change: a malformed request is refused, the account
    is read, the call decides whether there is anything to do, and the question is opened.

    - a blank or whitespace-only `username` is refused before any I/O. The schema cannot
      express it — `min_length` counts whitespace — so this is the gate, and `listaccts` would
      otherwise be fetched in order to match nothing.
    - the preflight's failures pass straight through with their own codes and `choices`.
    - an account that is **already suspended** is answered `no_op` and no request is opened. That
      payload names the account and the server and carries nothing else: it is transcript,
      and the account summary holds WHM's own `suspendreason`, which as of the suspend tool is
      the operator's
      reason.
    - otherwise `open_change_request` writes the PENDING row with the preflight as evidence
      (context persisted at gate time; provenance the card shows) and `build_change_gate_response`
      turns its id into the card's address and the iframe
      (one function shapes every CHANGE result; link-out text beside the frame).

    No `reason` parameter, and nowhere to add one: the word is typed by an operator on the card,
    after this result has been rendered and forgotten. The gate refuses a
    reason-shaped argument as well (`assert_no_reason_argument`), so the boundary holds even for
    a caller that reaches this function directly rather than over MCP.
    """
    normalized_username = username.strip()
    if not normalized_username:
        return tool_failure(ERROR_USERNAME_REQUIRED, MESSAGE_USERNAME_REQUIRED)

    state = await collect_account_state(
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        server_ref=server_ref,
        username=normalized_username,
        context=context,
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

    return await _open_account_change(
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        server_ref=server_ref,
        username=normalized_username,
        state=state,
        context=context,
    )


@sanitize_tool_errors(TOOL_WHM_UNSUSPEND_ACCOUNT)
async def whm_unsuspend_account(
    *,
    server_ref: str,
    username: str,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one cPanel account's suspension to be lifted; lift nothing.

    `whm_suspend_account`'s mirror, and the preflight is the same function, so what differs is
    only what the account's state means. Two of the three states answer instead of gating, and
    both are discovered by the read rather than declared by the caller:

    - a blank or whitespace-only `username` is refused before any I/O, as above.
    - the preflight's failures pass straight through with their own codes and `choices`.
    - an account that is **not suspended** is answered `no_op`. There is nothing to lift, so
      there is nothing for an operator to authorise.
    - an account whose suspension is **locked** is refused: `unsuspendacct` will not lift a
      locked suspension, so the card would buy a decision and then a failed run. The lock is on
      the summary the preflight already read (`is_locked`, or the older `suspendlock`). Refused
      on a positive lock only — see the module docstring for why an absent field is not one, and
      why WHM's own refusal remains the authoritative answer.
    - otherwise the question is opened, exactly as for a suspension.

    Both answers that reach a transcript are built from the username and the server name rather
    than from the account summary, and here that matters more than it did at the suspend tool:
    an account
    being unsuspended is suspended right now, so its summary carries `suspendreason` — the
    operator's own words from the suspension (the reason field the LLM never sees, an id-only
    URL, no path back for a value once written).

    No `reason` parameter, and nowhere to add one. Nothing is written out to WHM
    on this path either: `unsuspendacct` has no note field, so no path back for a value once
    written opens.
    """
    normalized_username = username.strip()
    if not normalized_username:
        return tool_failure(ERROR_USERNAME_REQUIRED, MESSAGE_USERNAME_REQUIRED)

    state = await collect_account_state(
        tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        server_ref=server_ref,
        username=normalized_username,
        context=context,
    )
    if state.get("ok") is not True:
        return state

    account = state[EVIDENCE_ACCOUNT]
    server_name = state.get(EVIDENCE_SERVER_NAME)
    server_id = state.get(EVIDENCE_SERVER_ID)

    if account.get("suspended") is not True:
        logger.info(
            LOG_UNSUSPEND_NO_OP,
            tool=TOOL_WHM_UNSUSPEND_ACCOUNT,
            server_id=server_id,
            username=normalized_username,
        )
        return tool_ok(
            status=STATUS_NO_OP,
            server=server_name,
            username=normalized_username,
            suspended=False,
            message=f"`{normalized_username}` is not suspended; nothing to approve.",
        )

    if account.get("is_locked") is True:
        logger.info(
            LOG_UNSUSPEND_LOCKED,
            tool=TOOL_WHM_UNSUSPEND_ACCOUNT,
            server_id=server_id,
            username=normalized_username,
        )
        return tool_failure(
            ERROR_SUSPENSION_LOCKED,
            f"`{normalized_username}` has a locked suspension, which WHM will not lift. An "
            "administrator has to unlock it on the server first.",
        )

    return await _open_account_change(
        tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        server_ref=server_ref,
        username=normalized_username,
        state=state,
        context=context,
    )


def register_whm_account_change_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the WHM account CHANGE tools; return each name with its risk (the MCP `tools/call`
    contract, risk and status kept as separate columns).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for
    these calls — they open an approval request and execute nothing, and the run-plus-receipt
    row belongs
    to the executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for each name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_WHM_SUSPEND_ACCOUNT,
        description=DESCRIPTION_WHM_SUSPEND_ACCOUNT,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split
        # that matters is the approval gate; the classification that matters is the risk
        # returned below.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def whm_suspend_account_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
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
        # text block and the approval iframe, in that order — while every refusal
        # answers the envelope every tool shares.
        return await whm_suspend_account(server_ref=server_ref, username=username, context=context)

    @server.tool(
        name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        description=DESCRIPTION_WHM_UNSUSPEND_ACCOUNT,
        # `destructiveHint` is False and that is the whole reason the pair is not one tool with
        # an `action` enum (DECISIONS section 9): lifting a suspension restores service rather than
        # removing it, so the two names carry opposite risk and RBAC can grant them apart.
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
    )
    async def whm_unsuspend_account_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
        username: Annotated[
            str,
            Field(
                description=(
                    "The exact cPanel account username to unsuspend, as `whm_search_accounts` "
                    "reports it in `user`. Must not be blank, and must not be guessed."
                )
            ),
        ],
    ) -> ToolAnswer:
        return await whm_unsuspend_account(
            server_ref=server_ref, username=username, context=context
        )

    return {
        TOOL_WHM_SUSPEND_ACCOUNT: ToolRisk.CHANGE,
        TOOL_WHM_UNSUSPEND_ACCOUNT: ToolRisk.CHANGE,
    }


# --- Internals ---


def _refused_preflight(payload: ToolPayload, *, tool_name: str, server_ref: str) -> ToolPayload:
    """Log one refused preflight and hand the refusal back unchanged.

    Identifiers and the code only, never the account payload — the same bound the no-op events
    keep, and for the same reason: `suspendreason` is the operator's own words.

    **The id is read off the live request rather than taken from the ambient binding, and that is
    not belt-and-braces.** `RequestContextMiddleware` binds one into structlog's contextvars per
    HTTP request, but a Streamable HTTP tool call does not run in the task that served it: the
    session manager runs the session in a task created during `initialize`, and a task copies
    contextvars at creation. So the id riding along here is the id of the request that *opened the
    MCP session* — one value for every call in that session, and not the one the tool call's own
    response carried. Measured: `apps/api/tests/test_whm_account_non_answers.py` asserts this line
    against the `x-request-id` of the very response it belongs to, and that assertion fails on the
    inherited value. An id that reads like a correlation and resolves to a different request is
    worse than no id, because it is the thing an operator would grep with.
    """
    logger.warning(
        LOG_PREFLIGHT_FAILED,
        tool=tool_name,
        server_ref=server_ref,
        error_code=payload.get("error_code"),
        **({} if (request_id := _calling_request_id()) is None else {LOG_REQUEST_ID: request_id}),
    )
    return payload


def _calling_request_id() -> str | None:
    """The id of the HTTP request this tool call arrived on, or `None` off-request.

    `get_http_request` is the same production seam `remember_mcp_auth_error` uses, and it raises
    off-request rather than answering — a direct call, a non-HTTP transport, a test driving the
    function alone. `None` then, never a minted id: a fresh uuid on a line nobody can correlate is
    the silence this event exists to end, dressed as an answer.
    """
    try:
        scope = get_http_request().scope
    except RuntimeError:
        return None
    stored = scope.get("state", {}).get(SCOPE_STATE_REQUEST_ID)
    return stored if isinstance(stored, str) and stored else None


async def _open_account_change(
    *,
    tool_name: str,
    server_ref: str,
    username: str,
    state: ToolPayload,
    context: McpToolContext,
) -> ToolAnswer:
    """Write the PENDING row and shape the answer — the tail both tools share.

    The preflight `state` becomes the row's evidence verbatim, so the card describes the read
    that decided there was something to approve. `server_ref` is recorded as the model
    passed it, because the arguments are a record of what was asked for; what the change will
    actually run against is `evidence["server_id"]` (context persisted at gate time,
    `_resolve_change_target`).

    **The ownership compare is here rather than in each tool** (owner-as-`server_ref`). This is
    the one door both
    account CHANGE tools reach `open_change_request` through, so putting the guard at the door
    makes "no card is opened for a credential that cannot perform the change" a property of the
    mechanism instead of a line two tools each have to remember — and a third account CHANGE
    tool inherits it by calling this function at all.

    Ownership is **proven** here or the change is refused (owner-as-`server_ref`, folding a
    non-answer into the benign value): three of the four
    verdicts stop at this line, the two unreported ones included.

    The evidence names the credential as well as the machine (the four recorded fields). `owner` and
    `api_username` go in straight rather than defensively: a card is only built past the guard
    above, which proved both readable. The account summary keeps its own `owner` too — the flat key
    is what the card labels and what the runner compares, the summary is what WHM said.
    """
    owner = state.get(EVIDENCE_OWNER)
    api_username = state.get(EVIDENCE_API_USERNAME)
    ownership = classify_ownership(owner=owner, api_username=api_username)
    if not ownership.is_proven:
        return refuse_unproven_ownership(
            ownership=ownership,
            tool_name=tool_name,
            site=SITE_PREFLIGHT,
            username=username,
            owner=owner,
            api_username=api_username,
            server_ref=server_ref,
            server_name=state.get(EVIDENCE_SERVER_NAME),
            server_id=state.get(EVIDENCE_SERVER_ID),
        )

    opened = await open_change_request(
        tool_name=tool_name,
        arguments={"server_ref": server_ref, "username": username},
        evidence={
            EVIDENCE_SERVER_ID: state.get(EVIDENCE_SERVER_ID),
            EVIDENCE_SERVER_NAME: state.get(EVIDENCE_SERVER_NAME),
            EVIDENCE_API_USERNAME: api_username,
            EVIDENCE_HOST: recorded(state.get(EVIDENCE_HOST)),
            EVIDENCE_OWNER: owner,
            EVIDENCE_ACCOUNT: state[EVIDENCE_ACCOUNT],
        },
        context=context,
    )
    return build_change_gate_response(opened, tool_name=tool_name, context=context)


__all__ = [
    "DESCRIPTION_WHM_SUSPEND_ACCOUNT",
    "DESCRIPTION_WHM_UNSUSPEND_ACCOUNT",
    "ERROR_ACCOUNT_NOT_FOUND",
    "ERROR_ACCOUNT_OWNER_UNKNOWN",
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_SERVER_UNAVAILABLE",
    "ERROR_SUSPENSION_LOCKED",
    "ERROR_SUSPENSION_STATE_UNREADABLE",
    "ERROR_USERNAME_REQUIRED",
    "ERROR_WRONG_CREDENTIAL_FOR_OWNER",
    "EVIDENCE_ACCOUNT",
    "EVIDENCE_API_USERNAME",
    "EVIDENCE_HOST",
    "EVIDENCE_OWNER",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_UNRECORDED",
    "LOG_OWNERSHIP_REFUSED",
    "LOG_PREFLIGHT_FAILED",
    "LOG_SUSPEND_NO_OP",
    "LOG_SUSPEND_UNVERIFIED",
    "LOG_UNSUSPEND_LOCKED",
    "LOG_UNSUSPEND_NO_OP",
    "LOG_UNSUSPEND_UNVERIFIED",
    "MESSAGE_POSTFLIGHT_SUSPEND_FAILED",
    "MESSAGE_POSTFLIGHT_UNSUSPEND_FAILED",
    "MESSAGE_SERVER_UNAVAILABLE",
    "MESSAGE_SUSPEND_FAILED",
    "MESSAGE_UNSUSPEND_FAILED",
    "MESSAGE_USERNAME_REQUIRED",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "TOOL_WHM_SUSPEND_ACCOUNT",
    "TOOL_WHM_UNSUSPEND_ACCOUNT",
    "VERIFICATION_UNAVAILABLE",
    "classify_ownership",
    "collect_account_state",
    "match_account",
    "register_whm_account_change_tools",
    "whm_suspend_account",
    "whm_unsuspend_account",
]
