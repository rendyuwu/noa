"""`whm_firewall_allowlist_remove` — the undo path for a firewall allow entry.

**Its own tool and its own approval, deliberately** (DECISIONS §6.5: "keep
`whm_firewall_allowlist_remove` separate — it is the undo path, run on its own"). T25 merged
steps 2 and 3 of the operator's working pattern because they are almost always run together;
this one is not part of that pattern at all. It is what an operator reaches for when an allow
entry should not have been written, or should stop existing before its window closes.

Beside `whm_firewall_change.py` rather than inside it, for C14: that module is 700 lines and a
CHANGE tool is two halves. What the two share lives in `whm_firewall_change_common.py`, which
both import and neither owns — so the two tools are siblings rather than one depending on the
other, and the aggregate registrar is not a cycle.

**The preflight is T24's, run in-process**, the same as T25's: the same availability
probe, the same dual-backend lookup. The evidence is born inside this call, lives milliseconds,
belongs to the same user, and reaches the operator through `approval_context` rather than through
a transcript.

**There is a no-op branch here, and T25's argument for not having one is exactly why.** T25 has
none because its allow entry carries a TTL, so a repeat always moves the expiry and is a real
change. A removal has no such property: an address with no allow entry anywhere is already in
the state this change would produce, and asking an operator to authorise that is worse than
saying so. What the no-op costs is a claim about absence, so it is **gated on a full
answer** — every usable backend has to have answered the preflight. One silent backend and
"there is nothing to remove" is a guess, so the card opens instead (V86: silence is not evidence
of absence, and here the absence is the whole claim).

**"Is there still an allow entry" is not the combined verdict, and that gap is a correctness
bug waiting to happen.** Both backends resolve a conflict block-first, so an address on a deny
list *and* an allow list reports as blocked and the allow entry vanishes from the verdict. Read
that way, a removal that failed on an address csf also denies would report as done. So the
question is asked of `allow_entry`, a fact the parsers now carry beside the verdict
(`core.integrations.whm.csf`), through `holds_allow_entry`.

**Every step is tolerated, and the postflight is the only authority.** `csf -tra` drops a
temporary allow and `csf -ar` a permanent one; an entry lives in one of those lists, not both,
so one of the two commands always reports "not in that list". Imunify refuses a delete for an
entry it does not hold. T25 could keep one required command — the allow entry the operator asked
to *exist* — and this one has no equivalent: nothing here is required to succeed, so the fresh
read afterwards is what decides whether the change took. A **sudo-rights** failure is the one
exception and it is not tolerated anywhere: sudoers can permit `csf -v` and refuse
`csf -ar`, and "the entry was not there" is never the right reading of "you may not run this".

**Every backend operation goes through `run_on_usable_backends`** — the change and
the postflight both. Zero usable backends is that door's refusal, not a check written here.

**V96 bites on the way back, not on the way out**. This tool writes no comment: `csf
-tra`/`-ar` and Imunify's delete take none, so nothing new leaves NOA and V43's permission is
not used. The bound still applies, because the entry being *removed* was written by T25 and
carries `noa:<action_request_id> <reason>`. Three doors, all closed here:

- a backend that refuses the command frequently quotes the entry back, and that message becomes
  `tool_runs.result_summary`, which `noa_get_action_result` hands a model — cut
  (`backend_change_failure`);
- the postflight's own `csf -g` lines are read for a verdict and never put in the payload, for
  the same reason;
- the **no-op answer is a transcript surface** — it is a plain tool result, not a gate
  response — so it is built from the server name, the address and one boolean NOA measured,
  never from the evidence lines it was decided from. That is T23's rule one system over, and it
  is the door this tool opens that T25 did not have.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Final

import structlog
from fastmcp import FastMCP
from pydantic import Field

from core.approvals.delta import (
    # `VERIFICATION_UNAVAILABLE` arrives below through `whm_firewall_change_common`, the same
    # string from the same definition — `core.approvals.delta` owns all four states.
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
    ListDelta,
)
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.db.lifecycle import ToolRisk
from core.errors import NoaError
from core.integrations.whm.availability import check_firewall_binaries
from core.integrations.whm.csf import parse_csf_target
from core.integrations.whm.firewall_gate import run_on_usable_backends
from core.integrations.whm.ssh import resolve_whm_ssh_config
from core.remote_exec.types import SSHConnectionConfig
from core.servers.whm_ref import resolve_whm_server_ref
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
from noa_api.mcp_tools.whm_firewall import (
    ERROR_INVALID_TARGET,
    ERROR_TARGET_REQUIRED,
    MESSAGE_TARGET_REQUIRED,
    BackendLookup,
    gather_firewall_entries,
)
from noa_api.mcp_tools.whm_firewall_change_common import (
    EVIDENCE_FIREWALL,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    BackendChange,
    FirewallChangeTarget,
    backend_change_failure,
    backend_outcomes,
    evidence_bound,
    firewall_state,
    holds_allow_entry,
    resolve_firewall_change_target,
    tolerated_csf_step,
    tolerated_imunify_step,
    unanswered_backends,
)

TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE = "whm_firewall_allowlist_remove"

# The postflight says an allow entry is still there. Its own code rather than T25's
# `firewall_allow_failed`, which means the opposite thing (an entry that should exist and does
# not) — one audit trail, and two codes that read alike would be worse than two that do not.
ERROR_ALLOWLIST_REMOVE_FAILED = "firewall_allowlist_remove_failed"

MESSAGE_CHANGE_TARGET_NOT_IPV4 = (
    "Only a single IPv4 address can be removed from the allow lists. Networks, IPv6 addresses "
    "and hostnames can be checked with `whm_preflight_firewall_entries` but not changed."
)
MESSAGE_ALLOWLIST_REMOVE_FAILED = (
    "The address is still on an allow list after the removal ran. Check the server's firewall "
    "directly before asking again."
)

# One structured event per tolerated step, so "the entry was already gone" and "the command
# could not run" stay separable after the fact. Identifiers and codes only, never a comment
#. Its own event name rather than T25's: a log event names the tool a reader is
# looking for, and `tool=` rides beside it as a field.
LOG_REMOVE_STEP_TOLERATED: Final = "whm_firewall_allowlist_remove_step_tolerated"

# The preflight found nothing to remove, so no card was opened. Answers "why is there no
# approval request" from the logs.
LOG_REMOVE_NO_OP: Final = "whm_firewall_allowlist_remove_no_op"

# The change ran and the confirming read could not answer. Warning, because an operator may want
# to look at the box.
LOG_REMOVE_UNVERIFIED: Final = "whm_firewall_allowlist_remove_unverified"

DESCRIPTION_WHM_FIREWALL_ALLOWLIST_REMOVE = (
    "Take one IPv4 address off a WHM server's firewall allow lists, in CSF and Imunify360 "
    "together. This is the undo path for `whm_firewall_release_and_allow`: use it when an "
    "address should stop being allowed before its window closes. It changes a live system, so "
    "it does not run when you call it: NOA reads the current firewall state, opens an approval "
    "request, and answers with the address of a card where an operator decides. Call "
    "`whm_preflight_firewall_entries` first and report what it found. If the address is on no "
    "allow list, this answers `no_op` and opens nothing. Read the outcome with "
    "`noa_get_action_result`, and never report the address as removed without it."
)

SERVER_REF_DESCRIPTION: Final = (
    "Which WHM server: its id, its name in NOA, or its hostname. Call `whm_list_servers` first "
    "if the operator has not named one."
)

logger = structlog.get_logger(__name__)


# --- The tool: it opens a question and changes nothing ---


@sanitize_tool_errors(TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE)
async def whm_firewall_allowlist_remove(
    *,
    server_ref: str,
    target: str,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one IPv4 address to leave the allow lists; change nothing.

    Two guards run before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `target` is refused — the schema cannot express it,
      because `min_length` counts whitespace;
    - anything that is not a single IPv4 address is refused. This is the CHANGE side of the
      rule T24 reads the other way: reporting what CSF says about an IPv6 address is useful, and
      writing a rule for one is not something these backends are being asked to do here.

    Then one database session — resolve the operator's word to a server (V18: a tie is `choices`,
    never a pick) and turn that row into a connection — and the session closes before the SSH
    hops, which is T21's rule.

    `resolve_whm_ssh_config` is allowed to raise: its three refusals are `NoaError`s, so
    `sanitize_tool_errors` hands the model the code that names the fix.

    What comes back from the firewall decides between two answers. An address that **no answering
    backend holds an allow entry for** is already in the state this change would produce, so it
    is answered rather than gated — and only when every usable backend answered, because
    otherwise "there is nothing to remove" is a claim about a list nobody read. Otherwise
    the reading goes onto the row verbatim as the before-state and the question opens.

    The no-op answer lands in a transcript and is built from the server name, the address
    and one measured boolean — never from the evidence lines, which carry the marker and reason
    T25 wrote onto the very entry this call is about.

    No `reason` parameter and nowhere to add one — the word is typed by an operator on the card,
    after this result has been rendered and forgotten, and the gate refuses a
    reason-shaped argument even for a caller that reaches this function directly.
    """
    normalized_target = target.strip()
    if not normalized_target:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    if parse_csf_target(normalized_target).kind != "ip":
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_CHANGE_TARGET_NOT_IPV4)

    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        resolution = await resolve_whm_server_ref(server_ref, repository=repository)
        if not resolution.ok or resolution.server is None:
            return tool_failure(
                resolution.error_code or ERROR_UNKNOWN,
                resolution.message,
                choices=resolution.choices,
            )
        server_id = str(resolution.server.id)
        server_name = resolution.server.name
        config = resolve_whm_ssh_config(
            resolution.server,
            cipher=context.secret_cipher,
            require_host_key_fingerprint=True,
        )

    availability = await check_firewall_binaries(config)
    lookups = await gather_firewall_entries(
        config, target=normalized_target, availability=availability
    )

    unanswered = unanswered_backends(lookups)
    if not unanswered and not holds_allow_entry(lookups):
        logger.info(
            LOG_REMOVE_NO_OP,
            tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
            server_id=server_id,
            target=normalized_target,
        )
        return tool_ok(
            status=STATUS_NO_OP,
            server=server_name,
            target=normalized_target,
            allowlisted=False,
            message=(
                f"`{normalized_target}` is not on an allow list on {server_name}; "
                "nothing to approve."
            ),
        )

    opened = await open_change_request(
        tool_name=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        arguments={"server_ref": server_ref, "target": normalized_target},
        evidence={
            EVIDENCE_SERVER_ID: server_id,
            EVIDENCE_SERVER_NAME: server_name,
            EVIDENCE_TARGET: normalized_target,
            EVIDENCE_FIREWALL: firewall_state(lookups, availability=availability),
        },
        context=context,
    )
    return build_change_gate_response(
        opened, tool_name=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE, context=context
    )


