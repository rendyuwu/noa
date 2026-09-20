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

**Every sentence and heading an operator reads for this family is composed here.** The approval
card is to render the runner's own bytes unchanged and no LLM authors any of them, so whatever
this module writes into `headline` and `message` is what a person reads off the completed card and
pastes into a ticket.

One half of that is not true yet, and saying so here is cheaper than a reader discovering it:
**nothing renders `headline` today.** The completed card still builds its heading from the tool
name, so this family's card currently reads `Whm Suspend Account`. The key is written ahead of its
reader on purpose — the surfaces cannot be rewritten before the values they render exist, and
every row opened before that rewrite has to keep rendering — but until the embed reads it, a
`headline` here is a value in `tool_runs.result_summary` and nothing a person sees. Two
consequences that shape the wording below: the words state
facts rather than verdicts, because the card's status corner already states the verdict off the
delta's verification; and the raw error code stays on the envelope's `error_code` rather than
inside a sentence, because it names a remedy to an engineer and names nothing to the operator
deciding what to do next.
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
    WriteFailure,
    confirmed_verification,
    confirmed_verification_sentence,
    uuid_or_none,
    write_failure_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import ToolPayload, tool_failure, tool_ok
from noa_api.mcp_tools.whm_account_change import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SUSPENSION_STATE_UNREADABLE,
    EVIDENCE_ACCOUNT,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    LOG_SUSPEND_UNVERIFIED,
    LOG_UNSUSPEND_UNVERIFIED,
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

# The confirming read's own deadline, and it is deliberately not the mutation's.
#
# `WHM_READ_TIMEOUT_SECONDS` is 120 by default because `unsuspendacct` was measured at 52.91 s on
# the owner's web8, and that budget belongs to the change. A read taken after it must not inherit
# it: 120 s of write plus 120 s of read is four minutes holding one of fifteen pool connections,
# for a question the write has already answered badly. 30 s is the 20 s every WHM call ran under
# until this week with half again on top: `listaccts` was served under that 20 s, and what moved
# the number was a write taking 52.91 s, not a read.
WHM_CONFIRM_READ_TIMEOUT_SECONDS: Final = 30.0

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
    # What WHM was asked to do, as it appears mid-sentence: "WHM accepted the {noun} of x".
    noun: str
    # The state the account reads in once the change took: "The x account {confirmed_state} on y."
    confirmed_state: str
    # The card's heading where the account reads the way the change asked, and where nothing could
    # be read at all — the latter because the corner beside it already says the change is
    # unconfirmed, so the heading there names what the card is about rather than a measurement.
    headline: str
    # The heading where the account was read and reads the other way. Not the negation of the one
    # above: "not suspended" and "still suspended" name the two directions' failures as an
    # operator would say them.
    mismatch_headline: str
    # The consequence of the act, stated once, in the owner's own words.
    #
    # **Empty on unsuspend, and nothing takes its place.** A suspension has a consequence worth
    # stating — the whole account goes unreachable — while lifting one produces no new consequence
    # to state, so a mirrored sentence there would be a clause nobody measured and nobody
    # supplied. Decided by the owner on 2026-09-13.
    consequence: str
    # What to say when WHM's own refusal carried no sentence of its own.
    failure_message: str
    unverified_log_event: str


_SUSPEND: Final = _AccountChangeDirection(
    tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
    target_suspended=True,
    noun="suspension",
    confirmed_state="is suspended",
    headline="Account suspended",
    mismatch_headline="Account not suspended",
    consequence="The whole account — nothing on it is reachable.",
    failure_message=MESSAGE_SUSPEND_FAILED,
    unverified_log_event=LOG_SUSPEND_UNVERIFIED,
)

