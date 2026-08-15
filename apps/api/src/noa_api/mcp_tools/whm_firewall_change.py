"""`whm_firewall_release_and_allow` — the WHM firewall release CHANGE tool (T25).

**Two steps of the operator's real job, merged into one approval** (DECISIONS §6.5,
owner-confirmed 2026-08-04). The working pattern is *check whether an IP is denied → release it →
allowlist it*, and steps 2 and 3 are almost always run together, so one card matches the real
unit of work instead of asking twice for one decision. The check in front of it stays its own
exposed tool (T24), because that verdict is what an operator reads before deciding to call this
at all. The undo path is a third module and a third name (T26, DECISIONS §6.5: "keep
`whm_firewall_allowlist_remove` separate — it is the undo path, run on its own").

Beside `whm_firewall.py` rather than inside it. A CHANGE tool is two halves on opposite sides of
V22's boundary — a tool the LLM can reach that changes nothing, and a runner reachable only from
`core.approvals.execution` — and both of those plus T26 would push one module past C14's 900-line
budget. What is shared is code, not a file: `gather_firewall_entries` is the before-state (V66),
and `noa_firewall_comment` / `without_noa_comment_text` are the two ends of V96's bound, which
belong beside the READ surface that has to honour them.

**What T26 became the second caller of moved to `whm_firewall_change_common.py`** — the
before-state shape, `BackendChange`, the tolerated release commands, the evidence keys, and the
resolution of a machine and address out of an approved row. Hoisted rather than imported from
here, so the two tool modules are siblings instead of one depending on the other; re-exported
below, so nothing that already named them here moved (the same shape `change_target` was hoisted
in at T25).

**The preflight is T24's, run in-process** (C9, V17). Not T24's *tool* — its internals: the same
availability probe, the same dual-backend lookup, the same verdict combination. That evidence is
born inside this call, lives milliseconds, belongs to the same user, and reaches the operator
through `approval_context` rather than through a transcript. It is also literally the before-state
DECISIONS §6.5 asks the receipt for: why the address was blocked, and the `csf.deny` /
Imunify lines it was read from.

**There is no no-op branch, and that is a decision rather than an omission.** T22 and T23 answer
instead of gating when the account is already in the state the change would produce. Nothing here
is ever in that state: the allow entry carries a TTL, so a repeat always moves the expiry, and an
address that is already allowlisted is exactly the case an operator re-runs this for when the old
window is about to close. An address that no backend has ever heard of is not a no-op either —
the release finds nothing and the allow is still the change that was asked for.

**One approval, two outcomes, never collapsed** (DECISIONS §6.5). The runner answers with
`released` and `allowlisted` as separate booleans and fails with separate codes
(`firewall_release_failed` / `firewall_allow_failed`), because "we let it through the deny list"
and "we put it on the allow list" are two claims and a single "done" hides which one is false.

**Release before allow, and the order is load-bearing.** CSF resolves a conflict block-first — an
address in both `csf.deny` and `csf.allow` is still blocked — so an allow entry written before the
deny entry is removed buys nothing and reads as success (`core.integrations.whm.csf`, which is
where the same precedence is parsed).

**The release commands tolerate a non-zero exit; the allow command does not.** `csf -dr` on an
address that is not in `csf.deny` exits non-zero, and so does an Imunify delete for an entry that
was never there — which is the *common* case, since most releases target an address held by one
backend or by a temporary ban rather than by every list this touches. Treating that as a failure
would refuse the ordinary release. The allow is the entry the operator asked to exist, so its
failure is the backend's failure and keeps the backend's own code. Neither is the authority on
whether the change took: the postflight is, and it is a fresh read through the same door.

**Every backend operation goes through `run_on_usable_backends`** (V57, T68) — the change and the
postflight both. Zero usable backends is that door's refusal, not a check written here: a
per-tool copy is a copy the next tool forgets, and V57's harm lives exactly here, on an approved
CHANGE that reports done and did nothing.

**A postflight that did not answer is not a verified change** (V86, and V62's rule one system
over). If any usable backend stays silent on the confirming read, the change is reported as
having happened and *not* verified, with the silent backend named. A combined verdict built from
whichever half answered is the fabrication V86 was written at T24 to stop, and on a CHANGE it is
worse: "released and allowed" on a box whose blocking backend was never re-read.

**The operator's reason is written onto the entry, and cut back out of the READ** (C8, V43, V96).
csf's allow entry takes a comment and Imunify's takes `--comment`, and the only honest content
for either is why the address was allowed — V43 permits the one field to leave NOA for exactly
this. What V96 requires is that it cannot come back, and the path back is T24, which reads csf's
own lines into a transcript. So the comment is written behind NOA's marker
(`noa:<action_request_id> <reason>`) and T24 cuts from that marker to the end of the line. Two
consequences worth stating: the runner's own payload never repeats the reason, because
`result_summary` is derived from it and `noa_get_action_result` hands that to a model (V96b); and
a backend's failure message is cut too, because it can quote the command it failed to run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

import structlog
from fastmcp import FastMCP
from pydantic import Field

from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.db.lifecycle import ToolRisk
from core.errors import NoaError
from core.integrations.whm.availability import check_firewall_binaries
from core.integrations.whm.csf import parse_csf_target
from core.integrations.whm.csf_cli import require_csf_success, run_csf_command
from core.integrations.whm.firewall_gate import run_on_usable_backends
from core.integrations.whm.imunify_cli import parse_imunify_json_output, run_imunify_command
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
    VERDICT_ALLOWLISTED,
    VERDICT_BLOCKED,
    BackendLookup,
    combine_firewall_verdict,
    gather_firewall_entries,
    noa_firewall_comment,
)
from noa_api.mcp_tools.whm_firewall_change_common import (
    # Hoisted to `whm_firewall_change_common` at T26, when the allowlist removal became the
    # second caller of the same before-state, the same backend-answer shape and the same
    # evidence resolution (V66). Re-exported below, so every name this module already published
    # keeps working from here.
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_SERVER_UNAVAILABLE,
    EVIDENCE_FIREWALL,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    MESSAGE_EVIDENCE_UNUSABLE,
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    BackendChange,
    FirewallChangeTarget,
    backend_change_failure,
    firewall_state,
    resolve_firewall_change_target,
    tolerated_csf_step,
    tolerated_imunify_step,
    unanswered_backends,
)

TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW = "whm_firewall_release_and_allow"

# V77's bound, verbatim: one minute to one year, and no server-side default. An operator states
# the duration in chat and the model converts it to minutes as an ordinary tool argument — a
# duration is not a reason, so C8 is untouched by it carrying one.
MIN_DURATION_MINUTES: Final = 1
MAX_DURATION_MINUTES: Final = 525_600

# The one evidence key this tool owns alone. The other four are the firewall pair's shared shape
# (`whm_firewall_change_common`) — still deliberately not the account tools', which happen to
# spell two of them the same way: a constant shared across two different contracts is how a key
# ends up meaning two things.
EVIDENCE_DURATION_MINUTES = "duration_minutes"

ERROR_DURATION_INVALID = "duration_invalid"
# The postflight says the address is still blocked: the deny entry outlived the release.
ERROR_RELEASE_FAILED = "firewall_release_failed"
# It is no longer blocked and it is not allowed either: the release took and the allow did not.
ERROR_ALLOW_FAILED = "firewall_allow_failed"

MESSAGE_DURATION_INVALID = (
    f"`duration_minutes` must be a whole number of minutes between {MIN_DURATION_MINUTES} and "
    f"{MAX_DURATION_MINUTES} (one year). Ask the operator how long the address should stay "
    "allowed; there is no default."
)
MESSAGE_CHANGE_TARGET_NOT_IPV4 = (
    "Only a single IPv4 address can be released and allowed. Networks, IPv6 addresses and "
    "hostnames can be checked with `whm_preflight_firewall_entries` but not changed."
)
MESSAGE_RELEASE_FAILED = (
    "The address is still blocked after the release ran. Check the server's firewall directly "
    "before asking again."
)
MESSAGE_ALLOW_FAILED = (
    "The address was released from the deny lists but is not on an allow list. Check the "
    "server's firewall directly before asking again."
)

# One structured event per tolerated release failure, so "the deny entry was already gone" and
# "the release command could not run" are still distinguishable after the fact. Identifiers and
# codes only, never the comment (V8, V96).
LOG_RELEASE_STEP_TOLERATED: Final = "whm_firewall_release_step_tolerated"

# The change ran and the confirming read could not answer. Warning, because an operator may want
# to look at the box.
LOG_RELEASE_UNVERIFIED: Final = "whm_firewall_release_and_allow_unverified"

DESCRIPTION_WHM_FIREWALL_RELEASE_AND_ALLOW = (
    "Release one IPv4 address from a WHM server's firewall deny lists and put it on the allow "
    "list for a stated length of time, in CSF and Imunify360 together. This changes a live "
    "system, so it does not run when you call it: NOA reads the current firewall state, opens an "
    "approval request, and answers with the address of a card where an operator decides. Call "
    "`whm_preflight_firewall_entries` first and report what it found. `duration_minutes` is "
    "required and has no default — ask the operator how long the address should stay allowed and "
    'convert their answer to whole minutes ("5 min" is 5, "2 hours" is 120, "5 days" is '
    "7200). Read the outcome with `noa_get_action_result`, and never report the address as "
    "released without it."
)

SERVER_REF_DESCRIPTION: Final = (
    "Which WHM server: its id, its name in NOA, or its hostname. Call `whm_list_servers` first "
    "if the operator has not named one."
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _ReleaseTarget:
    """The machine, address and window an approved release runs against, from the evidence.

    `FirewallChangeTarget` plus the window, which is this tool's alone: the removal one module
    over resolves the same machine and address and has no duration to carry.
    """

    config: SSHConnectionConfig
    server_name: str
    target: str
    duration_minutes: int


# --- The tool: it opens a question and changes nothing (V16, V22, V23) ---


@sanitize_tool_errors(TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW)
async def whm_firewall_release_and_allow(
    *,
    server_ref: str,
    target: str,
    duration_minutes: int,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one IPv4 address to be released and allowed; change nothing (T25 — V16, V17, V23).

    Three guards run before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `target` is refused (V21) — the schema cannot express it,
      because `min_length` counts whitespace;
    - anything that is not a single IPv4 address is refused (V54). This is the CHANGE side of the
      rule T24 reads the other way: reporting what CSF says about an IPv6 address is useful, and
      writing a rule for one is not something these backends are being asked to do here;
    - `duration_minutes` outside 1-525600 is refused (V77). The schema declares the same bound,
      and this is the same bound where a caller reaching the function directly meets it.

    Then one database session — resolve the operator's word to a server (V18: a tie is `choices`,
    never a pick) and turn that row into a connection — and the session closes before the SSH
    hops, which is T21's rule and matters here because four handshakes follow.

    `resolve_whm_ssh_config` is allowed to raise: its three refusals are `NoaError`s, so
    `sanitize_tool_errors` hands the model the code that names the fix (V19). Reaching the probe
    with an unpinned row would instead report "no firewall backends", which is a different
    problem and the wrong thing to go fix.

    What comes back from the firewall is the before-state, and it goes on the row verbatim (V33,
    V35): the verdict, the evidence lines it was read from, and the backends that did not answer
    (V86). No `reason` parameter and nowhere to add one — the word is typed by an operator on the
    card, after this result has been rendered and forgotten (C8, V15, V43), and the gate refuses
    a reason-shaped argument even for a caller that reaches this function directly.
    """
    normalized_target = target.strip()
    if not normalized_target:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    if parse_csf_target(normalized_target).kind != "ip":
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_CHANGE_TARGET_NOT_IPV4)

    if not _duration_in_bounds(duration_minutes):
        return tool_failure(ERROR_DURATION_INVALID, MESSAGE_DURATION_INVALID)

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

    opened = await open_change_request(
        tool_name=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        arguments={
            "server_ref": server_ref,
            "target": normalized_target,
            "duration_minutes": duration_minutes,
        },
        evidence={
            EVIDENCE_SERVER_ID: server_id,
            EVIDENCE_SERVER_NAME: server_name,
            EVIDENCE_TARGET: normalized_target,
            EVIDENCE_DURATION_MINUTES: duration_minutes,
            EVIDENCE_FIREWALL: firewall_state(lookups, availability=availability),
        },
        context=context,
    )
    return build_change_gate_response(
        opened, tool_name=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW, context=context
    )


