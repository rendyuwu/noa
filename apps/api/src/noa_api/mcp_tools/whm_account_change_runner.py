"""The halves of `whm_suspend_account` and `whm_unsuspend_account` that change WHM.

Beside `whm_account_change.py` rather than inside it, and the reason is the same one that split the
Proxmox and PMG tools from their runners: a CHANGE tool is two halves on opposite sides of the
cookie/CSRF boundary, and together they run past the file-size cap's 900-line budget. That module
reached the budget exactly, so the next line either lands here or does not land at all.

The split falls where the design already draws a line — **nothing in the tool module can change
anything, and nothing here is reachable without an approval.** The tool names, the evidence keys,
the error codes, the sentences and `match_account` all come from that module; nothing there
imports this one, so `registry.py` reaches the tools and `change_runners.py` reaches the runners
with no cycle between them. What moved is code and not vocabulary: every constant stayed where
it was, so a reader following a name from a receipt still lands on the module that defines it.

**Two runners, one implementation, and the difference is one value.** Suspend and unsuspend are
separate names on purpose — opposite risk directions, and RBAC can grant them apart — but
everything between the two names is shared: `_resolve_change_target` turns an approved
request into the client that performs it and re-proves the credential's ownership,
`_verify_account_state` is the postflight, and `_AccountChangeDirection` carries the only thing
that differs, which is the value `suspended` must hold once the change took.

**The operator's reason reaches WHM through the suspend runner and nowhere else.** `suspendacct` has
a suspension-note field and the operator-typed reason field is the only honest text for it;
`unsuspendacct` takes no note, so that runner never reads the value — which is what "a value, not a
permission" looks like from the other side. Neither payload echoes it back, because `result_summary`
is derived from the payload and `noa_get_action_result` hands the summary to a model, and neither
delta carries it either: `ChangeDelta` refuses a reason-bearing key at construction, and WHM's own
echo of the note (`suspendreason`, on every later `listaccts` row) is among the names it refuses.
That last part is not hypothetical here — the account summary this runner reads back *is* the shape
that carries it, which is why the identity a delta is built from is two named strings rather than
the summary.

**The delta states a field change, and the `old` side comes off the evidence.** What moved is one
boolean, `suspended`, and the value it moved from is the reading the operator authorised against
rather than a second reading taken later. Where the evidence cannot say, no field change is
stated at all: an `old` side nobody recorded is not an `old` side of `false`, and a delta whose
before-value was invented is the fabrication refused one surface over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import structlog

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
    FieldChange,
)
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.integrations.whm.accounts import account_suspension_state, normalize_whm_account_list
from core.integrations.whm.client import WHMClient
from noa_api.mcp_tools.change_target import (
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import ERROR_UNKNOWN, ToolPayload, tool_failure, tool_ok
from noa_api.mcp_tools.whm_account_change import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SUSPENSION_STATE_UNREADABLE,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    LOG_SUSPEND_UNVERIFIED,
    LOG_UNSUSPEND_UNVERIFIED,
    MESSAGE_POSTFLIGHT_SUSPEND_FAILED,
    MESSAGE_POSTFLIGHT_UNSUSPEND_FAILED,
    MESSAGE_SUSPEND_FAILED,
    MESSAGE_UNSUSPEND_FAILED,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    match_account,
)
from noa_api.mcp_tools.whm_account_owner_gate import (
    SITE_RUNNER,
    classify_ownership,
    refuse_unproven_ownership,
)
from noa_api.mcp_tools.whm_read import MESSAGE_LIST_ACCOUNTS_FAILED

# The one field an account change moves, and the name it is stated under in every delta this
# module publishes. A constant because a receipt outlives the call and a renamed field reads to a
# reader as a different fact.
DELTA_FIELD_SUSPENDED: Final = "suspended"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _AccountChangeDirection:
    """What "the change took" means for one direction of the suspend/unsuspend pair.

    The two tools are mirror images, and this is what makes that a fact of construction rather
    than a claim in a docstring: one postflight reads both, and the only thing it needs
    from its caller is which value `suspended` must hold afterwards. Flipping `target_suspended`
    is what a mutation test flips, and it turns "the change took" into its opposite in one place
    — in the payload and in the delta at once, since the delta's `new` side is read from here.

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


