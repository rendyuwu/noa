"""The half of `pmg_whitelist` that edits `mynetworks`.

Beside `pmg_whitelist.py` rather than inside it, for the file-size cap and on the boundary the
design already
draws — **nothing in the tool module can change anything, and nothing here is reachable without an
approval**. The password-reset runner and the NIC tool made the same split for the same reason.
The evidence keys, the
action words and the tool's name come from that module; nothing there imports this one, so
`registry.py` reaches the tool and `change_runners.py` reaches the runner with no cycle between
them.

**The re-read is the design, not an optimisation.** PMG has no compare-and-set token, so the
CAS-token-from-the-runner rule's
first half — a CAS token taken from the runner's own read — has no instance here. Its second half
does: what the approval window is checked against is the **fact** the operator approved, which is
whether the target is on the list, re-measured now. Three ways that goes and only one is a failure:

- the whitelist is still in the state the card described → do the change.
- it is **already** in the state that was asked for → `no_op`. Somebody reached it first; the world
  is as the operator wanted it, and calling that a failure would send them to fix something that
  is not broken.
- the list cannot be read → refuse with the integration's own code. A change decided against a
  read that did not answer is a change decided against nothing.

**Two deliberate departures from `noa-old`** (port, never import; ported code is copied, not
rewritten: a port carries the code, not the defect):

1. **A removal takes every matching line, each by PMG's own spelling.** `mynetworks` can hold two
   spellings of one address — `1.2.3.4` and `1.2.3.4/32` are one entry to a reader and two
   lines in the file — and `core.integrations.pmg.mynetworks` keeps both for exactly this reason.
   `noa-old` sent one `delete` for the *normalised* form, which leaves the duplicate standing and
   aims `pmgsh delete /config/mynetworks/<cidr>` at a path segment PMG may never have printed. The
   deletion here names `entry.cidr`, the token `pmgsh ls` emitted, which is the object identifier
   PMG itself gave.
2. **An add writes the normalised form**, because that is what membership was decided on and what
   the card showed the operator. A `1.2.3.4/24` typed in chat is a request about
   `1.2.3.0/24`, and writing back the operator's own spelling would put a line in `mynetworks`
   that NOA's own reader then normalises to something else.

**`pmgconfig sync --restart 1` after every mutation**. `pmgsh` writes PMG's config and
Postfix does not pick the change up until the sync runs, so a mutation that skips it looks applied
and is not. A write that landed while the sync failed gets **its own code**: the config row moved
and mail flow did not, and neither `ok: true, status: changed` nor a bare failure says that — the
first is a lie and the second sends an operator back to re-add into a no-op.

**The postflight asks the change's own question**. It re-reads `mynetworks` and re-tests
membership — not the `200 OK` on stdout, which says PMG accepted a write, and not the sync's exit
code. A read that cannot answer is `unavailable`, never `false`: silence is not evidence of absence
(folding a non-answer into the benign value), and the password-reset verdict-on-verify rule holds
one system over — **verification-unavailable is not verified, and it is
not refuted either.**

One bound stated rather than implied: this postflight verifies PMG's **config**. `pmgconfig sync`'s
own success is the only thing saying Postfix picked the change up, because NOA reads `mynetworks`
through `pmgsh` and has no view of Postfix's live table. That is why the sync is a step whose
failure is reported by name instead of being folded into the verdict.

**The no-path-back rule has no instance here, and that is worth stating rather than assuming.**
This runner never
reads `request.reason`: a `mynetworks` entry is a CIDR and nothing else — no comment, no note, no
description — so nothing the operator-typed reason field keeps from the LLM is written onto a PMG
node, and nothing NOA wrote
comes back through a later READ. It is also why a backend failure message travels whole here while
the allowlist-remove tool cuts its own (`backend_change_failure`): csf quotes an entry NOA wrote a
comment onto, and
`pmgsh` has no comment to quote.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import structlog

from core.approvals.delta import (
    # `VERIFICATION_UNAVAILABLE` arrives below through `change_target`, the same string from the
    # same definition — `core.approvals.delta` owns all four states.
    VERIFICATION_MISMATCH,
    VERIFICATION_NOT_IN_FORCE,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
    ListDelta,
)
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.mynetworks import (
    MynetworksEntry,
    find_matching_entries,
    normalize_cidr,
    parse_mynetworks_entries,
)
from core.integrations.pmg.pmgsh_cli import (
    run_pmg_mynetworks_add,
    run_pmg_mynetworks_delete,
    run_pmg_mynetworks_list,
    run_pmgconfig_sync_restart,
)
from core.integrations.pmg.ssh import resolve_pmg_ssh_config
from core.remote_exec.types import SSHConnectionConfig
from noa_api.mcp_tools.change_target import (
    ERROR_EVIDENCE_UNUSABLE,
    MESSAGE_EVIDENCE_UNUSABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.pmg_whitelist import (
    ACTION_ADD,
    ACTIONS,
    ERROR_SERVER_UNAVAILABLE,
    EVIDENCE_ACTION,
    EVIDENCE_NORMALIZED_TARGET,
    EVIDENCE_SERVER_ID,
    EVIDENCE_TARGET,
    EVIDENCE_TOTAL_ENTRIES,
    MESSAGE_SERVER_UNAVAILABLE,
    TOOL_PMG_WHITELIST,
)
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    tool_failure,
    tool_ok,
)

# `pmgsh` accepted the entry and `pmgconfig sync` did not run to completion. The config moved and
# Postfix did not, which is neither a change nor a refusal — see the module docstring.
ERROR_SYNC_FAILED: Final = "pmg_sync_failed"

# The write was accepted, the postflight read answered, and the whitelist is not where it was asked
# to be. Distinct from the unavailable case: this is a measurement.
ERROR_POSTFLIGHT_FAILED: Final = "postflight_failed"

# One structured event per outcome an operator may have to act on. Identifiers and codes only
# — and never `request.reason`, which this runner does not read at all.
LOG_WHITELIST_RUN_NO_OP: Final = "pmg_whitelist_no_op"
LOG_WHITELIST_RUN_UNVERIFIED: Final = "pmg_whitelist_unverified"

logger = structlog.get_logger(__name__)


# --- The runner: reachable only after an operator approved (the far side of the cookie/CSRF
# boundary) ---


@dataclass(frozen=True)
class WhitelistChangeTarget:
    """The node, the direction and the address an approved whitelist change runs against.

    Both spellings of the address travel, for the reason they travel on the card: `normalized` is
    what is compared and written, and `target` is what the operator typed and therefore what
    a sentence about their request has to be able to say.
    """

    config: SSHConnectionConfig
    server_name: str
    action: str
    target: str
    normalized: str

    @property
    def adding(self) -> bool:
        """Is this change asking for the address to be on the list?"""
        return self.action == ACTION_ADD


def build_pmg_whitelist_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that edits `mynetworks`, once an operator approved.

    A closure over the tool context rather than a class, for the suspend tool's reason: what it
    needs is the
    same session factory, cipher and repository the tool used, so the change goes through the
    production decrypt site rather than a second copy of it.

    `request.reason` is on the request — the executor reads it off the row for every approved
    change — and this runner never touches it. A `mynetworks` entry has no field for one, so
    nothing the operator-typed reason field keeps from the LLM leaves NOA here and the no-path-back
    rule's bound has no instance on this tool.
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Apply this approved whitelist change, and say what happened.

        Resolve from the evidence, re-read, decide, write, sync, verify. Every refusal answers the
        ordinary tool envelope rather than raising, because the executor's own catch records
        something coarser than what this knew.

        Three deltas are decided here and the rest below. The resolution refusal carries **none**
        — nothing was read and nothing was written, so there is nothing to state. A
        `mynetworks` read that could not answer carries one with **no list move**, because the
        list was never seen. The no-op carries one with an **empty** list move, which is not the
        same thing: the list was read, it already held what was asked for, and "NOA compared and
        nothing moved" is what an operator needs to be told rather than an absent facet a
        renderer would show as a blank.
        """
        target = await _resolve_change_target(request.evidence, context=context)
        if not isinstance(target, WhitelistChangeTarget):
            return ChangeOutcome(payload=target)

        entries = _evidence_total_entries(request.evidence)
        before = await _read_matches(target)
        if not isinstance(before, list):
            return ChangeOutcome(
                payload={**before, **_common(target)},
                delta=_whitelist_delta(
                    target,
                    verification=VERIFICATION_UNAVAILABLE,
                    verification_cause=str(before.get("error_code") or ERROR_UNKNOWN),
                ),
            )

        if bool(before) == target.adding:
            logger.info(
                LOG_WHITELIST_RUN_NO_OP,
                tool=TOOL_PMG_WHITELIST,
                action_request_id=str(request.action_request_id),
                server=target.server_name,
                action=target.action,
            )
            return ChangeOutcome(
                payload=tool_ok(
                    **_common(target),
                    status=STATUS_NO_OP,
                    exists=target.adding,
                    verified=True,
                    message=(
                        f"`{target.normalized}` was already "
                        f"{'on' if target.adding else 'off'} the mynetworks whitelist on "
                        f"{target.server_name} when NOA ran this change, so nothing was written."
                    ),
                ),
                delta=_whitelist_delta(
                    target,
                    verification=VERIFICATION_VERIFIED,
                    list_delta=ListDelta(total_entries=entries),
                ),
            )

        applied = await _apply_change(target, matches=before)
        moved = ListDelta(added=applied.added, removed=applied.removed, total_entries=entries)
        if applied.failure is not None:
            return ChangeOutcome(
                payload=applied.failure,
                # The list move rides **beside** the failure rather than instead of it: a
                # refused `delete` stops mid-loop with the lines it already took gone from the
                # config, and a sync that failed leaves every line written and none in force.
                # Both are measurements, and dropping them would report a half-done change as
                # one that did nothing.
                delta=_whitelist_delta(
                    target,
                    verification=(
                        VERIFICATION_NOT_IN_FORCE if applied.written else VERIFICATION_UNAVAILABLE
                    ),
                    verification_cause=applied.cause,
                    list_delta=moved,
                ),
            )

        return await _verify_membership(target, request=request, moved=moved)

    return run