# --- The runner: reachable only after an operator approved (V22's far side) ---


def build_whm_firewall_release_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that releases and allows, once an operator approved (T25, T38 — V22, V46).

    A closure over the tool context rather than a class, for T22's reason: what it needs is the
    same session factory, cipher and repositories the tool used, so the change goes through the
    production decrypt site rather than a second one.
    """

    async def run(request: ChangeExecutionRequest) -> ToolPayload:
        """Release the address this approved request names, allow it, and say what happened.

        The order is: resolve from the evidence, probe availability, change, re-read. Each step
        can refuse, and every refusal answers the ordinary tool envelope rather than raising,
        because the executor's own catch records something coarser than what this knew (V19).

        The one instant everything is computed from is taken once, here: csf takes a TTL in
        seconds and Imunify takes an absolute epoch, and deriving them separately is how the two
        backends end up disagreeing about when the entry expires by however long the first hop
        took.
        """
        target = await _resolve_release_target(request, context=context)
        if not isinstance(target, _ReleaseTarget):
            return target

        expires_at = datetime.now(UTC) + timedelta(minutes=target.duration_minutes)
        comment = noa_firewall_comment(request.action_request_id, reason=request.reason)

        availability = await check_firewall_binaries(target.config)
        changes = await run_on_usable_backends(
            availability,
            csf=lambda: _csf_release_and_allow(
                target.config,
                target=target.target,
                duration_seconds=target.duration_minutes * 60,
                comment=comment,
            ),
            imunify=lambda: _imunify_release_and_allow(
                target.config,
                target=target.target,
                expiration_epoch=int(expires_at.timestamp()),
                comment=comment,
            ),
        )
        lookups = await gather_firewall_entries(
            target.config, target=target.target, availability=availability
        )
        return _release_outcome(
            target,
            request=request,
            expires_at=expires_at,
            changes=changes,
            lookups=lookups,
        )

    return run


def build_whm_firewall_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tool (T25).

    One entry, and it stays one: T26's removal contributes its own map from
    `whm_firewall_allowlist`, the way each system's registrar does. Collecting it here instead
    would make this module import the one that imports its shared machinery.
    """
    return {
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW: build_whm_firewall_release_runner(context=context),
    }


