"""The half of `proxmox_vm_nic` that flips the link.

Beside `proxmox_nic.py` rather than inside it, for the file-size cap and on the boundary the design
already draws — **nothing in the tool module can change anything, and nothing here is reachable
without an approval**. The password-reset tool made the same split for the same reason. The evidence
keys, the action words and the tool's name come from that module; nothing there imports this one, so
`registry.py` reaches the tool and `change_runners.py` reaches the runner with no cycle between
them.

**The re-read is the design, not an optimisation.** `noa-old` carried the config digest from its
preflight to its change call as a tool argument, seconds apart, and wrote the NIC line it had read
back then. With an approval gate in that window the same arrangement has two failure modes an
operator pays for:

- **a stale digest refuses an approved change.** The digest covers the *whole* VM config, so a
  memory change, a disk resize or an edited description between the card being opened and being
  approved makes Proxmox answer `digest_mismatch` — after an operator has typed a reason and
  pressed Approve.
- **a stale value is a lost update.** Writing back the gate-time `netN` line reverts any bridge,
  tag or model edit made in the meantime, silently, as a side effect of a change that was about
  the link state. NOA would be the author of that.

So this runner re-reads the config, toggles `link_down` on the line **as it is now**, and writes
under the digest from that same read. The compare-and-set window is its own read→write, which is
what a CAS is for. What the approval window is checked against is the fact the operator actually
approved: the NIC's **link state**, compared here against the state on the evidence.

Three ways that comparison can go, and only one of them is a failure:

- the NIC is still in the state the card described → do the change.
- the NIC is **already** in the state that was asked for → `no_op`. Somebody reached it first; the
  world is as the operator wanted it, and calling that a failure would send them to fix something
  that is not broken.
- the NIC is **gone** → `net_not_found`. A line that no longer exists cannot be edited, and
  guessing at a neighbouring one would be a change to an interface nobody approved.

**The postflight asks the change's own question**. It re-reads the config and recomputes the
link state off the `netN` line — not off the task's exit status, which says only that Proxmox
accepted the write. A read that cannot answer is `unavailable`, never `false`: silence is not
evidence of absence, and the verdict rule holds one system over —
**verification-unavailable is not verified, and it is not refuted either.**

**No path back to a model opens here, and that is worth stating rather than assuming.** This runner
never reads `request.reason`: a `netN` line has no note field, NOA writes no `description`, and the
only thing it puts on the wire is the interface line and a digest. So nothing the reason rule keeps
from the LLM is written onto this VM, the one operator-typed field's permission is unused, and
nothing NOA wrote comes back through a later READ — the password-reset and unsuspend tools'
situation, one system over.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import structlog

from core.approvals.delta import (
    # `VERIFICATION_UNAVAILABLE` arrives below through `change_target`, the same string from the
    # same definition — `core.approvals.delta` owns all four states.
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
    FieldChange,
)
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.integrations.proxmox.client import ProxmoxClient
from core.integrations.proxmox.nic import (
    NetworkInterface,
    find_nic,
    list_nics,
    set_link_down,
)
from noa_api.mcp_tools.change_target import (
    ERROR_EVIDENCE_UNUSABLE,
    MESSAGE_EVIDENCE_UNUSABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    WriteFailure,
    confirmed_verification,
    confirmed_verification_sentence,
    uuid_or_none,
    write_failure_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.proxmox_nic import (
    ACTION_DISABLE,
    ACTIONS,
    ERROR_NET_NOT_FOUND,
    EVIDENCE_ACTION,
    EVIDENCE_NET,
    EVIDENCE_NIC,
    EVIDENCE_NODE,
    EVIDENCE_SERVER_ID,
    EVIDENCE_VMID,
    TOOL_PROXMOX_VM_NIC,
    link_state_for,
)
from noa_api.mcp_tools.proxmox_password import (
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    text_or_none,
    upstream_failure,
)
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    tool_failure,
    tool_ok,
)

# The approved NIC is no longer on the VM. Shares the tool's code because it is the same fact and
# the same remedy — name an interface that exists — even though it is discovered on the far side
# of an approval this time. It carries no `choices`: a model cannot fix this by calling again with
# a different NIC, because the change it would be fixing has already been authorised.
MESSAGE_NET_GONE: Final = (
    "The interface this change was approved for is no longer on the VM. Ask for the change again "
    "against an interface it has."
)

# The config write's own task did not reach a terminal state in time. The write was accepted, so
# the link may already have moved — which is why this is not reported as a refusal.
ERROR_TASK_TIMEOUT: Final = "task_timeout"
MESSAGE_TASK_TIMEOUT: Final = (
    "Proxmox accepted the change but its task had not finished when NOA stopped waiting. Check "
    "the VM's network interface before relying on it."
)

# The task reached a terminal state and it was a failure — Proxmox saying it did not apply.
ERROR_TASK_FAILED: Final = "task_failed"

# The write was accepted, the postflight read answered, and the link is not where it was asked to
# be. Distinct from the unavailable case above it: this is a measurement.
ERROR_POSTFLIGHT_FAILED: Final = "postflight_failed"

# One structured event per outcome an operator may have to act on. Identifiers and codes only
# — and never `request.reason`, which this runner does not read at all.
LOG_NIC_RUN_NO_OP: Final = "proxmox_vm_nic_no_op"
LOG_NIC_RUN_UNVERIFIED: Final = "proxmox_vm_nic_unverified"

# `noa-old`'s numbers, kept. A `netN` write finishes in well under a second in practice.
TASK_POLL_ATTEMPTS: Final = 30
TASK_POLL_DELAY_SECONDS: Final = 0.5

logger = structlog.get_logger(__name__)


# --- The runner: reachable only after an operator approved (the far side of the cookie/CSRF
# boundary) ---


@dataclass(frozen=True)
class NICChangeTarget:
    """The endpoint, VM, interface and direction an approved change runs against."""

    client: ProxmoxClient
    server_name: str
    node: str
    vmid: int
    net: str
    action: str

    @property
    def disabled(self) -> bool:
        """Is this change asking for the link to be down?"""
        return self.action == ACTION_DISABLE

    @property
    def desired_link_state(self) -> str:
        """The link state this change is asking for."""
        return link_state_for(self.action)


@dataclass(frozen=True)
class FreshNIC:
    """The interface as it is *now*, with the digest of the read that found it.

    The two travel together because they are only meaningful together: the digest authorises a
    write of exactly the config this line was read from, and pairing a line from one read with a
    digest from another is the blind overwrite the digest exists to prevent.
    """

    nic: NetworkInterface
    digest: str


def build_proxmox_vm_nic_runner(*, context: McpToolContext) -> ChangeRunner:
    """The half that flips the link, once an operator approved.

    A closure over the tool context rather than a class, for the suspend tool's reason: what it
    needs is the same session factory, cipher and repositories the tool used, so the change goes
    through the production decrypt site and the production client rather than second copies of
    either.

    `request.reason` is on the request — the executor reads it off the row for every approved change
    — and this runner never touches it. A `netN` line has no note field, so nothing the reason rule
    keeps from the LLM leaves NOA here, and no path back to a model opens on this tool.
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Flip this approved request's NIC, and say what happened.

        Resolve from the evidence, re-read, decide, write, verify. Every refusal answers the
        ordinary tool envelope rather than raising, because the executor's own catch records
        something coarser than what this knew.

        The resolution refusal carries **no delta** — nothing was read and nothing was written,
        so there is nothing to state. Everything below it carries one, and the **no-op**
        is the case worth naming: its delta reports an explicitly empty `changed_fields`, because
        a before→after taken from its payload would render the identity fields as new values and
        describe a change to an interface nothing touched.
        """
        target = await _resolve_change_target(request.evidence, context=context)
        if not isinstance(target, NICChangeTarget):
            return ChangeOutcome(payload=target)

        async with target.client:
            fresh = await _read_current_nic(target)
            if not isinstance(fresh, FreshNIC):
                return ChangeOutcome(
                    payload=fresh,
                    # Read before any write, so nothing moved and NOA knows it: the config could
                    # not be read, it carried no digest, or the approved interface is gone.
                    delta=_nic_delta(
                        target,
                        verification=VERIFICATION_UNAVAILABLE,
                        verification_cause=str(fresh.get("error_code") or ERROR_UNKNOWN),
                        changed_fields=(),
                    ),
                )

            if fresh.nic.link_state == target.desired_link_state:
                logger.info(
                    LOG_NIC_RUN_NO_OP,
                    tool=TOOL_PROXMOX_VM_NIC,
                    action_request_id=str(request.action_request_id),
                    node=target.node,
                    vmid=target.vmid,
                    net=target.net,
                )
                return ChangeOutcome(
                    payload=tool_ok(
                        **_common(target),
                        status=STATUS_NO_OP,
                        link_state=fresh.nic.link_state,
                        verified=True,
                        message=(
                            f"`{target.net}` on VM {target.vmid} ({target.server_name}) was "
                            f"already {'disabled' if target.disabled else 'enabled'} when NOA "
                            "ran this change, so nothing was written."
                        ),
                    ),
                    delta=_nic_delta(target, verification=VERIFICATION_VERIFIED, changed_fields=()),
                )

            # A failed write is not an answer about the interface yet, so there is no early
            # return here: the postflight runs either way and the failure travels into it.
            failure = await _write_link_state(target, fresh=fresh)
            return await _verify_link_state(target, request=request, write_failure=failure)

    return run


def build_proxmox_nic_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tool."""
    return {TOOL_PROXMOX_VM_NIC: build_proxmox_vm_nic_runner(context=context)}