# --- The runner: reachable only after an operator approved (V22's far side) ---


def build_whm_firewall_allowlist_remove_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that removes the allow entries, once an operator approved.

    A closure over the tool context rather than a class, for T22's reason: what it needs is the
    same session factory, cipher and repositories the tool used, so the change goes through the
    production decrypt site rather than a second one.

    `request.reason` is on the request — the executor reads it off the row for every approved
    change — and this runner never touches it. There is no comment field on a removal to
    put it in, so nothing new leaves NOA here; what still applies is the bound on the way back,
    because the entry being deleted carries the reason T25 wrote (V96, see the module docstring).
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Take this approved request's address off the allow lists, and say what happened.

        Resolve from the evidence, probe availability, change, re-read. Each step can refuse, and
        every refusal answers the ordinary tool envelope rather than raising, because the
        executor's own catch records something coarser than what this knew.

        The resolution refusal carries **no delta**: nothing was driven and nothing was read, so
        there is nothing to state. Every answer from `_removal_outcome` carries one.
        """
        target = await resolve_firewall_change_target(request.evidence, context=context)
        if not isinstance(target, FirewallChangeTarget):
            return ChangeOutcome(payload=target)

        availability = await check_firewall_binaries(target.config)
        changes = await run_on_usable_backends(
            availability,
            csf=lambda: _csf_allowlist_remove(target.config, target=target.target),
            imunify=lambda: _imunify_allowlist_remove(target.config, target=target.target),
        )
        lookups = await gather_firewall_entries(
            target.config, target=target.target, availability=availability
        )
        return _removal_outcome(target, request=request, changes=changes, lookups=lookups)

    return run


def build_whm_firewall_allowlist_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tool."""
    return {
        TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE: build_whm_firewall_allowlist_remove_runner(
            context=context
        ),
    }