def register_whm_firewall_change_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the WHM firewall release tool; return its name with its risk (I.mcp, V20).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call (T73) — it opens an approval request and executes nothing, and V46's row belongs to the
    executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for the name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        description=DESCRIPTION_WHM_FIREWALL_RELEASE_AND_ALLOW,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split
        # that matters is the approval gate (V16); the classification that matters is the risk
        # returned below. `destructiveHint` is False because this restores access rather than
        # removing it, the same reading that keeps T23 a separate name from T22.
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    async def whm_firewall_release_and_allow_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
        target: Annotated[
            str,
            Field(
                description=(
                    "The single IPv4 address to release and allow, exactly as "
                    "`whm_preflight_firewall_entries` reports it. Networks, IPv6 addresses and "
                    "hostnames are not accepted here."
                )
            ),
        ],
        duration_minutes: Annotated[
            int,
            Field(
                ge=MIN_DURATION_MINUTES,
                le=MAX_DURATION_MINUTES,
                description=(
                    "How long the address stays on the allow list, in whole minutes. Required, "
                    'with no default: ask the operator and convert their answer ("5 min" is 5, '
                    '"2 hours" is 120, "5 days" is 7200). Maximum 525600, one year.'
                ),
            ),
        ],
    ) -> ToolAnswer:
        return await whm_firewall_release_and_allow(
            server_ref=server_ref,
            target=target,
            duration_minutes=duration_minutes,
            context=context,
        )

    return {TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW: ToolRisk.CHANGE}