def build_pmg_whitelist_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tool."""
    return {TOOL_PMG_WHITELIST: build_pmg_whitelist_runner(context=context)}


# --- Internals ---


async def _resolve_change_target(
    evidence: Mapping[str, Any], *, context: McpToolContext
) -> WhitelistChangeTarget | ToolPayload:
    """The node, direction and address an approved change runs against.

    **From the evidence, never from the arguments.** `server_ref` is a string a model supplied and
    inventory can be edited between a request and its approval; the evidence is the state the
    operator actually saw on the card.

    Every value is re-checked as it comes back out of JSONB, `action` included — that is the third
    place the enum is bounded (the release-and-allow tool's discipline, the
    enum-bounded-in-three-places shape one system over): what `tools/list`
    publishes, what the tool body re-checks, and what survived the round trip. The normalised
    target is re-parsed rather than trusted, because it becomes an argv token in a `pmgsh` command
    and a value that no longer reads as a network is one NOA declines rather than sends.

    The database session closes before the SSH hop, the account search's rule, and here it matters
    twice over: the executor's own session is open for the whole of the call.
    """
    action = evidence.get(EVIDENCE_ACTION)
    target = evidence.get(EVIDENCE_TARGET)
    normalized = evidence.get(EVIDENCE_NORMALIZED_TARGET)
    if (
        action not in ACTIONS
        or not isinstance(target, str)
        or not target.strip()
        or not isinstance(normalized, str)
        or normalize_cidr(normalized) != normalized
    ):
        return tool_failure(ERROR_EVIDENCE_UNUSABLE, MESSAGE_EVIDENCE_UNUSABLE)

    server_id = uuid_or_none(evidence.get(EVIDENCE_SERVER_ID))
    if server_id is None:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    async with context.session_factory() as session:
        repository = context.pmg_server_repository_factory(session)
        server = await repository.get_by_id(server_id)
        if server is None:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
        server_name = server.name
        config = resolve_pmg_ssh_config(
            server,
            cipher=context.secret_cipher,
            require_host_key_fingerprint=True,
        )

    return WhitelistChangeTarget(
        config=config,
        server_name=server_name,
        action=str(action),
        target=target.strip(),
        normalized=normalized,
    )


async def _read_matches(target: WhitelistChangeTarget) -> list[MynetworksEntry] | ToolPayload:
    """Every `mynetworks` line that *is* this target, as PMG has it now.

    Exact membership, never containment: a containing network is not this entry, and a removal that
    treated it as one would delete a CIDR the operator never named.

    A `PMGSHCLIError` becomes the envelope rather than travelling as an exception, because a runner
    that raises arrives at the executor as a coarser code than the integration layer already knew
    (raw exceptions never reach the LLM). `ssh_sudo_required` and `pmgsh_command_failed` keep naming
    different remedies here for the same reason they do in the READ tools (`noa-old` GH #82).
    """
    try:
        output = await run_pmg_mynetworks_list(target.config)
    except PMGSHCLIError as exc:
        return tool_failure(exc.error_code, exc.message)
    entries = parse_mynetworks_entries(output)
    return find_matching_entries(entries, normalized_target=target.normalized)


@dataclass(frozen=True)
class _Applied:
    """What the write step actually moved, and the refusal if there was one.

    `failure` is the envelope, `None` when both steps took. The other three are the delta's
    material and they are carried out of here rather than inferred by the caller, because this is
    the only frame that knows *how far* a partial removal got: the loop below stops at the first
    refusal, so "which lines are gone" is a fact only the loop holds.

    `written` separates the two failures the module docstring keeps apart. `pmgsh` refused means
    nothing was accepted; `pmgconfig sync` refused means everything was accepted and none of it
    is in force, which is a third outcome and not a shade of either.
    """

    failure: ToolPayload | None = None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    written: bool = False
    cause: str | None = None


async def _apply_change(
    target: WhitelistChangeTarget, *, matches: list[MynetworksEntry]
) -> _Applied:
    """Write the change and apply it. No `failure` when both steps took.

    The two failures are kept apart on purpose. A refused `pmgsh create`/`delete` means nothing
    moved. A refused `pmgconfig sync` means the config moved and Postfix did not — see the module
    docstring — so it carries its own code and says the entry is written but not in force.

    A removal sends one `delete` per matching line, each naming that line's own spelling, and stops
    at the first refusal: the remaining lines are still on the box and the postflight will find
    them, which is a truer answer than pressing on and reporting a partial removal as a whole one.
    The lines that *did* go are collected as they go, one at a time, so a refusal halfway through
    reports what it moved instead of reporting nothing.
    """
    removed: list[str] = []
    try:
        if target.adding:
            await run_pmg_mynetworks_add(target.config, cidr=target.normalized)
        else:
            for entry in matches:
                await run_pmg_mynetworks_delete(target.config, cidr=entry.cidr)
                removed.append(entry.cidr)
    except PMGSHCLIError as exc:
        return _Applied(
            failure={
                **tool_failure(exc.error_code, exc.message),
                **_common(target),
                "applied": False,
            },
            removed=tuple(removed),
            cause=exc.error_code,
        )

    added = (target.normalized,) if target.adding else ()
    try:
        await run_pmgconfig_sync_restart(target.config)
    except PMGSHCLIError as exc:
        return _Applied(
            failure={
                **tool_failure(
                    ERROR_SYNC_FAILED,
                    f"PMG accepted the whitelist change for `{target.normalized}` on "
                    f"{target.server_name}, but `pmgconfig sync` failed, so mail flow has not "
                    f"picked it up yet: {exc.message}",
                ),
                **_common(target),
                "applied": False,
            },
            added=added,
            removed=tuple(removed),
            written=True,
            cause=ERROR_SYNC_FAILED,
        )
    return _Applied(added=added, removed=tuple(removed), written=True)


def _evidence_total_entries(evidence: Mapping[str, Any]) -> int | None:
    """How many lines `mynetworks` held when the operator was asked.

    Off the evidence, because it is the only place the whole list was counted: this runner reads
    the lines that *match* its target and never the rest, so a total taken here would be a total
    of four entries on a gateway that holds forty.

    `None` when the evidence carries no usable count — a row opened before the key existed.
    Absent rather than zero: a list nobody counted is not an empty list.
    """
    total = evidence.get(EVIDENCE_TOTAL_ENTRIES)
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    return total


def _whitelist_delta(
    target: WhitelistChangeTarget,
    *,
    verification: str,
    verification_cause: str | None = None,
    list_delta: ListDelta | None = None,
) -> ChangeDelta:
    """This change's before→after, as the runner that ran it states it.

    **The facet is `list_delta`**, because a `mynetworks` entry has no value that changes — it is
    either a line in the file or it is not, and the change is its membership. Both spellings of
    the address ride on the identity for the reason they ride in the payload: a reader told
    only that `203.0.113.0/24` moved cannot tell whether an operator asked about a network or a
    host inside it.

    `added` and `removed` hold PMG's own spelling of each line, not the normalised form, because
    `mynetworks` can hold `1.2.3.4` and `1.2.3.4/32` as two lines and one entry — a receipt
    saying "removed" without saying which lines is a receipt that cannot be checked against the
    box.
    """
    return ChangeDelta(
        identity=_common(target),
        verification=verification,
        verification_cause=verification_cause,
        list_delta=list_delta,
    )


async def _verify_membership(
    target: WhitelistChangeTarget,
    *,
    request: ChangeExecutionRequest,
    moved: ListDelta,
) -> ChangeOutcome:
    """Is the address on the list now? Read off `mynetworks`, never off the exit code.

    `pmgsh` printing `200 OK` says PMG accepted a write; it does not say what the whitelist now
    holds. So this re-reads the list and re-tests membership, which is the fact the change is about.

    Three answers, and the middle one is the password-reset verdict-on-verify rule one system over:

    1. **verified** — the list carries the state that was asked for.
    2. **unavailable** — the postflight read could not answer. `status: changed` with
       `verified: false` and `verification: unavailable`, never a bare `false` that reads as a
       measurement. The write and the sync were both accepted, so reporting this as a failure would
       send an operator to repeat a change that has probably already happened.
    3. **mismatch** — the list is readable and the address is not where it was asked to be. That is
       a measurement, and it is a failure.

    All three carry the list move that already happened, beside the verdict rather than instead
    of it: the writes were accepted on every one of these paths, so what they moved is a fact
    even where the confirming read disagrees with it or could not answer at all.
    """
    after = await _read_matches(target)
    if not isinstance(after, list):
        cause = str(after.get("error_code") or ERROR_UNKNOWN)
        logger.warning(
            LOG_WHITELIST_RUN_UNVERIFIED,
            tool=TOOL_PMG_WHITELIST,
            action_request_id=str(request.action_request_id),
            server=target.server_name,
            action=target.action,
            cause=cause,
        )
        return ChangeOutcome(
            payload=tool_ok(
                **_common(target),
                status=STATUS_CHANGED,
                verified=False,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=cause,
                message=(
                    f"PMG accepted the whitelist change for `{target.normalized}` on "
                    f"{target.server_name}, but NOA could not read the list back to confirm it. "
                    "Check the server before relying on it."
                ),
            ),
            delta=_whitelist_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=cause,
                list_delta=moved,
            ),
        )

    exists = bool(after)
    if exists != target.adding:
        return ChangeOutcome(
            payload={
                **tool_failure(
                    ERROR_POSTFLIGHT_FAILED,
                    f"PMG accepted the change, but `{target.normalized}` on "
                    f"{target.server_name} is still "
                    f"{'absent from' if target.adding else 'on'} the mynetworks whitelist. "
                    "Check the server.",
                ),
                **_common(target),
                "exists": exists,
                "verified": False,
            },
            delta=_whitelist_delta(target, verification=VERIFICATION_MISMATCH, list_delta=moved),
        )

    return ChangeOutcome(
        payload=tool_ok(
            **_common(target),
            status=STATUS_CHANGED,
            exists=exists,
            verified=True,
            # PMG's own spelling of each line a removal took. An entry stored as a bare host and
            # its `/32` twin are two lines, and a receipt saying "removed" without saying how
            # many is a receipt that cannot be checked against the box.
            removed=list(moved.removed),
            message=(
                f"`{target.normalized}` was "
                f"{'added to' if target.adding else 'removed from'} the mynetworks whitelist on "
                f"{target.server_name} and confirmed by a fresh read."
            ),
        ),
        delta=_whitelist_delta(target, verification=VERIFICATION_VERIFIED, list_delta=moved),
    )


def _common(target: WhitelistChangeTarget) -> ToolPayload:
    """The identifiers every answer from this runner carries.

    Both spellings, for the exact-membership rule's reason: this payload becomes
    `tool_runs.result_summary` and `noa_get_action_result` hands that to a model, and a model told
    only that `1.2.3.4/24` was whitelisted would report a host where a network was changed.

    The whitelist's other entries are deliberately not among them. What a model needs is which
    address on which node moved which way — not the rest of a mail gateway's allow list.
    """
    return {
        "server": target.server_name,
        "action": target.action,
        "target": target.target,
        "normalized_target": target.normalized,
    }


__all__ = [
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_SYNC_FAILED",
    "LOG_WHITELIST_RUN_NO_OP",
    "LOG_WHITELIST_RUN_UNVERIFIED",
    "WhitelistChangeTarget",
    "build_pmg_whitelist_runner",
    "build_pmg_whitelist_runners",
]