def register_whm_firewall_allowlist_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the WHM firewall allowlist CHANGE tool; return its name and risk (I.mcp, V20).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call — it opens an approval request and executes nothing, and V46's row belongs to the
    executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for the name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        description=DESCRIPTION_WHM_FIREWALL_ALLOWLIST_REMOVE,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split
        # that matters is the approval gate; the classification that matters is the risk
        # returned below. `destructiveHint` is True and T25's is False, which is the pair read
        # correctly: releasing an address restores its access, and this takes it away again.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def whm_firewall_allowlist_remove_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
        target: Annotated[
            str,
            Field(
                description=(
                    "The single IPv4 address to take off the allow lists, exactly as "
                    "`whm_preflight_firewall_entries` reports it. Networks, IPv6 addresses and "
                    "hostnames are not accepted here."
                )
            ),
        ],
    ) -> ToolAnswer:
        return await whm_firewall_allowlist_remove(
            server_ref=server_ref, target=target, context=context
        )

    return {TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE: ToolRisk.CHANGE}


# --- Internals ---


async def _csf_allowlist_remove(config: SSHConnectionConfig, *, target: str) -> BackendChange:
    """Drop the temporary allow, then the permanent one. Internal — ⊥ an MCP tool.

    `noa-old`'s two commands in its order: `-tra` removes a temporary allow, `-ar` the
    `csf.allow` entry. Both are sent because an address is held by one or the other and NOA does
    not know which — T25 writes temporary entries, an operator's own hand-added ones are
    permanent — and the one that finds nothing exits non-zero, which is the ordinary case rather
    than a failure.

    `run_csf_command` still raises for an SSH-level failure, and `tolerated_csf_step` re-raises a
    sudo-rights refusal: sudoers can allow the probe and refuse the write, and that is not
    an entry being absent.
    """
    try:
        await _tolerated_csf(config, args=["-tra", target], target=target)
        await _tolerated_csf(config, args=["-ar", target], target=target)
    except NoaError as exc:
        return backend_change_failure(exc.error_code, exc.message)
    return BackendChange(ok=True)