# --- Internals ---


def _duration_in_bounds(duration_minutes: object) -> bool:
    """V77's bound, and a type check with it.

    `bool` is excluded explicitly: it is an `int` in Python, and `True` would otherwise pass as
    one minute. The evidence round-trips through JSONB, so the runner asks this question of a
    value that has been through JSON and back.
    """
    if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int):
        return False
    return MIN_DURATION_MINUTES <= duration_minutes <= MAX_DURATION_MINUTES


async def _csf_release_and_allow(
    config: SSHConnectionConfig, *, target: str, duration_seconds: int, comment: str
) -> BackendChange:
    """Release from CSF's deny lists, then allow with a TTL. Internal — ⊥ an MCP tool (C9, V17).

    Four commands in `noa-old`'s order (C13, V69): `-tr` drops a temporary block, `-dr` drops the
    permanent `csf.deny` entry, and `-ta` writes the temporary allow with its own TTL and comment.

    The two removals are tolerated when they exit non-zero, because "not in that list" is how csf
    reports the ordinary case — an address held by a temporary ban has no `csf.deny` line, and one
    that was never blocked has neither. The allow is required to succeed: it is the entry the
    operator asked to exist. `run_csf_command` still raises for an SSH-level failure on any of
    them, and that code is what comes back.
    """
    try:
        await _tolerated_csf(config, args=["-tr", target], target=target)
        await _tolerated_csf(config, args=["-dr", target], target=target)
        allow = await run_csf_command(config, args=["-ta", target, str(duration_seconds), comment])
        require_csf_success(allow, default_message="CSF did not add the temporary allow entry.")
    except NoaError as exc:
        return backend_change_failure(exc.error_code, exc.message)
    return BackendChange(ok=True)