# --- Internals ---


async def _resolve_change_target(
    evidence: Mapping[str, Any], *, context: McpToolContext
) -> NICChangeTarget | ToolPayload:
    """The endpoint, VM, interface and direction an approved change runs against.

    **From the evidence, never from the arguments.** `server_ref` is a string a model supplied and
    inventory can be edited between a request and its approval; the evidence is the state the
    operator actually saw on the card.

    Every value is re-checked as it comes back out of JSONB, `action` included — that is the third
    place the enum is bounded (published schema, re-checked at every hop): what `tools/list`
    publishes, what the tool body re-checks, and what survived the round trip. A value that no
    longer parses is a request NOA declines rather than guesses at, because by the time a runner
    reads it an operator has typed a reason and pressed Approve.

    The database session closes before the HTTP hops, the account search's rule, and here it matters
    twice over: the executor's own session is open for the whole of the call.
    """
    node = evidence.get(EVIDENCE_NODE)
    net = evidence.get(EVIDENCE_NET)
    vmid = evidence.get(EVIDENCE_VMID)
    action = evidence.get(EVIDENCE_ACTION)
    if (
        not isinstance(node, str)
        or not node.strip()
        or not isinstance(net, str)
        or not net.strip()
        or isinstance(vmid, bool)
        or not isinstance(vmid, int)
        or vmid < 1
        or action not in ACTIONS
    ):
        return tool_failure(ERROR_EVIDENCE_UNUSABLE, MESSAGE_EVIDENCE_UNUSABLE)

    server_id = uuid_or_none(evidence.get(EVIDENCE_SERVER_ID))
    if server_id is None:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    async with context.session_factory() as session:
        repository = context.proxmox_server_repository_factory(session)
        server = await repository.get_by_id(server_id)
        if server is None:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
        server_name = server.name
        client = context.proxmox_client_factory(server, cipher=context.secret_cipher)

    return NICChangeTarget(
        client=client,
        server_name=server_name,
        node=node.strip(),
        vmid=vmid,
        net=net.strip(),
        action=str(action),
    )


