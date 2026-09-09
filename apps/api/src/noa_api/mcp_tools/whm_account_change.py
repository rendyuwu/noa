"""WHM account CHANGE tools: `whm_suspend_account` (T22), `whm_unsuspend_account` (T23).

**The first CHANGE tool NOA exposed**, and therefore the first call that runs the whole gate
over the real mount: `tools/call` → in-process preflight → `action_requests(PENDING)` → the
approval card → an operator's cookie POST (T37) → the executor (T38) → the runner at the bottom
of this module. Everything above the runner was already built and, until T22, vacuous.

**Two halves, on opposite sides of V22's boundary, in one module.** A tool is what the LLM can
reach and it changes nothing; a runner performs the change and is reachable only from
`core.approvals.execution`, which is reachable only from an approval. They live together because
they are two moments of one workflow and because a runner acts on the evidence its tool
gathered — splitting them would put the before-state and the change that answers it in two files
that can drift. What keeps the split honest is that neither calls the other: neither tool holds a
reference to a runner, and a runner is dispatched by tool name from a registry the MCP path never
reads (`noa_api.mcp_tools.change_runners`).

**Two tools, one shape, and the shape is shared rather than mirrored.** Suspend and unsuspend are
not merged — opposite risk directions, clearer as two names (DECISIONS §9) — but everything
between the two names is one implementation (V66): `collect_account_state` is the preflight for
both, `_resolve_change_target` turns an approved request into the client that performs it, and
`_verify_account_state` is the postflight, which differs only in the value `suspended` must hold
when the change took (`_AccountChangeDirection`). What is deliberately written twice is the
surface a model reads — the two tool functions, their descriptions and their registrations —
because those genuinely differ and a shared spelling of them would be one sentence trying to
describe two opposite acts.

**The preflight runs inside the call** (C9, V17), and it is `fetch_whm_accounts` — T20/T21's
internal, not a second copy of "resolve a server and list its accounts" (V66). The evidence it
produces is born in-process, lives milliseconds, belongs to the same user, and reaches the
operator's card through `approval_context` rather than through a transcript. That is the whole of
DECISIONS §3.2: no evidence store, no freshness window, no `require_preflight` protocol.

**An account already in the state the change would produce is answered, not gated.** The
preflight is what discovers it — already suspended for T22, not suspended at all for T23 — and
asking an operator to authorise a change that would do nothing is worse than saying so. No
`action_requests` row is written on that path, so the only trace is a structured log line —
which is the right amount of trace for a call that changed nothing (a CHANGE tool's `tools/call`
writes no `tool_runs` row either, T73).

**A locked suspension is refused before a card exists** (T23). WHM's `unsuspendacct` refuses an
account whose suspension is locked, so opening a request for one costs an operator a decision and
buys a run that fails — the no-op argument above, one state over. The lock is already on the
normalised summary (`core.integrations.whm.accounts`, which reads `is_locked` and falls back to
the older `suspendlock`), so the preflight that reads the account reads the lock with it.

The guard fires on a **positive** lock only, and that bound is deliberate rather than an
oversight: `listaccts` omits the field entirely on cPanel versions that do not have it, and
refusing every unsuspend on those servers would cost more than the failure it prevents. WHM's own
refusal at execute time stays the authoritative one — it arrives as `whm_api_error` carrying
WHM's `reason`, which names the remedy — and this guard is the cheap early half of it.

**A credential that does not own the account is refused before a card exists**, the same argument
one step harder, and `whm_account_owner_gate` holds all of it: cPanel gates an account write on
*ownership* rather than on the token's ACL (§R.33), so the preflight compares the account's
`owner` against the resolved row's `api_username`. Two things this module decides rather than
that one. The guard sits in `_open_account_change`, the single door both tools reach
`open_change_request` through, so a third account CHANGE tool cannot be written without it; and
it runs **again** in the runner, off the stored evidence, because a row is editable between a
request and its decision (V33) and repointing `api_username` after the card was rendered would
otherwise substitute the acting identity silently.

**The card and the receipt name the credential, not just the machine** (§V108): the row's
`name`, its `api_username`, the host out of its `base_url`, and the account's `owner`. A
privileged write whose credential is not recorded is not auditable, and what an audit needs is
which identity acted — a username, never the token (V8).

**A runner acts on the server the card described, not on the operator's word.** `server_ref` is
whatever the model passed, and inventory can be edited between a request and its approval;
`evidence["server_id"]` is the machine the preflight actually read and the operator actually saw
(V33). Re-resolving the string there would be a second resolution that can disagree with the one
the decision rests on.

**The suspension note is the operator's reason, and only suspend has one.** WHM's `suspendacct`
takes a note, and C8's single field is the only text NOA has that belongs there — the LLM never
authored it, never relayed it and never saw it, and it is read from `action_requests.reason`
after the decision committed (`core.approvals.execution`). What that costs is two return paths,
and V96 closes both: WHM echoes the note back as `suspendreason`, so `whm_search_accounts`
withholds the field from the rows it hands a model (`ACCOUNT_FIELDS_WITHHELD_FROM_MODEL`); and
`tool_runs.result_summary` is derived from a runner's payload and read back by
`noa_get_action_result`, so no payload here carries the note — not a no-op answer, not a runner's.
`whm_list_accounts`' parked table keeps the column, because that page is behind the operator's
own cookie.

`unsuspendacct` takes no note, so **T23 writes nothing out and V96 does not bite on that side.**
It does meet a case T22 could not: an account being unsuspended *is* suspended when the preflight
reads it, so its summary carries `suspendreason` — an operator's earlier words. That summary goes
onto the row as evidence, where V27's requester-match and the card are its only readers (a model
cannot reach it: `ActionResultView` has no field for evidence, V76). What is not closed by
construction is this tool's own answers, which do land in a transcript (V26) — so the no-op and
the locked refusal are built from the username and the server name, never from the summary.

**Postflight, and its third answer.** A change WHM accepted is re-read to confirm it took, and it
is re-read **through the credential that wrote it**: the identity that performed the change is the
one that confirms it. A reseller token's `listaccts` sees its own accounts (77 of 77 on the
measured host, §R.33) and its own account is the only one in question, so moving the confirming
read to a root credential "so it can see everything" would answer "did it take" from an identity
that did not perform the write. One `_ChangeTarget`, one client, all three phases. Two
outcomes are obvious — the account reached the state that was asked for, or it did not and the
change is therefore a failure — and the third is the one worth naming: the mutation succeeded and
the confirming read did not answer. That is recorded as a change that happened and was *not
verified*, never as a failure and never as a silent pass. V62's rule one system over:
verification-unavailable is not verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final
from uuid import UUID

import structlog
from fastmcp import FastMCP
from pydantic import Field

from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.db.lifecycle import ToolRisk
from core.integrations.whm.accounts import WHMAccount, normalize_whm_account_list
from core.integrations.whm.client import WHMClient
from noa_api.mcp_tools.change_gate import build_change_gate_response, open_change_request
from noa_api.mcp_tools.change_target import (
    # Hoisted to `change_target` at T25, when the firewall runner became the second caller of
    # the same refusals and the same three status words (V66). Re-exported below, so every name
    # this module already published keeps working from here.
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)
from noa_api.mcp_tools.whm_account_owner_gate import (
    # Hoisted at T78, when the runner became the second caller of the same refusal — the shape
    # `change_target` was hoisted in. Re-exported below, so every name this module publishes
    # keeps working from here.
    ERROR_ACCOUNT_OWNER_UNKNOWN,
    ERROR_WRONG_CREDENTIAL_FOR_OWNER,
    EVIDENCE_API_USERNAME,
    EVIDENCE_HOST,
    EVIDENCE_OWNER,
    EVIDENCE_UNRECORDED,
    LOG_OWNERSHIP_REFUSED,
    SITE_PREFLIGHT,
    SITE_RUNNER,
    classify_ownership,
    recorded,
    refuse_unproven_ownership,
)
from noa_api.mcp_tools.whm_read import MESSAGE_LIST_ACCOUNTS_FAILED, fetch_whm_accounts

TOOL_WHM_SUSPEND_ACCOUNT = "whm_suspend_account"
TOOL_WHM_UNSUSPEND_ACCOUNT = "whm_unsuspend_account"

# The evidence keys the tools write and the runners read back. Constants because they cross a
# boundary in time as well as in code — a tool writes them into `approval_context` JSONB and
# the runner reads them minutes later — and a misspelt key in JSONB reads as an absent one (V66,
# the argument `core.approvals.context` makes one level up).
EVIDENCE_SERVER_ID = "server_id"
EVIDENCE_SERVER_NAME = "server"
EVIDENCE_ACCOUNT = "account"

ERROR_USERNAME_REQUIRED = "username_required"
ERROR_ACCOUNT_NOT_FOUND = "account_not_found"
# WHM accepted the mutation and the confirming read says it did not take.
ERROR_POSTFLIGHT_FAILED = "postflight_failed"
# The account's suspension is locked, and `unsuspendacct` refuses a locked account (T23). A
# refusal rather than an approval request: the card would buy a decision and a failed run.
ERROR_SUSPENSION_LOCKED = "account_suspension_locked"

MESSAGE_USERNAME_REQUIRED = "A cPanel account username is required."
MESSAGE_SUSPEND_FAILED = "WHM did not suspend the account."
MESSAGE_UNSUSPEND_FAILED = "WHM did not unsuspend the account."
MESSAGE_POSTFLIGHT_SUSPEND_FAILED = "WHM accepted the suspension but the account is not suspended."
MESSAGE_POSTFLIGHT_UNSUSPEND_FAILED = (
    "WHM accepted the unsuspension but the account is still suspended."
)

# One structured event per call that found nothing to do, so "why is there no approval card" is
# answerable from the logs. Identifiers only, never the account payload (V8).
LOG_SUSPEND_NO_OP = "whm_suspend_account_no_op"
LOG_UNSUSPEND_NO_OP = "whm_unsuspend_account_no_op"

# The same question with a different answer: there was something to do and WHM would refuse it.
LOG_UNSUSPEND_LOCKED = "whm_unsuspend_account_suspension_locked"

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
# two schemas cannot drift into describing one argument two ways (V66).
#
# It does **not** mean what it means on the read tools, and this is where a model learns that
# (§V106). Both branches are stated because neither covers the other: reseller rows are named
# after their credential (V109(b)) and hidden from `whm_list_servers` (V109(a)), while the root
# rows cannot all be called `root` — so for the 56 of 451 measured `owner=root` accounts (§R.33)
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


@dataclass(frozen=True)
class _AccountChangeDirection:
    """What "the change took" means for one direction of the suspend/unsuspend pair.

    The two tools are mirror images, and this is what makes that a fact of construction rather
    than a claim in a docstring (V66): one postflight reads both, and the only thing it needs
    from its caller is which value `suspended` must hold afterwards. Flipping `target_suspended`
    is what a mutation test flips, and it turns "the change took" into its opposite in one place.

    The sentences travel with it because an operator reads them: "WHM accepted the suspension"
    and "WHM accepted the unsuspension" are the same claim about two different acts, and a shared
    wording would have to name neither.
    """

    tool_name: str
    # The value `suspended` must hold on the re-read once the change took.
    target_suspended: bool
    # What WHM was asked to do, as it appears mid-sentence: "WHM accepted the {noun} of `x`".
    noun: str
    # The confirmed state, as an operator reads it: "`x` {confirmed_state}."
    confirmed_state: str
    # The `postflight_failed` sentence — WHM accepted a call that did not take.
    postflight_message: str
    unverified_log_event: str


_SUSPEND: Final = _AccountChangeDirection(
    tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
    target_suspended=True,
    noun="suspension",
    confirmed_state="is suspended",
    postflight_message=MESSAGE_POSTFLIGHT_SUSPEND_FAILED,
    unverified_log_event=LOG_SUSPEND_UNVERIFIED,
)

_UNSUSPEND: Final = _AccountChangeDirection(
    tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
    target_suspended=False,
    noun="unsuspension",
    confirmed_state="is no longer suspended",
    postflight_message=MESSAGE_POSTFLIGHT_UNSUSPEND_FAILED,
    unverified_log_event=LOG_UNSUSPEND_UNVERIFIED,
)


async def collect_account_state(
    *,
    server_ref: str,
    username: str,
    context: McpToolContext,
) -> ToolPayload:
    """One account's current state on one WHM server. Internal — ⊥ an MCP tool (C9, V17).

    The before-state an operator authorises against, and the same function both account CHANGE
    tools call (T22, T23). Not decorated with `sanitize_tool_errors`: its callers are exposed
    tools that already are, and a second boundary would turn a `NoaError` into a payload the
    caller then has to unwrap twice (`fetch_whm_accounts`' rule, one module over).

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

    **The credential comes back beside the account.** `api_username` and the host are the row's,
    captured by `fetch_whm_accounts` off the row that won resolution rather than read again
    here; `owner` is the account's, lifted out of the summary onto the payload because the
    ownership compare and the audit trail both ask for it by name and neither should have to
    know the shape of a `listaccts` row (§V106, §V108). Raw as their sources gave them —
    `None` when a source did not answer — because the compare has to be able to tell a name it
    could not read from one it read and disliked; the word for a non-answer is written where
    the evidence is built.
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
    """Ask for one cPanel account's suspension to be lifted; lift nothing (T23 — V16, V17, V23).

    `whm_suspend_account`'s mirror, and the preflight is the same function, so what differs is
    only what the account's state means. Two of the three states answer instead of gating, and
    both are discovered by the read rather than declared by the caller:

    - a blank or whitespace-only `username` is refused before any I/O (V21), as above.
    - the preflight's failures pass straight through with their own codes and `choices` (V18).
    - an account that is **not suspended** is answered `no_op`. There is nothing to lift, so
      there is nothing for an operator to authorise.
    - an account whose suspension is **locked** is refused: `unsuspendacct` will not lift a
      locked suspension, so the card would buy a decision and then a failed run. The lock is on
      the summary the preflight already read (`is_locked`, or the older `suspendlock`). Refused
      on a positive lock only — see the module docstring for why an absent field is not one, and
      why WHM's own refusal remains the authoritative answer.
    - otherwise the question is opened, exactly as for a suspension.

    Both answers that reach a transcript are built from the username and the server name rather
    than from the account summary, and here that matters more than it did at T22: an account
    being unsuspended is suspended right now, so its summary carries `suspendreason` — the
    operator's own words from the suspension (C8, V26, V96a).

    No `reason` parameter, and nowhere to add one (C8, V15, V43). Nothing is written out to WHM
    on this path either: `unsuspendacct` has no note field, so V96's return paths do not open.
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

        The server and the account both come from the **evidence**, never from the arguments —
        `_resolve_change_target` is where that rule lives, shared with T23's runner (V33, V66).
        """
        target = await _resolve_change_target(request, context=context)
        if not isinstance(target, _ChangeTarget):
            return target

        # C8's single field, written where WHM keeps a suspension note. The operator typed it,
        # the LLM never saw it, and it is not echoed back in the payload below — `result_summary`
        # is derived from that payload and `noa_get_action_result` returns it to a model (V96b).
        mutation = await target.client.suspend_account(
            username=target.username, reason=request.reason
        )
        if mutation.get("ok") is not True:
            return _passthrough_failure(mutation, fallback=MESSAGE_SUSPEND_FAILED)

        return await _verify_account_state(
            target, direction=_SUSPEND, action_request_id=request.action_request_id
        )

    return run


def build_whm_unsuspend_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that lifts a suspension, reachable only after an operator approved (T23, T38).

    The suspend runner one direction over, and deliberately narrower in one respect:
    `unsuspendacct` takes only a username. `request.reason` is on the request — the executor
    reads it off the row for every approved change (V43) — and this runner does not touch it,
    because there is no field on the target system it belongs in. Nothing to write out means
    none of V96's return paths open here.
    """

    async def run(request: ChangeExecutionRequest) -> ToolPayload:
        """Lift the suspension this approved request names, and say what happened."""
        target = await _resolve_change_target(request, context=context)
        if not isinstance(target, _ChangeTarget):
            return target

        mutation = await target.client.unsuspend_account(username=target.username)
        if mutation.get("ok") is not True:
            # A locked suspension the tool's preflight did not see — the lock was set after the
            # request was opened, or WHM did not report it — arrives here as `whm_api_error`
            # carrying WHM's own `reason`, which is the sentence that names the remedy.
            return _passthrough_failure(mutation, fallback=MESSAGE_UNSUSPEND_FAILED)

        return await _verify_account_state(
            target, direction=_UNSUSPEND, action_request_id=request.action_request_id
        )

    return run


def build_whm_account_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tools (T22, T23)."""
    return {
        TOOL_WHM_SUSPEND_ACCOUNT: build_whm_suspend_runner(context=context),
        TOOL_WHM_UNSUSPEND_ACCOUNT: build_whm_unsuspend_runner(context=context),
    }


def register_whm_account_change_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the WHM account CHANGE tools; return each name with its risk (I.mcp, V20).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for
    these calls (T73) — they open an approval request and execute nothing, and V46's row belongs
    to the executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for each name at startup, rather than
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
        # text block and the approval iframe, in that order (V24, V25) — while every refusal
        # answers the envelope every tool shares.
        return await whm_suspend_account(server_ref=server_ref, username=username, context=context)

    @server.tool(
        name=TOOL_WHM_UNSUSPEND_ACCOUNT,
        description=DESCRIPTION_WHM_UNSUSPEND_ACCOUNT,
        # `destructiveHint` is False and that is the whole reason the pair is not one tool with
        # an `action` enum (DECISIONS §9): lifting a suspension restores service rather than
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


@dataclass(frozen=True)
class _ChangeTarget:
    """The machine and account an approved change runs against, resolved from the evidence."""

    client: WHMClient
    username: str
    server_name: str


async def _open_account_change(
    *,
    tool_name: str,
    server_ref: str,
    username: str,
    state: ToolPayload,
    context: McpToolContext,
) -> ToolAnswer:
    """Write the PENDING row and shape the answer — the tail both tools share (T33, T32).

    The preflight `state` becomes the row's evidence verbatim, so the card describes the read
    that decided there was something to approve (V33, V35). `server_ref` is recorded as the model
    passed it, because the arguments are a record of what was asked for; what the change will
    actually run against is `evidence["server_id"]` (V33, `_resolve_change_target`).

    **The ownership compare is here rather than in each tool** (§V106). This is the one door both
    account CHANGE tools reach `open_change_request` through, so putting the guard at the door
    makes "no card is opened for a credential that cannot perform the change" a property of the
    mechanism instead of a line two tools each have to remember — and a third account CHANGE
    tool inherits it by calling this function at all.

    Ownership is **proven** here or the change is refused (§V106, V86): three of the four
    verdicts stop at this line, the two unreported ones included.

    The evidence names the credential as well as the machine (§V108). `owner` and `api_username`
    go in straight rather than defensively: a card is only built past the guard above, which
    proved both readable. The account summary keeps its own `owner` too — the flat key is what
    the card labels and what the runner compares, the summary is what WHM said.
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


async def _resolve_change_target(
    request: ChangeExecutionRequest, *, context: McpToolContext
) -> _ChangeTarget | ToolPayload:
    """The client and username an approved account change runs against, or the refusal (V33).

    **From the evidence, never from the arguments.** `server_ref` is a string the model supplied
    and inventory can be edited between a request and its approval; `evidence["server_id"]` is
    the machine the preflight actually read and the operator actually saw on the card.
    Re-resolving the string here would be a second resolution that can disagree with the one the
    decision rests on.

    Two ways it refuses on the identity of the machine, both `whm_server_unavailable` and both
    before any mutation: the evidence no longer carries a usable id or username (it round-tripped
    through JSONB, and a value that no longer parses is a request NOA refuses rather than guesses
    at), or the server row is gone.

    **A third refuses on the identity of the credential** (§V106). The row's `api_username` is
    read here, now, and compared against the `owner` the card was built from: a row is editable
    between a request and its decision (V33), so the credential this change would run as need
    not be the one the operator authorised — repointing `api_username` at another reseller after
    the card was rendered would otherwise be a silent substitution of the acting identity. The
    live value is the one that can be wrong, so the live value is the one that is checked.

    It refuses on an **unproven** verdict too, including a row whose evidence carries no `owner`
    — one opened before this key existed. An approval authorises a change to *this* account by
    *that* credential, and evidence that cannot say whether the pair holds does not carry the
    authorisation forward (V86).

    The database session closes before the caller's WHM round trips, T21's rule — and here it
    matters twice over, because the executor's own session is open for the whole of the call.
    """
    server_id = uuid_or_none(request.evidence.get(EVIDENCE_SERVER_ID))
    account = request.evidence.get(EVIDENCE_ACCOUNT)
    username = account.get("user") if isinstance(account, dict) else None
    if server_id is None or not isinstance(username, str) or not username:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    owner = request.evidence.get(EVIDENCE_OWNER)
    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        server = await repository.get_by_id(server_id)
        if server is None:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
        api_username = server.api_username
        client = context.whm_client_factory(server, cipher=context.secret_cipher)
        server_name = server.name

    ownership = classify_ownership(owner=owner, api_username=api_username)
    if not ownership.is_proven:
        return refuse_unproven_ownership(
            ownership=ownership,
            tool_name=request.tool_name,
            site=SITE_RUNNER,
            username=username,
            owner=owner,
            api_username=api_username,
            # What the model asked for, as the gate recorded it (T33). The resolved row's name
            # is a reseller's `api_username` by V109(b) and stays out of the answer; it goes to
            # the log instead.
            server_ref=request.arguments.get("server_ref"),
            server_name=server_name,
            server_id=str(server_id),
            action_request_id=str(request.action_request_id),
        )

    return _ChangeTarget(client=client, username=username, server_name=server_name)


async def _verify_account_state(
    target: _ChangeTarget,
    *,
    direction: _AccountChangeDirection,
    action_request_id: UUID,
) -> ToolPayload:
    """Re-read the account and say whether the change took (V62's rule, one system over).

    Three answers, and the middle one is why this is a function rather than a boolean:

    - the account reads the way the change asked for → done, and verified;
    - it reads the other way → WHM accepted a call that did not take, which is a failure;
    - the read itself did not answer → the change happened and is **unverified**. Reporting that
      as a failure would send an operator to repeat a change that may already have taken;
      reporting it as a plain success would claim a confirmation nobody has.

    One function for both directions (V66): `direction.target_suspended` is the only thing that
    differs, and a second copy of these three branches is a second place the third one can be
    dropped.
    """
    result = await target.client.list_accounts()
    verified = (
        match_account(normalize_whm_account_list(result.get("accounts")), username=target.username)
        if result.get("ok") is True
        else None
    )

    if verified is None:
        logger.warning(
            direction.unverified_log_event,
            tool=direction.tool_name,
            action_request_id=str(action_request_id),
            username=target.username,
            cause=str(result.get("error_code") or MESSAGE_LIST_ACCOUNTS_FAILED),
        )
        return tool_ok(
            status=STATUS_CHANGED,
            server=target.server_name,
            username=target.username,
            verified=False,
            verification=VERIFICATION_UNAVAILABLE,
            message=(
                f"WHM accepted the {direction.noun} of `{target.username}`, but the confirming "
                "read did not answer. Check the account on the server."
            ),
        )

    if (verified.get("suspended") is True) is not direction.target_suspended:
        return tool_failure(ERROR_POSTFLIGHT_FAILED, direction.postflight_message)

    return tool_ok(
        status=STATUS_CHANGED,
        server=target.server_name,
        username=target.username,
        suspended=direction.target_suspended,
        verified=True,
        message=f"`{target.username}` {direction.confirmed_state}.",
    )


def _passthrough_failure(result: dict[str, object], *, fallback: str) -> ToolPayload:
    """A `WHMClient` failure as a tool failure, keeping the code that names the remedy."""
    message = result.get("message")
    spoken = message if isinstance(message, str) and message.strip() else None
    return tool_failure(str(result.get("error_code") or ERROR_UNKNOWN), spoken or fallback)


__all__ = [
    "DESCRIPTION_WHM_SUSPEND_ACCOUNT",
    "DESCRIPTION_WHM_UNSUSPEND_ACCOUNT",
    "ERROR_ACCOUNT_NOT_FOUND",
    "ERROR_ACCOUNT_OWNER_UNKNOWN",
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_SERVER_UNAVAILABLE",
    "ERROR_SUSPENSION_LOCKED",
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
    "build_whm_account_change_runners",
    "build_whm_suspend_runner",
    "build_whm_unsuspend_runner",
    "classify_ownership",
    "collect_account_state",
    "match_account",
    "register_whm_account_change_tools",
    "whm_suspend_account",
    "whm_unsuspend_account",
]