async def _tolerated_csf(config: SSHConnectionConfig, *, args: list[str], target: str) -> None:
    """`tolerated_csf_step` with this tool's name and log event bound to it."""
    await tolerated_csf_step(
        config,
        args=args,
        target=target,
        tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        event=LOG_REMOVE_STEP_TOLERATED,
    )


async def _imunify_allowlist_remove(config: SSHConnectionConfig, *, target: str) -> BackendChange:
    """Delete the whitelist entry. Internal — ⊥ an MCP tool.

    `noa-old`'s call and its argument order, unchanged. `--purpose white` is the whole
    difference from the delete T25 sends, which targets `drop`: this removes the allow, that one
    removes a block. Tolerated for the reason csf's steps are — Imunify refuses a delete for an
    entry it does not hold, and an address allowed only by CSF is the common shape of this call.
    """
    try:
        await tolerated_imunify_step(
            config,
            args=["ip-list", "local", "delete", "--purpose", "white", target, "--json"],
            target=target,
            tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
            event=LOG_REMOVE_STEP_TOLERATED,
            step="delete",
        )
    except NoaError as exc:
        return backend_change_failure(exc.error_code, exc.message)
    return BackendChange(ok=True)


def _removal_delta(
    target: FirewallChangeTarget,
    *,
    request: ChangeExecutionRequest,
    changes: Mapping[str, BackendChange],
    lookups: Mapping[str, BackendLookup],
    verification: str,
    verification_cause: str | None = None,
    list_delta: ListDelta | None = None,
) -> ChangeDelta:
    """This removal's before→after, as the runner that ran it states it.

    **The facet is `list_delta` and not a field change, and V97 is the reason.** Both backends
    resolve a conflict block-first, so an address on a deny list *and* an allow list reads
    `blocked` before the removal and `blocked` after it — a delta computed from the combined
    verdict would render "nothing changed" for a removal that worked, on exactly the address
    that most plainly had an entry to delete. What this change touches is list membership, so
    that is what the delta states, off `holds_allow_entry` — the fact the parsers carry beside
    the verdict for this tool's sake.

    `list_delta` is `None` on every branch except a clean removal, and the omission is a claim:
    `holds_allow_entry` is an aggregate over the backends that answered, so where it still reads
    true NOA knows an entry survived somewhere and does *not* know whether another backend's
    entry went. The per-backend rows carry what was measured; a `removed: []` beside them would
    read as "nothing left any list", which is more than the aggregate said.
    """
    return ChangeDelta(
        identity={"server": target.server_name, "target": target.target},
        verification=verification,
        verification_cause=verification_cause,
        list_delta=list_delta,
        backends=backend_outcomes(changes, lookups),
        unanswered=tuple(unanswered_backends(lookups)),
        bound=evidence_bound(request.evidence),
    )