_UNSUSPEND: Final = _AccountChangeDirection(
    tool_name=TOOL_WHM_UNSUSPEND_ACCOUNT,
    target_suspended=False,
    noun="unsuspension",
    confirmed_state="is no longer suspended",
    headline="Account unsuspended",
    mismatch_headline="Account still suspended",
    # Deliberately empty, and the field's own comment holds why: no mirror of the suspend
    # direction's consequence sentence goes here.
    consequence="",
    failure_message=MESSAGE_UNSUSPEND_FAILED,
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
        # A failed call is not an answer about the account yet, so there is no early return here:
        # the postflight runs either way and the failure travels into it.
        return await _verify_account_state(
            target,
            direction=_SUSPEND,
            request=request,
            write_failure=write_failure_or_none(mutation),
        )

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
        # A locked suspension the tool's preflight did not see — the lock was set after the
        # request was opened, or WHM did not report it — arrives as `whm_api_error` carrying
        # WHM's own `reason`, which is the sentence that names the remedy. It rides into the
        # postflight on the failure rather than short-circuiting it, because a refused call and
        # an unanswered one want different things from the account's current state.
        return await _verify_account_state(
            target,
            direction=_UNSUSPEND,
            request=request,
            write_failure=write_failure_or_none(mutation),
        )

    return run


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


def _state_words(suspended: bool) -> str:
    """One boolean as an operator reads it, and the only spelling of it this module composes."""
    return "suspended" if suspended else "not suspended"


def _before_clause(
    changed_fields: tuple[FieldChange, ...] | None,
    *,
    target: _ChangeTarget,
    direction: _AccountChangeDirection,
) -> str:
    """What the account read before the change ran, in the one spelling that branch earned.

    The value-bearing spellings are read off the **same** material the delta beside them is built
    from — the confirmed row off the tuple, the rest off the gate-time reading that tuple is
    derived from — so the line an operator reads on the card and the field change an administrator
    opens in the audit drawer cannot state two different before-values.

    **The grammar carries the distinction, and that is the point of the wording rather than a
    style choice.** "before this ran" claims a *comparison* — NOA holds both sides and is naming
    the one it started from. "when NOA last read it" claims only a *reading* — NOA holds the
    gate-time side and has nothing to set against it. Neither may be spelled the other way, and the
    two middle cases are the ones a later simplification would fold together:

    - one row — both sides were read and they differ. The ordinary confirmed change.
    - `()` — both sides were read and they match: measured, and nothing moved. Only reachable
      where the gate-time reading already equalled what the change asked for, which is the value
      the line therefore names.
    - `None` and a gate-time reading on the evidence — the postflight could not confirm, so no
      *change* can be claimed; the reading the operator authorised against exists all the same,
      and it is named. This is the branch that sends an operator to WHM to check the account by
      hand, which makes it exactly the branch where they need something to compare what they find
      against — and it is the branch whose approval card, read a minute earlier, displayed that
      same reading.
    - `None` and no gate-time reading — the evidence genuinely carried none, so the line says NOA
      holds no reading rather than naming a value. An `old` side nobody recorded is not an `old`
      side of `false`, and printing "it was not suspended" off an absent reading is exactly the
      fabrication the facet's three states exist to keep apart.

    **The absence test is `is None`, never falsiness.** `suspended_before` is a three-state value
    and `False` is one of its two readings; a guard written `if not target.suspended_before:`
    would send a live account's perfectly good reading down the no-reading path and put the defect
    back under a new spelling. `account_suspension_state` is what keeps absence distinguishable
    from a read `false`, and this is the last surface that distinction has to survive to.
    """
    if target.suspended_before is None:
        return "NOA has no reading of what it was before."
    if changed_fields is None:
        return f"It was {_state_words(target.suspended_before)} when NOA last read it."
    if not changed_fields:
        return f"It already read {_state_words(direction.target_suspended)} before this ran."
    return f"It was {_state_words(bool(changed_fields[0].old))} before this ran."


async def _verify_account_state(
    target: _ChangeTarget,
    *,
    direction: _AccountChangeDirection,
    request: ChangeExecutionRequest,
    write_failure: WriteFailure | None = None,
) -> ChangeOutcome:
    """Re-read the account and say whether the change took (the crypt-verify rule, one system
    over).

    **`write_failure` is how a failed call reaches here without being erased by it.** It is
    `None` on the ordinary path and every branch below reads exactly as it did before. When it
    is set, the call to WHM failed and this reading is the better witness of what the account
    holds — but a postflight that did not know would emit a clean success and leave no trace
    anything went wrong, so the failure shapes the sentence and the verdict rather than being
    dropped.

    The re-read carries its own deadline (`WHM_CONFIRM_READ_TIMEOUT_SECONDS`), which is not the
    mutation's: the write's 120 s exists for `unsuspendacct`, and a read taken after it must not
    hold a pool connection for a second two-minute budget.

    Three answers about the account itself, and the middle one is why this is a function rather
    than a boolean:

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
    result = await target.client.list_accounts(
        read_timeout_seconds=WHM_CONFIRM_READ_TIMEOUT_SECONDS
    )
    verified = (
        match_account(normalize_whm_account_list(result.get("accounts")), username=target.username)
        if result.get("ok") is True
        else None
    )
    state = account_suspension_state(verified) if verified is not None else None

    # **Nothing composed below is built from the row this read answered with.** WHM stores the
    # operator's typed NOA reason as the suspension note and echoes it back as `suspendreason` on
    # every later `listaccts` row, so a sentence assembled from that row would render a previous
    # decision's reason on this card. Every string here is built from the username, the server
    # name and the one boolean.

    if state is None:
        # Two shapes of non-answer, kept apart in the sentence as well as in the cause, because
        # they send an operator to different places: a read that never answered says nothing about
        # the account, while a read that answered without a state NOA can spell says WHM's own
        # version reports the field in a way this build does not know.
        if verified is None:
            cause = str(result.get("error_code") or MESSAGE_LIST_ACCOUNTS_FAILED)
            detail = "NOA could not read the account back afterwards"
        else:
            cause = ERROR_SUSPENSION_STATE_UNREADABLE
            detail = "NOA read the account back and WHM did not say whether it is suspended"
        logger.warning(
            direction.unverified_log_event,
            tool=direction.tool_name,
            action_request_id=str(request.action_request_id),
            username=target.username,
            cause=cause,
            write_failure=None if write_failure is None else write_failure.code,
        )
        # The cause names the **confirming read**, not the write, on both paths: what it answers
        # is why NOA holds no measurement, and the write's own code is on the envelope beside it
        # where a reader looks for what failed. Two questions, two fields.
        #
        # `None` is the changed-fields answer this branch takes, and it is a literal in both places
        # rather than a value that happens to arrive as one: the line below and the delta at the
        # foot of this return are each handed `None` outright. That is the whole reason the
        # measured-empty spelling cannot appear here — nothing is passed through that could carry
        # it — and it needs no argument about where an empty tuple is produced. An empty tuple here
        # would tell an operator NOA compared and found nothing moved, about a change nothing was
        # read for.
        #
        # Nothing compared is not nothing read, and the clause keeps the two apart: the gate-time
        # reading is still on the target, and this is the branch that sends an operator to WHM to
        # check the account by hand, so it is named as the thing to check against.
        unconfirmed = f"{detail}, so it cannot say the account {direction.confirmed_state}."
        before = _before_clause(None, target=target, direction=direction)
        # The heading names what the card is about rather than a reading, because there is no
        # reading: the corner beside it states that the change is unconfirmed, off the delta.
        headline = f"{direction.headline} — {target.username}"
        return ChangeOutcome(
            payload=(
                tool_ok(
                    headline=headline,
                    status=STATUS_CHANGED,
                    server=target.server_name,
                    username=target.username,
                    verified=False,
                    verification=VERIFICATION_UNAVAILABLE,
                    message=(
                        f"WHM accepted the {direction.noun} of {target.username} on "
                        f"{target.server_name}. {unconfirmed}\n{before}"
                    ),
                )
                if write_failure is None
                else {
                    **tool_failure(
                        write_failure.code,
                        f"WHM {write_failure.verb} the {direction.noun} of {target.username} on "
                        f"{target.server_name}. {unconfirmed}\n{before}",
                    ),
                    "headline": headline,
                }
            ),
            delta=_account_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=cause,
                changed_fields=None,
            ),
        )

    matched = state is direction.target_suspended

    if write_failure is None:
        if not matched:
            return ChangeOutcome(
                payload={
                    **tool_failure(
                        ERROR_POSTFLIGHT_FAILED,
                        # This sentence **is** the measurement, so no before-clause line follows
                        # it: the reading it states is what a second line would restate, and two
                        # statements of one reading read as two readings.
                        f"NOA read the account back on {target.server_name}: "
                        f"{target.username} is {_state_words(state)}.",
                    ),
                    "headline": f"{direction.mismatch_headline} — {target.username}",
                },
                # Measured and disagreeing: the account was re-read and the field did not move.
                delta=_account_delta(target, verification=VERIFICATION_MISMATCH, changed_fields=()),
            )

        changed_fields = _suspension_change(target, direction=direction)
        # The owner's consequence sentence, where the direction has one. Nothing stands in for it
        # on the other direction — `_AccountChangeDirection.consequence` holds why.
        consequence = f" {direction.consequence}" if direction.consequence else ""
        return ChangeOutcome(
            payload=tool_ok(
                headline=f"{direction.headline} — {target.username}",
                status=STATUS_CHANGED,
                server=target.server_name,
                username=target.username,
                suspended=direction.target_suspended,
                verified=True,
                message=(
                    f"The {target.username} account {direction.confirmed_state} on "
                    f"{target.server_name}.{consequence}\n"
                    f"{_before_clause(changed_fields, target=target, direction=direction)}"
                ),
            ),
            delta=_account_delta(
                target,
                verification=VERIFICATION_VERIFIED,
                changed_fields=changed_fields,
            ),
        )

    verification, cause = confirmed_verification(matched=matched, failure=write_failure)
    reading = f"{target.username} is {_state_words(state)}"
    # The write's own error code is **not** spliced in here: it rides on the envelope's
    # `error_code`, where an administrator looks for it, and it names nothing to the operator
    # reading this line. What that reader needs from the failure is whether WHM refused or never
    # answered, which is what `verb` says in words.
    opener = (
        f"WHM {write_failure.verb} the {direction.noun} of {target.username} on "
        f"{target.server_name}"
    )

    if verification == VERIFICATION_VERIFIED:
        # The call never answered and the account is where the change asked for it. No hedge:
        # NOA sent the write, WHM took the connection, and qualifying every timeout with "NOA
        # cannot prove it caused this" teaches an operator to skip the qualifier.
        changed_fields = _suspension_change(target, direction=direction)
        return ChangeOutcome(
            payload=tool_ok(
                headline=f"{direction.headline} — {target.username}",
                status=STATUS_CHANGED,
                server=target.server_name,
                username=target.username,
                suspended=direction.target_suspended,
                verified=True,
                message=(
                    f"{opener}, so NOA re-read the account: {reading}.\n"
                    f"{_before_clause(changed_fields, target=target, direction=direction)}"
                ),
            ),
            delta=_account_delta(
                target,
                verification=VERIFICATION_VERIFIED,
                changed_fields=changed_fields,
            ),
        )

    # `()` only where WHM refused and the reading agrees with the refusal — a comparison was made
    # and both sides say nothing moved. Everywhere else `None`: a call that went unanswered may
    # still land, and an account already in the target state after a refusal was not put there by
    # this change, so neither is a diff NOA can state.
    measured_empty = verification == VERIFICATION_MISMATCH
    changed_fields = () if measured_empty else None
    # The `mismatch` sentence states the reading it took, the way the postflight's own mismatch
    # branch above does, so no before-clause line follows it. The other shapes here compared
    # nothing — and the line that follows *them* says that without claiming NOA read nothing: the
    # gate-time reading is what an operator checking the account by hand has to compare against.
    before = (
        ""
        if measured_empty
        else f"\n{_before_clause(changed_fields, target=target, direction=direction)}"
    )
    return ChangeOutcome(
        payload={
            **tool_failure(
                write_failure.code,
                confirmed_verification_sentence(
                    opener=opener,
                    reading=reading,
                    matched=matched,
                    failure=write_failure,
                    fallback=direction.failure_message,
                )
                + before,
            ),
            # The account was read, so the heading names what it reads as: the change's own
            # heading where the reading agrees with what was asked for, and the direction's
            # did-not-happen heading where it does not.
            "headline": (
                f"{direction.headline if matched else direction.mismatch_headline} — "
                f"{target.username}"
            ),
        },
        delta=_account_delta(
            target,
            verification=verification,
            verification_cause=cause,
            changed_fields=changed_fields,
        ),
    )