@dataclass(frozen=True)
class _ChangeTarget:
    """The machine and account an approved change runs against, resolved from the evidence.

    `suspended_before` is the gate-time reading of the one field this change moves, carried here
    so the postflight can state a delta without re-reading the evidence it was resolved from.
    `None` means the evidence did not say — a row opened before the key existed, one whose account
    summary did not survive its JSONB round trip as an object, or one whose `suspended` is not a
    boolean — and a caller states no field change at all in that case rather than substituting a
    value.
    """

    client: WHMClient
    username: str
    server_name: str
    suspended_before: bool | None


def build_whm_suspend_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that suspends, reachable only after an operator approved.

    A closure over the tool context rather than a class: what it needs is the same session
    factory, cipher and client factory the tool used, and holding them by reference is what makes
    the change go through the production decrypt site rather than a second one.
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Suspend the account this approved request names, and say what happened.

        Answers the ordinary tool envelope (`noa_api.mcp_tools.results`), because the executor
        classifies the run and bounds the summary off it. It does not raise, for the reason a
        tool does not: the executor catches, but what it can record then is coarser than
        what this knew.

        The server and the account both come from the **evidence**, never from the arguments —
        `_resolve_change_target` is where that rule lives, shared with the unsuspend tool's runner.

        The resolution refusal carries **no delta**: nothing was asked of WHM, so nothing was
        measured and nothing is stated. WHM's own refusal of the mutation does carry one,
        because by then the identity is resolved and the credential is proven.
        """
        target = await _resolve_change_target(request, context=context)
        if not isinstance(target, _ChangeTarget):
            return ChangeOutcome(payload=target)

        # The reason field, written where WHM keeps a suspension note. The operator typed it,
        # the LLM never saw it, and it is not echoed back in the payload below — `result_summary`
        # is derived from that payload and `noa_get_action_result` returns it to a model.
        mutation = await target.client.suspend_account(
            username=target.username, reason=request.reason
        )
        if mutation.get("ok") is not True:
            return _passthrough_failure(target, mutation, fallback=MESSAGE_SUSPEND_FAILED)

        return await _verify_account_state(target, direction=_SUSPEND, request=request)

    return run


def build_whm_unsuspend_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that lifts a suspension, reachable only after an operator approved.

    The suspend runner one direction over, and deliberately narrower in one respect:
    `unsuspendacct` takes only a username. `request.reason` is on the request — the executor
    reads it off the row for every approved change — and this runner does not touch it,
    because there is no field on the target system it belongs in. Nothing to write out means
    no path back to a model opens here.
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Lift the suspension this approved request names, and say what happened."""
        target = await _resolve_change_target(request, context=context)
        if not isinstance(target, _ChangeTarget):
            return ChangeOutcome(payload=target)

        mutation = await target.client.unsuspend_account(username=target.username)
        if mutation.get("ok") is not True:
            # A locked suspension the tool's preflight did not see — the lock was set after the
            # request was opened, or WHM did not report it — arrives here as `whm_api_error`
            # carrying WHM's own `reason`, which is the sentence that names the remedy.
            return _passthrough_failure(target, mutation, fallback=MESSAGE_UNSUSPEND_FAILED)

        return await _verify_account_state(target, direction=_UNSUSPEND, request=request)

    return run