async def _read_current_nic(target: NICChangeTarget) -> FreshNIC | ToolPayload:
    """The approved interface as it is now, with this read's digest (see the module docstring)."""
    config_result = await target.client.get_qemu_config(target.node, target.vmid)
    if config_result.get("ok") is not True:
        return upstream_failure(config_result, fallback="Proxmox VM config lookup failed")

    config = config_result.get("config")
    config_map: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    digest = text_or_none(config_result.get("digest"))
    if digest is None:
        return upstream_failure(
            {"error_code": "invalid_response"},
            fallback="Proxmox returned a VM config with no digest",
        )

    nic = find_nic(list_nics(config_map), target.net)
    if nic is None:
        return tool_failure(ERROR_NET_NOT_FOUND, MESSAGE_NET_GONE)

    return FreshNIC(nic=nic, digest=digest)


def _nic_delta(
    target: NICChangeTarget,
    *,
    verification: str,
    verification_cause: str | None = None,
    changed_fields: tuple[FieldChange, ...] | None,
) -> ChangeDelta:
    """This change's before→after, as the runner that ran it states it.

    One field can move on this tool and it is the link state, so `changed_fields` is the whole
    facet. `changed_fields` is passed in rather than derived, because the two spellings of
    "nothing to report" are decided per branch and are not interchangeable:

    - `()` — nothing moved, and NOA has grounds for saying so. Proxmox refused the write, its
      task came back terminal and non-`OK`, the config could not be read *before* anything was
      written, or the interface was already in the state that was asked for. The last is a
      comparison and the others are the certainty that no write went out, which
      `core.approvals.delta` treats as the same claim: the facet says nothing moved, not how the
      runner came to know it.
    - `None` — NOA cannot say. The write was accepted and the task did not finish in time, the
      confirming read could not answer, or the flip was confirmed and the *evidence* carried no
      before-value to compare it against. Claiming either direction there is the fabrication
      naming-the-subset exists to stop, and `false` would be the one that reads as a measurement.

    The interface's own line is not on the identity, for the reason it is not in the payload: a
    reader needs which interface on which VM moved which way, not the MAC address and bridge of
    a machine somebody is describing to a customer.
    """
    return ChangeDelta(
        identity=_common(target),
        verification=verification,
        verification_cause=verification_cause,
        changed_fields=changed_fields,
    )