def _removal_outcome(
    target: FirewallChangeTarget,
    *,
    request: ChangeExecutionRequest,
    changes: Mapping[str, BackendChange],
    lookups: Mapping[str, BackendLookup],
) -> ChangeOutcome:
    """What the change did, read off a fresh dual-backend read.

    Four answers, in the order they are decided:

    1. **a backend could not be driven** — its own code, because that is the remedy. Reported
       first because it is a fact about the commands rather than an inference from the state.
    2. **a usable backend did not answer the confirming read** — the change happened and is
       *unverified*, with the silent backend named. Reporting it as done would be the
       fabrication T24 was written to stop; reporting it as failed would send an operator to
       repeat a removal that may already have taken (V62's rule).
    3. **an allow entry is still there** — the removal did not take.
    4. **no allow entry anywhere** — it did.

    `removed` appears only where the postflight answered, because an absent field is more honest
    than a `false` nobody measured — the same shape T25's `released` / `allowlisted` take.

    Read from `holds_allow_entry` rather than from the combined verdict, and that is the
    correctness point of this function: the verdict resolves block-over-allow, so on an address
    csf also denies it would report a surviving allow entry as removed.

    Nothing from `lookups` reaches the payload beyond those two facts. The lines behind them
    carry the marker and reason T25 wrote onto this very entry, and `result_summary` is derived
    from what is returned here (V96b).
    """
    common: ToolPayload = {
        "server": target.server_name,
        "target": target.target,
        "backends": {name: change.as_payload() for name, change in changes.items()},
    }
    unanswered = unanswered_backends(lookups)

    def delta(
        *,
        verification: str,
        verification_cause: str | None = None,
        list_delta: ListDelta | None = None,
    ) -> ChangeDelta:
        """`_removal_delta` with this branch's four shared arguments already bound."""
        return _removal_delta(
            target,
            request=request,
            changes=changes,
            lookups=lookups,
            verification=verification,
            verification_cause=verification_cause,
            list_delta=list_delta,
        )

    broken = next((changes[name] for name in sorted(changes) if not changes[name].ok), None)
    if broken is not None:
        return ChangeOutcome(
            payload={
                **tool_failure(broken.error_code or ERROR_UNKNOWN, broken.message or ""),
                **common,
                "unanswered_backends": unanswered,
            },
            delta=delta(
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=broken.error_code or ERROR_UNKNOWN,
            ),
        )

    if unanswered:
        logger.warning(
            LOG_REMOVE_UNVERIFIED,
            tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
            action_request_id=str(request.action_request_id),
            target=target.target,
            unanswered_backends=unanswered,
        )
        return ChangeOutcome(
            payload=tool_ok(
                **common,
                status=STATUS_CHANGED,
                verified=False,
                verification=VERIFICATION_UNAVAILABLE,
                unanswered_backends=unanswered,
                message=(
                    f"`{target.target}` was removed from the allow lists on "
                    f"{target.server_name}, but {' and '.join(unanswered)} did not answer the "
                    "confirming read. Check the server's firewall directly."
                ),
            ),
            # Named on `unanswered`, with no cause beside it: which source said nothing *is* the
            # cause here.
            delta=delta(verification=VERIFICATION_UNAVAILABLE),
        )

    if holds_allow_entry(lookups):
        return ChangeOutcome(
            payload={
                **tool_failure(ERROR_ALLOWLIST_REMOVE_FAILED, MESSAGE_ALLOWLIST_REMOVE_FAILED),
                **common,
                "removed": False,
            },
            # Measured and disagreeing, which is what earns the `false` beside it — and no list
            # move is stated, for the reason `_removal_delta` gives.
            delta=delta(verification=VERIFICATION_MISMATCH),
        )

    return ChangeOutcome(
        payload=tool_ok(
            **common,
            status=STATUS_CHANGED,
            removed=True,
            verified=True,
            unanswered_backends=unanswered,
            message=(f"`{target.target}` is no longer on an allow list on {target.server_name}."),
        ),
        delta=delta(
            verification=VERIFICATION_VERIFIED,
            list_delta=ListDelta(removed=(target.target,)),
        ),
    )


__all__ = [
    "DESCRIPTION_WHM_FIREWALL_ALLOWLIST_REMOVE",
    "ERROR_ALLOWLIST_REMOVE_FAILED",
    "EVIDENCE_FIREWALL",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_TARGET",
    "LOG_REMOVE_NO_OP",
    "LOG_REMOVE_STEP_TOLERATED",
    "LOG_REMOVE_UNVERIFIED",
    "MESSAGE_ALLOWLIST_REMOVE_FAILED",
    "MESSAGE_CHANGE_TARGET_NOT_IPV4",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE",
    "VERIFICATION_UNAVAILABLE",
    "build_whm_firewall_allowlist_remove_runner",
    "build_whm_firewall_allowlist_runners",
    "register_whm_firewall_allowlist_tools",
    "whm_firewall_allowlist_remove",
]