async def _tolerated_csf(config: SSHConnectionConfig, *, args: list[str], target: str) -> None:
    """`tolerated_csf_step` with this tool's name and log event bound to it."""
    await tolerated_csf_step(
        config,
        args=args,
        target=target,
        tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        event=LOG_RELEASE_STEP_TOLERATED,
    )


async def _imunify_release_and_allow(
    config: SSHConnectionConfig, *, target: str, expiration_epoch: int, comment: str
) -> BackendChange:
    """Drop the blacklist entry, then whitelist with an expiry. Internal — ⊥ an MCP tool.

    `noa-old`'s two calls and its argument order, unchanged (C13, V69). The delete is tolerated
    for csf's reason one backend over: Imunify refuses a delete for an entry it does not hold, and
    an address blocked only by CSF is the common shape of this call. The add is required.
    """
    try:
        await tolerated_imunify_step(
            config,
            args=["ip-list", "local", "delete", "--purpose", "drop", target, "--json"],
            target=target,
            tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
            event=LOG_RELEASE_STEP_TOLERATED,
            step="delete",
        )
        added = await run_imunify_command(
            config,
            args=[
                "ip-list",
                "local",
                "add",
                "--purpose",
                "white",
                target,
                "--comment",
                comment,
                "--expiration",
                str(expiration_epoch),
                "--json",
            ],
        )
        parse_imunify_json_output(added)
    except NoaError as exc:
        return backend_change_failure(exc.error_code, exc.message)
    return BackendChange(ok=True)


async def _resolve_release_target(
    request: ChangeExecutionRequest, *, context: McpToolContext
) -> _ReleaseTarget | ToolPayload:
    """The connection, address and window an approved release runs against (V33).

    **From the evidence, never from the arguments.** `server_ref` and `target` are strings a model
    supplied, inventory can be edited between a request and its approval, and the evidence is the
    state the operator actually saw on the card. Re-resolving here would be a second resolution
    that can disagree with the one the decision rests on.

    Three refusals, all before anything is changed and each naming what an administrator should
    do: the address or the duration did not survive their JSONB round trip as usable values, the
    evidence carries no usable server id, or the server row is gone. The first two of those are
    the firewall pair's shared resolution (`resolve_firewall_change_target`); the duration is this
    tool's own and is checked **first**, against V77's bound rather than merely against its type,
    because a value outside it would write an allow entry nobody approved the length of — and
    because refusing it before the database round trip keeps a malformed request off the pool.

    The database session closes before the SSH hops, T21's rule, and here it matters twice over:
    the executor's own session is open for the whole of the call.
    """
    duration_minutes = request.evidence.get(EVIDENCE_DURATION_MINUTES)
    if not _duration_in_bounds(duration_minutes):
        return tool_failure(ERROR_EVIDENCE_UNUSABLE, MESSAGE_EVIDENCE_UNUSABLE)

    resolved = await resolve_firewall_change_target(request.evidence, context=context)
    if not isinstance(resolved, FirewallChangeTarget):
        return resolved

    return _ReleaseTarget(
        config=resolved.config,
        server_name=resolved.server_name,
        target=resolved.target,
        duration_minutes=int(duration_minutes),  # type: ignore[arg-type]
    )