def _evidence_link_state(evidence: Mapping[str, Any]) -> str | None:
    """The link state the operator saw on the card, as a delta's `old` side.

    Off the evidence rather than from the runner's own pre-write read: the card is what was
    authorised, and the pre-write read is a second reading taken later that can differ from it
    (which is precisely why the write goes out under that read's digest and not the card's).

    `None` when the evidence carries no usable value — a row opened before the key existed, or
    one whose `nic` half did not survive its JSONB round trip as an object. A caller treats that
    as "no field change can be stated", never as a default: an `old` side nobody recorded is not
    an `old` side of `up`.
    """
    nic = evidence.get(EVIDENCE_NIC)
    if not isinstance(nic, Mapping):
        return None
    link_state = nic.get("link_state")
    return link_state if isinstance(link_state, str) and link_state else None


async def _write_link_state(target: NICChangeTarget, *, fresh: FreshNIC) -> WriteFailure | None:
    """Write the toggled `netN` line under `fresh.digest`. `None` when it took.

    The line written is `fresh.nic.value` with `link_down` edited — the value as it is *now*, not
    the one the card described — so an unrelated edit made while the request was pending survives
    this write instead of being reverted by it (the module docstring's second failure mode).

    Proxmox's `digest_mismatch` passes through with its own code: it means the config moved between
    this runner's read and its write, and the remedy is to ask for the change again rather than to
    retry (`docs/integrations/proxmox.md`).

    **The failure travels rather than being answered here**, because this frame knows what
    Proxmox said and not what the interface now holds. The postflight reads that, and a refusal
    with a reading behind it says more than a refusal alone.
    """
    after_value = set_link_down(fresh.nic.value, disabled=target.disabled)
    write_result = await target.client.update_qemu_config(
        target.node,
        target.vmid,
        digest=fresh.digest,
        net_key=target.net,
        net_value=after_value,
    )
    if write_result.get("ok") is not True:
        return write_failure_or_none(
            upstream_failure(write_result, fallback="Proxmox VM config update failed")
        )

    upid = text_or_none(write_result.get("upid"))
    if upid is None:
        return None
    return await _wait_for_terminal_task(target, upid=upid)


async def _wait_for_terminal_task(target: NICChangeTarget, *, upid: str) -> WriteFailure | None:
    """Poll one UPID to a terminal state. `None` when it finished successfully.

    Terminality is `status == "stopped"`, or an exit status present while the task is no longer
    running — `client.get_task_status` normalises both to `None` when absent, so an empty string
    cannot read as present.

    A poll that cannot *read* the status is treated as a timeout rather than as a task failure:
    not knowing what a task did is not evidence that it failed, and the write it belongs to has
    already been accepted.

    The two failures keep separate codes, and the postflight reads them apart: a terminal non-`OK`
    task is Proxmox saying it did not apply the write, while `task_timeout` is NOA having stopped
    waiting on a write Proxmox accepted. Only the first makes a disagreeing read conclusive.
    """
    for attempt in range(TASK_POLL_ATTEMPTS):
        status_result = await target.client.get_task_status(target.node, upid)
        if status_result.get("ok") is not True:
            break

        task_status = text_or_none(status_result.get("task_status"))
        task_exit_status = text_or_none(status_result.get("task_exit_status"))
        if task_status == "stopped" or (task_exit_status is not None and task_status != "running"):
            if task_exit_status in (None, "OK"):
                return None
            return WriteFailure(
                code=ERROR_TASK_FAILED,
                message=(
                    f"Proxmox rejected the change: its task finished with exit status "
                    f"'{task_exit_status}'."
                ),
            )

        if attempt < TASK_POLL_ATTEMPTS - 1:
            await asyncio.sleep(TASK_POLL_DELAY_SECONDS)

    return WriteFailure(code=ERROR_TASK_TIMEOUT, message=MESSAGE_TASK_TIMEOUT)