def build_whm_account_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → the thing that performs that change once approved."""
    return {
        TOOL_WHM_SUSPEND_ACCOUNT: build_whm_suspend_runner(context=context),
        TOOL_WHM_UNSUSPEND_ACCOUNT: build_whm_unsuspend_runner(context=context),
    }


# --- Internals ---


async def _resolve_change_target(
    request: ChangeExecutionRequest, *, context: McpToolContext
) -> _ChangeTarget | ToolPayload:
    """The client and username an approved account change runs against, or the refusal.

    **From the evidence, never from the arguments.** `server_ref` is a string the model supplied
    and inventory can be edited between a request and its approval; `evidence["server_id"]` is
    the machine the preflight actually read and the operator actually saw on the card.
    Re-resolving the string here would be a second resolution that can disagree with the one the
    decision rests on.

    Two ways it refuses on the identity of the machine, both `whm_server_unavailable` and both
    before any mutation: the evidence no longer carries a usable id or username (it round-tripped
    through JSONB, and a value that no longer parses is a request NOA refuses rather than guesses
    at), or the server row is gone.

    **A third refuses on the identity of the credential**. The row's `api_username` is
    read here, now, and compared against the `owner` the card was built from: a row is editable
    between a request and its decision, so the credential this change would run as need
    not be the one the operator authorised — repointing `api_username` at another reseller after
    the card was rendered would otherwise be a silent substitution of the acting identity. The
    live value is the one that can be wrong, so the live value is the one that is checked.

    It refuses on an **unproven** verdict too, including a row whose evidence carries no `owner`
    — one opened before this key existed. An approval authorises a change to *this* account by
    *that* credential, and evidence that cannot say whether the pair holds does not carry the
    authorisation forward.

    The gate-time `suspended` reading is lifted off the same summary the username came from, and
    it goes through the one reader that answers `None` for anything WHM did not spell readably
    (`account_suspension_state`). This becomes the `old` side of a delta an operator reads, so a
    summary written by something other than WHM's own `listaccts` must not be able to make a
    non-boolean read as a state — and a field nobody could read is not an `old` side of `false`
    either, which is the `None` `_suspension_change` then states no field change for.

    The database session closes before the caller's WHM round trips, the account search's rule — and
    here it matters twice over, because the executor's own session is open for the whole of the
    call.
    """
    server_id = uuid_or_none(request.evidence.get(EVIDENCE_SERVER_ID))
    account = request.evidence.get(EVIDENCE_ACCOUNT)
    username = account.get("user") if isinstance(account, dict) else None
    if server_id is None or not isinstance(username, str) or not username:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    suspended_before = account_suspension_state(account) if isinstance(account, dict) else None
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
            # What the model asked for, as the gate recorded it. The resolved row's name
            # is a reseller's `api_username` by the name-equals-api_username rule and stays out
            # of the answer; it goes to
            # the log instead.
            server_ref=request.arguments.get("server_ref"),
            server_name=server_name,
            server_id=str(server_id),
            action_request_id=str(request.action_request_id),
        )

    return _ChangeTarget(
        client=client,
        username=username,
        server_name=server_name,
        suspended_before=suspended_before,
    )


def _account_delta(
    target: _ChangeTarget,
    *,
    verification: str,
    verification_cause: str | None = None,
    changed_fields: tuple[FieldChange, ...] | None,
) -> ChangeDelta:
    """This change's before→after, as the runner that ran it states it.

    The identity is two named strings — the server and the account — and deliberately not the
    account summary the postflight just read. That summary carries WHM's `suspendreason`, which
    as of the suspend tool is the operator's own words coming back off the target system, and a
    delta is a
    receipt key a model can reach through the audit trail. `ChangeDelta` would
    refuse it, and the point of building the identity by hand is that the refusal never has to
    fire.

    `changed_fields` is decided per branch, because the two spellings of "nothing to report" are
    not interchangeable here either:

    - `()` — the account was re-read and the field did not move. WHM accepted a call that did not
      take, which is a measurement and a failure.
    - `None` — nothing was compared. WHM refused the mutation, so a timeout could be hiding a
      change that landed; or the confirming read did not answer at all, or answered without a
      suspension state NOA can read; or the postflight answered and the *evidence* carried no
      before-value to answer against, which is `_suspension_change`'s own `None` and reaches here
      on the confirmed branch.
    """
    return ChangeDelta(
        identity={"server": target.server_name, "username": target.username},
        verification=verification,
        verification_cause=verification_cause,
        changed_fields=changed_fields,
    )


def _suspension_change(
    target: _ChangeTarget, *, direction: _AccountChangeDirection
) -> tuple[FieldChange, ...] | None:
    """The one row a confirmed account change renders, an empty diff, or no diff at all.

    Three answers, because "the evidence never said" and "the two sides match" are two claims and
    only one of them is a measurement:

    - `None` — the evidence carried no usable `old` side, so no comparison happened. An empty
      tuple here would tell an operator NOA compared and found the account where it left it,
      about a change the postflight has just confirmed moved it. An `old` side nobody recorded is
      not an `old` side of `false`.
    - `()` — both sides were read and they match. Reachable without being a bug: the tool answers
      `no_op` instead of gating when the account is already in the state the change would
      produce, so the only way here is a row whose account moved and moved back while the request
      sat pending — and a delta claiming a move there would describe one that did not happen.
    - one row — the two sides differ, which is the ordinary confirmed change.
    """
    old = target.suspended_before
    if old is None:
        return None
    if old == direction.target_suspended:
        return ()
    return (FieldChange(field=DELTA_FIELD_SUSPENDED, old=old, new=direction.target_suspended),)


async def _verify_account_state(
    target: _ChangeTarget,
    *,
    direction: _AccountChangeDirection,
    request: ChangeExecutionRequest,
) -> ChangeOutcome:
    """Re-read the account and say whether the change took (the crypt-verify rule, one system
    over).

    Three answers, and the middle one is why this is a function rather than a boolean:

    - the account reads the way the change asked for → done, and verified;
    - it reads the other way → WHM accepted a call that did not take, which is a failure;
    - the read did not produce a state → the change happened and is **unverified**. Reporting that
      as a failure would send an operator to repeat a change that may already have taken;
      reporting it as a plain success would claim a confirmation nobody has.

    **Two ways to reach the third answer, and they are named apart in the cause.** WHM may refuse
    the read outright, or it may answer with a row whose `suspended` is a spelling the normaliser
    does not read, which arrives here as a row with no such key. Both are "NOA holds no reading",
    and neither may be folded into a boolean: `(row.get("suspended") is True)` turns an unread
    field into `false`, which reports a landed suspension as `postflight_failed` and — worse,
    because it is a claim rather than a complaint — reports an unsuspension nobody confirmed as a
    verified success. `account_suspension_state` is where absence stays distinguishable from a
    read `false`, and it is the same reader the preflight and the delta's `old` side go through.

    One function for both directions: `direction.target_suspended` is the only thing that
    differs, and a second copy of these branches is a second place the third answer can be
    dropped.

    The re-read goes through the credential that performed the write, which is why the summary it
    answers with is never the delta's material: the identity is built from two strings this
    function already holds, and the summary stays in this frame.
    """
    result = await target.client.list_accounts()
    verified = (
        match_account(normalize_whm_account_list(result.get("accounts")), username=target.username)
        if result.get("ok") is True
        else None
    )
    state = account_suspension_state(verified) if verified is not None else None

    if state is None:
        if verified is None:
            cause = str(result.get("error_code") or MESSAGE_LIST_ACCOUNTS_FAILED)
            detail = "the confirming read did not answer"
        else:
            cause = ERROR_SUSPENSION_STATE_UNREADABLE
            detail = "the confirming read did not report a suspension state NOA can read"
        logger.warning(
            direction.unverified_log_event,
            tool=direction.tool_name,
            action_request_id=str(request.action_request_id),
            username=target.username,
            cause=cause,
        )
        return ChangeOutcome(
            payload=tool_ok(
                status=STATUS_CHANGED,
                server=target.server_name,
                username=target.username,
                verified=False,
                verification=VERIFICATION_UNAVAILABLE,
                message=(
                    f"WHM accepted the {direction.noun} of `{target.username}`, but {detail}. "
                    "Check the account on the server."
                ),
            ),
            delta=_account_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=cause,
                changed_fields=None,
            ),
        )

    if state is not direction.target_suspended:
        return ChangeOutcome(
            payload=tool_failure(ERROR_POSTFLIGHT_FAILED, direction.postflight_message),
            # Measured and disagreeing: the account was re-read and the field did not move.
            delta=_account_delta(target, verification=VERIFICATION_MISMATCH, changed_fields=()),
        )

    return ChangeOutcome(
        payload=tool_ok(
            status=STATUS_CHANGED,
            server=target.server_name,
            username=target.username,
            suspended=direction.target_suspended,
            verified=True,
            message=f"`{target.username}` {direction.confirmed_state}.",
        ),
        delta=_account_delta(
            target,
            verification=VERIFICATION_VERIFIED,
            changed_fields=_suspension_change(target, direction=direction),
        ),
    )


def _passthrough_failure(
    target: _ChangeTarget, result: dict[str, object], *, fallback: str
) -> ChangeOutcome:
    """A `WHMClient` failure as a tool failure, keeping the code that names the remedy.

    The delta beside it states **no** field change. WHM refusing a call is not the same as WHM
    reporting that nothing happened: a timeout or a dropped connection arrives here too, and the
    mutation behind it may have landed. `()` would claim a re-read that never happened, and
    `false` in a rendered diff would read as one.
    """
    message = result.get("message")
    spoken = message if isinstance(message, str) and message.strip() else None
    code = str(result.get("error_code") or ERROR_UNKNOWN)
    return ChangeOutcome(
        payload=tool_failure(code, spoken or fallback),
        delta=_account_delta(
            target,
            verification=VERIFICATION_UNAVAILABLE,
            verification_cause=code,
            changed_fields=None,
        ),
    )


__all__ = [
    "DELTA_FIELD_SUSPENDED",
    "build_whm_account_change_runners",
    "build_whm_suspend_runner",
    "build_whm_unsuspend_runner",
]