def _release_outcome(
    target: _ReleaseTarget,
    *,
    request: ChangeExecutionRequest,
    expires_at: datetime,
    changes: Mapping[str, BackendChange],
    lookups: Mapping[str, BackendLookup],
) -> ToolPayload:
    """What the change did, read off a fresh dual-backend read (DECISIONS §6.5, V62, V86).

    Five answers, in the order they are decided, and the order is the argument:

    1. **a backend could not be driven** — its own code, because that is the remedy. Reported
       first because it is a fact about the commands rather than an inference from the state.
    2. **a usable backend did not answer the confirming read** — the change happened and is
       *unverified*, with the silent backend named (V86). Reporting it as done would be the
       fabrication T24 was written to stop, one side worse; reporting it as failed would send an
       operator to repeat a release that may already have taken (V62's rule).
    3. **still blocked** — the deny entry outlived the release, so the release failed.
    4. **neither blocked nor allowed** — the release took and the allow did not.
    5. **allowed** — both halves took, and both say so separately.

    `released` and `allowlisted` appear only where the postflight answered. Claiming either from
    a read that did not happen is exactly what the shape exists to prevent, and an absent field
    is more honest than a `false` nobody measured.
    """
    common: ToolPayload = {
        "server": target.server_name,
        "target": target.target,
        "duration_minutes": target.duration_minutes,
        # V77: the after-state shows the resolved expiry, not the window that was asked for.
        "expires_at": expires_at.isoformat(),
        "backends": {name: change.as_payload() for name, change in changes.items()},
    }
    unanswered = unanswered_backends(lookups)

    broken = next((changes[name] for name in sorted(changes) if not changes[name].ok), None)
    if broken is not None:
        return {
            **tool_failure(broken.error_code or ERROR_UNKNOWN, broken.message or ""),
            **common,
            "unanswered_backends": unanswered,
        }

    if unanswered:
        logger.warning(
            LOG_RELEASE_UNVERIFIED,
            tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
            action_request_id=str(request.action_request_id),
            target=target.target,
            unanswered_backends=unanswered,
        )
        return tool_ok(
            **common,
            status=STATUS_CHANGED,
            verified=False,
            verification=VERIFICATION_UNAVAILABLE,
            unanswered_backends=unanswered,
            message=(
                f"`{target.target}` was released and allowed on {target.server_name}, but "
                f"{' and '.join(unanswered)} did not answer the confirming read. Check the "
                "server's firewall directly."
            ),
        )

    verdict = combine_firewall_verdict(list(lookups.values()))
    if verdict == VERDICT_BLOCKED:
        return {
            **tool_failure(ERROR_RELEASE_FAILED, MESSAGE_RELEASE_FAILED),
            **common,
            "released": False,
            "allowlisted": False,
        }
    if verdict != VERDICT_ALLOWLISTED:
        # `not_found`: nothing blocks it and nothing allows it either.
        return {
            **tool_failure(ERROR_ALLOW_FAILED, MESSAGE_ALLOW_FAILED),
            **common,
            "released": True,
            "allowlisted": False,
        }

    return tool_ok(
        **common,
        status=STATUS_CHANGED,
        released=True,
        allowlisted=True,
        verified=True,
        unanswered_backends=unanswered,
        message=(
            f"`{target.target}` is released from the deny lists on {target.server_name} and "
            f"allowed until {expires_at.isoformat()}."
        ),
    )


__all__ = [
    "DESCRIPTION_WHM_FIREWALL_RELEASE_AND_ALLOW",
    "ERROR_ALLOW_FAILED",
    "ERROR_DURATION_INVALID",
    "ERROR_EVIDENCE_UNUSABLE",
    "ERROR_RELEASE_FAILED",
    "ERROR_SERVER_UNAVAILABLE",
    "EVIDENCE_DURATION_MINUTES",
    "EVIDENCE_FIREWALL",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_TARGET",
    "LOG_RELEASE_STEP_TOLERATED",
    "LOG_RELEASE_UNVERIFIED",
    "MAX_DURATION_MINUTES",
    "MESSAGE_ALLOW_FAILED",
    "MESSAGE_CHANGE_TARGET_NOT_IPV4",
    "MESSAGE_DURATION_INVALID",
    "MESSAGE_RELEASE_FAILED",
    "MIN_DURATION_MINUTES",
    "STATUS_CHANGED",
    "TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW",
    "VERIFICATION_UNAVAILABLE",
    "BackendChange",
    "build_whm_firewall_change_runners",
    "build_whm_firewall_release_runner",
    "firewall_state",
    "register_whm_firewall_change_tools",
    "unanswered_backends",
    "whm_firewall_release_and_allow",
]