async def _verify_link_state(
    target: NICChangeTarget,
    *,
    request: ChangeExecutionRequest,
    write_failure: WriteFailure | None = None,
) -> ChangeOutcome:
    """Did the link actually move? Read off the `netN` line, never off the task.

    The task's exit status says Proxmox accepted a write; it does not say what the interface now
    is. So this re-reads the config and recomputes the link state from the line itself, which is
    the fact the change is about.

    Three answers, and the middle one is the verdict rule one system over:

    1. **verified** — the line carries the state that was asked for.
    2. **unavailable** — the postflight read could not answer. `status: changed` with
       `verified: false` and `verification: unavailable`, never a bare `false` that reads as a
       measurement. Proxmox accepted the write, so reporting this as a failure would send an
       operator to repeat a change that has probably already happened.
    3. **mismatch** — the line is readable and the link is not where it was asked to be. That is a
       measurement, and it is a failure.

    **`write_failure` is how a failed write reaches here without being erased by it.** It is
    `None` on the ordinary path and every branch above reads as it did before. When it is set,
    Proxmox refused the write or never said what it did with it, and the interface is still the
    better witness of where the link now sits — but a postflight that did not know would emit a
    clean confirmed flip and leave no trace anything went wrong.
    """
    fresh = await _read_current_nic(target)
    where = f"`{target.net}` on VM {target.vmid} ({target.server_name})"
    if not isinstance(fresh, FreshNIC):
        cause = str(fresh.get("error_code") or ERROR_UNKNOWN)
        logger.warning(
            LOG_NIC_RUN_UNVERIFIED,
            tool=TOOL_PROXMOX_VM_NIC,
            action_request_id=str(request.action_request_id),
            node=target.node,
            vmid=target.vmid,
            net=target.net,
            cause=cause,
            write_failure=None if write_failure is None else write_failure.code,
        )
        # The cause names the **confirming read** on both paths: it answers why NOA holds no
        # measurement, while the write's own code sits on the envelope where a reader looks for
        # what failed.
        return ChangeOutcome(
            payload=(
                tool_ok(
                    **_common(target),
                    status=STATUS_CHANGED,
                    verified=False,
                    verification=VERIFICATION_UNAVAILABLE,
                    verification_cause=cause,
                    message=(
                        f"Proxmox accepted the change to {where}, but NOA could not read the "
                        "interface back to confirm it. Check the VM before relying on it."
                    ),
                )
                if write_failure is None
                else {
                    **tool_failure(
                        write_failure.code,
                        f"Proxmox {write_failure.verb} the change to {where} "
                        f"(`{write_failure.code}`), and NOA could not read the interface back "
                        "either. Check the VM before relying on it.",
                    ),
                    **_common(target),
                }
            ),
            delta=_nic_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=cause,
                changed_fields=None,
            ),
        )

    matched = fresh.nic.link_state == target.desired_link_state

    if write_failure is None:
        if not matched:
            return ChangeOutcome(
                payload={
                    **tool_failure(
                        ERROR_POSTFLIGHT_FAILED,
                        f"Proxmox accepted the change, but {where} still reads as "
                        f"{fresh.nic.link_state}. Check the VM.",
                    ),
                    **_common(target),
                    "link_state": fresh.nic.link_state,
                    "verified": False,
                },
                # Measured and disagreeing, which is what earns the `verified: false` beside it.
                delta=_nic_delta(target, verification=VERIFICATION_MISMATCH, changed_fields=()),
            )

        return ChangeOutcome(
            payload=tool_ok(
                **_common(target),
                status=STATUS_CHANGED,
                link_state=fresh.nic.link_state,
                verified=True,
                message=(
                    f"{where} was {'disabled' if target.disabled else 'enabled'} and confirmed: "
                    f"its link is now {fresh.nic.link_state}."
                ),
            ),
            delta=_nic_delta(
                target,
                verification=VERIFICATION_VERIFIED,
                changed_fields=_link_state_change(request, measured=fresh.nic.link_state),
            ),
        )

    verification, cause = confirmed_verification(matched=matched, failure=write_failure)
    opener = f"Proxmox {write_failure.verb} the change to {where} (`{write_failure.code}`)"

    if verification == VERIFICATION_VERIFIED:
        # Proxmox never said what it did and the link is where the change asked for it. No
        # hedge: NOA sent the write, Proxmox took it, and qualifying every unanswered call with
        # "NOA cannot prove it caused this" teaches an operator to skip the qualifier.
        return ChangeOutcome(
            payload=tool_ok(
                **_common(target),
                status=STATUS_CHANGED,
                link_state=fresh.nic.link_state,
                verified=True,
                message=(
                    f"{opener}, so NOA re-read the interface: its link is now "
                    f"{fresh.nic.link_state}."
                ),
            ),
            delta=_nic_delta(
                target,
                verification=VERIFICATION_VERIFIED,
                changed_fields=_link_state_change(request, measured=fresh.nic.link_state),
            ),
        )

    return ChangeOutcome(
        payload={
            **tool_failure(
                write_failure.code,
                confirmed_verification_sentence(
                    opener=opener,
                    reading=f"{where} reads as {fresh.nic.link_state}",
                    matched=matched,
                    failure=write_failure,
                    fallback="Proxmox VM config update failed.",
                ),
            ),
            **_common(target),
            # The reading rides beside the verdict on every one of these branches, because it is
            # what a refusal was missing: a code with no state behind it.
            "link_state": fresh.nic.link_state,
            "verified": False,
        },
        delta=_nic_delta(
            target,
            verification=verification,
            verification_cause=cause,
            # `()` only where Proxmox refused and the reading agrees with the refusal — both
            # sides say nothing moved. Everywhere else `None`: an unanswered write may still
            # land, and a link already in the desired state after a refusal was not put there by
            # this change.
            changed_fields=() if verification == VERIFICATION_MISMATCH else None,
        ),
    )


def _link_state_change(
    request: ChangeExecutionRequest, *, measured: str
) -> tuple[FieldChange, ...] | None:
    """The one row a confirmed flip renders, an empty diff, or no diff at all.

    Three answers, and the first two are the distinction `_evidence_link_state`'s own docstring
    asks a caller to keep:

    - `None` — the evidence carried no usable `old` side, so nothing was compared. An `old` side
      nobody recorded is not an `old` side of `up`, and an empty diff here would tell an operator
      the interface was checked against the card and had not moved, on the branch where the
      postflight has just confirmed it did.
    - `()` — both sides were read and they match. Reachable and not a bug: an interface edited
      away and back while the request sat pending reads the same as the card described, and a
      delta claiming a move there would be describing one that did not happen. `ChangeDelta`
      refuses an equal-sided row outright, so the case is decided here rather than raised.
    - one row — the two sides differ, which is the ordinary confirmed flip.
    """
    old = _evidence_link_state(request.evidence)
    if old is None:
        return None
    if old == measured:
        return ()
    return (FieldChange(field="link_state", old=old, new=measured),)


def _common(target: NICChangeTarget) -> ToolPayload:
    """The identifiers every answer from this runner carries.

    The interface's own line is deliberately not among them. This payload becomes
    `tool_runs.result_summary` and `noa_get_action_result` hands that to a model, and
    what a model needs is which interface on which VM moved which way — not the MAC address and
    bridge of a machine it is describing to somebody.
    """
    return {
        "server": target.server_name,
        "node": target.node,
        "vmid": target.vmid,
        "net": target.net,
        "action": target.action,
    }


__all__ = [
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_TASK_FAILED",
    "ERROR_TASK_TIMEOUT",
    "LOG_NIC_RUN_NO_OP",
    "LOG_NIC_RUN_UNVERIFIED",
    "MESSAGE_NET_GONE",
    "MESSAGE_TASK_TIMEOUT",
    "TASK_POLL_ATTEMPTS",
    "TASK_POLL_DELAY_SECONDS",
    "FreshNIC",
    "NICChangeTarget",
    "build_proxmox_nic_runners",
    "build_proxmox_vm_nic_runner",
]
