"""`proxmox_vm_nic` — one tool for two directions.

The second Proxmox CHANGE tool, and the first of the two **enum collapses** DECISIONS section 9
adopted: `proxmox_enable_vm_nic` and `proxmox_disable_vm_nic` become one tool with an `action`
parameter. That is a schema decision with a recorded cost — RBAC gets coarser, because a role can
no longer be granted one direction without the other (DECISIONS section 9) — and it is not
re-litigated here.

**Two halves, and only the first is here.** This module runs the in-process preflight
and opens an `action_requests` row; it executes nothing, and the LLM can reach it. The half that
flips the link is `proxmox_nic_runner.py`, reachable only from `core.approvals.execution` after an
operator approved. Two files for the file-size cap, split on the boundary the design already
draws, and the dependency runs one way — the password-reset tool's arrangement, one tool over.

**No reason parameter, and nowhere to add one**. `open_change_request` refuses a
reason-shaped argument even for a caller reaching this function directly.

**No `digest` parameter either, and no digest on the evidence** — this is where this tool departs
from `noa-old` deliberately. There, the digest was a *tool argument*: a preflight read it, the model
handed it back on the change call seconds later, and Proxmox refused the write if the config had
moved. An approval gate turns those seconds into minutes, and two things follow. A gate-time
digest would make any unrelated edit to the VM — memory, a disk, a description — refuse a change
an operator had already typed a reason for. And writing back the gate-time `netN` *value* would
silently revert a bridge or model edit made while the card sat pending: a lost update NOA
authored. So the runner re-reads and writes under a fresh digest, and the compare-and-set window
is its own read→write. What the approval window is checked against is the fact the operator
actually approved — the NIC's **link state** — which the runner re-reads and compares.

**One NIC is inferred; two without a name is `choices`**. A VM with a single NIC is not
an ambiguity, and refusing it would make the common case a two-turn conversation. The inference is
recorded on the evidence (`auto_selected`), so the card tells the operator NOA picked rather than
hiding it behind a `netN` key they never typed.

**A no-op branch, unlike the password-reset tool.** A NIC already in the state being asked for has
nothing to authorise, so this answers `no_op` and opens nothing — the suspend, unsuspend and
allowlist-remove tools' shape. That answer is transcript, so it is built from the server name, the
VM and one measured boolean rather than from the config it was decided from.

**No reason value has an instance here.** A `netN` line has no note field and NOA writes no
`description`, so nothing kept from the LLM is written onto this VM, and nothing NOA wrote comes
back to a model — the password-reset tool's situation, stated rather than assumed, and asserted on
the runner's payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.proxmox.client import ProxmoxClient
from core.integrations.proxmox.nic import (
    LINK_STATE_DOWN,
    LINK_STATE_UP,
    NetworkInterface,
    find_nic,
    list_nics,
)
from core.servers.proxmox_ref import resolve_proxmox_server_ref
from noa_api.mcp_tools.change_gate import (
    EVIDENCE_ASKED,
    EVIDENCE_HEADLINE,
    build_change_gate_response,
    open_change_request,
)
from noa_api.mcp_tools.change_target import STATUS_NO_OP
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.proxmox_password import text_or_none, upstream_failure
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)

TOOL_PROXMOX_VM_NIC: Final = "proxmox_vm_nic"

# --- The action enum --- Two words, and the pair is the whole of DECISIONS section 9's collapse.
# They are constants rather than literals at each site because they cross three boundaries: the
# published schema, the JSONB evidence, and the runner reading that evidence back minutes later.
ACTION_ENABLE: Final = "enable"
ACTION_DISABLE: Final = "disable"
ACTIONS: Final[tuple[str, ...]] = (ACTION_ENABLE, ACTION_DISABLE)

# --- Evidence keys --- This tool's own. They are written into `approval_context` here and read back
# by the runner after a decision, so a misspelling reads as an absent value rather than as an error
# — the password-reset tool's warning, and the reason these are constants shared by both halves
# rather than string literals twice.
EVIDENCE_SERVER_ID: Final = "server_id"
EVIDENCE_SERVER_NAME: Final = "server"
EVIDENCE_NODE: Final = "node"
EVIDENCE_VMID: Final = "vmid"
EVIDENCE_NET: Final = "net"
EVIDENCE_ACTION: Final = "action"
EVIDENCE_NIC: Final = "nic"
EVIDENCE_VM: Final = "vm"

# --- Refusals ---

ERROR_NODE_REQUIRED: Final = "node_required"
MESSAGE_NODE_REQUIRED: Final = (
    "A Proxmox node name is required — the cluster member the VM runs on."
)

ERROR_INVALID_VMID: Final = "invalid_vmid"
MESSAGE_INVALID_VMID: Final = "A VM id must be a positive whole number."

# The schema publishes an enum, so a well-behaved client cannot produce this. It is here for the
# caller that reaches the function directly, which is the release-and-allow tool's three-place
# discipline for a bounded argument: what `tools/list` publishes, what the body re-checks, and
# what the runner re-checks after the JSONB round trip.
ERROR_INVALID_ACTION: Final = "invalid_action"
MESSAGE_INVALID_ACTION: Final = (
    "The action must be `enable` or `disable`, exactly — nothing else names a link state."
)

ERROR_NO_NICS_FOUND: Final = "no_nics_found"
MESSAGE_NO_NICS_FOUND: Final = (
    "This VM has no QEMU network interfaces, so there is no link to enable or disable."
)

# The two ambiguity codes. Both ship `choices`, because a refusal that does not say
# what the options were makes the model ask the operator a question NOA could have answered.
ERROR_NET_SELECTION_REQUIRED: Final = "net_selection_required"
ERROR_NET_NOT_FOUND: Final = "net_not_found"

DESCRIPTION_PROXMOX_VM_NIC: Final = (
    "Enable or disable one network interface on one Proxmox VM. `disable` cuts the VM off the "
    "network by taking its link down; `enable` puts it back. This changes a live system, so it "
    "does not run when you call it: NOA reads the VM's current interfaces, opens an approval "
    "request, and answers with the address of a card where an operator decides. If the interface "
    "is already in the state you asked for, NOA says so and opens nothing. When the VM has more "
    "than one interface you must name which — call this without `net` and NOA will list them "
    "rather than pick. Read the outcome with `noa_get_action_result`, and never report a link as "
    "changed without it."
)

SERVER_REF_DESCRIPTION: Final = (
    "Which Proxmox server: its id, its name in NOA, or its hostname. Ask the operator if they "
    "have not named one."
)


# --- The tool: it opens a question and changes nothing ---


@sanitize_tool_errors(TOOL_PROXMOX_VM_NIC)
async def proxmox_vm_nic(
    *,
    server_ref: str,
    node: str,
    vmid: int,
    action: str,
    net: str | None = None,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one VM NIC's link to be enabled or disabled; change nothing.

    Three guards run before any I/O, so a malformed call costs no round trip: a blank or
    whitespace-only `node` is refused — the schema cannot express it, because `min_length` counts
    whitespace — a `vmid` that is not a positive whole number is refused here as well as by the
    schema, because a caller reaching this function directly bypasses pydantic and this value goes
    into a URL path, and an `action` outside the enum is refused for the same reason.

    Then one database session — resolve the operator's word to a server (a tie is `choices`,
    never a pick) — and the session closes before the HTTP hops, which is the account search's
    rule.

    The preflight is this call's own and runs in-process: the VM's interfaces and its run
    state, born here, milliseconds old, same user, reaching the operator through
    `approval_context` rather than through a transcript.

    Two of its answers are refusals rather than evidence, and both are ambiguity rather than
    failure: a VM with several NICs and no `net` named, and a `net` that is not on this
    VM. Each ships the interfaces it found, so the next call can name one.

    One is an answer rather than a question: a NIC already in the state being asked for is `no_op`,
    and no request is opened. There is nothing for an operator to authorise.
    """
    normalized_node = node.strip()
    if not normalized_node:
        return tool_failure(ERROR_NODE_REQUIRED, MESSAGE_NODE_REQUIRED)

    if isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 1:
        return tool_failure(ERROR_INVALID_VMID, MESSAGE_INVALID_VMID)

    if action not in ACTIONS:
        return tool_failure(ERROR_INVALID_ACTION, MESSAGE_INVALID_ACTION)

    requested_net = text_or_none(net)

    async with context.session_factory() as session:
        repository = context.proxmox_server_repository_factory(session)
        resolution = await resolve_proxmox_server_ref(server_ref, repository=repository)
        if not resolution.ok or resolution.server is None:
            return tool_failure(
                resolution.error_code or ERROR_UNKNOWN,
                resolution.message,
                choices=resolution.choices,
            )
        server_id = str(resolution.server.id)
        server_name = resolution.server.name
        client = context.proxmox_client_factory(resolution.server, cipher=context.secret_cipher)

    async with client:
        state = await collect_nic_state(
            client, node=normalized_node, vmid=vmid, requested_net=requested_net
        )

    if not isinstance(state, VMNICState):
        return state

    selection = select_requested_nic(state.nics, requested_net=requested_net)
    if not isinstance(selection, NICSelection):
        return selection

    nic = selection.nic
    if nic.link_state == link_state_for(action):
        return tool_ok(
            status=STATUS_NO_OP,
            server=server_name,
            node=normalized_node,
            vmid=vmid,
            net=nic.key,
            action=action,
            link_state=nic.link_state,
            message=(
                f"`{nic.key}` on VM {vmid} ({server_name}) is already "
                f"{'disabled' if action == ACTION_DISABLE else 'enabled'}; nothing to approve."
            ),
        )

    opened = await open_change_request(
        tool_name=TOOL_PROXMOX_VM_NIC,
        arguments={
            "server_ref": server_ref,
            "node": normalized_node,
            "vmid": vmid,
            "action": action,
            "net": requested_net,
        },
        evidence={
            EVIDENCE_HEADLINE: (
                f"{'Disable' if action == ACTION_DISABLE else 'Enable'} a network interface — "
                f"{nic.key} on VM {vmid}"
            ),
            # An imperative, never a prediction. `up` / `down` are the words Proxmox itself uses
            # and stay untranslated everywhere they appear, so the verb here is the operator's
            # own `enable` / `disable` rather than a third spelling of the same thing.
            EVIDENCE_ASKED: f"{action} {nic.key} on VM {vmid} ({normalized_node})",
            EVIDENCE_SERVER_ID: server_id,
            EVIDENCE_SERVER_NAME: server_name,
            EVIDENCE_NODE: normalized_node,
            EVIDENCE_VMID: vmid,
            EVIDENCE_NET: nic.key,
            EVIDENCE_ACTION: action,
            EVIDENCE_NIC: {**nic.as_evidence(), "auto_selected": selection.auto_selected},
            EVIDENCE_VM: state.as_evidence(),
        },
        context=context,
    )
    return build_change_gate_response(opened, tool_name=TOOL_PROXMOX_VM_NIC, context=context)


def link_state_for(action: str) -> str:
    """The link state `action` asks for. One mapping, read by both halves."""
    return LINK_STATE_DOWN if action == ACTION_DISABLE else LINK_STATE_UP


# --- The preflight ---


@dataclass(frozen=True)
class VMNICState:
    """The before-state an operator authorises a NIC change against.

    A fixed set of fields, built by naming what goes in rather than by sanitizing what came out of
    Proxmox — a VM config carries a hundred keys, and a structure with nowhere to put one cannot
    leak by an omission nobody noticed.

    `unavailable_reads` names any preflight read that could not answer, rather than letting an
    absent field read as a measured absence. Only the run state is allowed to be missing:
    the config is what the decision rests on, so a failure there is a refusal, not a gap.
    """

    name: str | None
    run_status: str | None
    nics: list[NetworkInterface]
    unavailable_reads: list[str]

    def as_evidence(self) -> dict[str, Any]:
        """JSON-native, for `approval_context` JSONB (the gate's rule).

        Every NIC, not only the selected one: an operator deciding whether to cut a VM off the
        network is deciding about *this* VM's connectivity, and whether the machine keeps another
        live interface afterwards is the difference between an inconvenience and a machine nobody
        can reach.
        """
        return {
            "name": self.name,
            "run_status": self.run_status,
            "nics": [nic.as_evidence() for nic in self.nics],
            "unavailable_reads": list(self.unavailable_reads),
        }


@dataclass(frozen=True)
class NICSelection:
    """Which NIC the change is about, and whether the operator named it.

    `auto_selected` rides onto the card. A VM with one interface does not need naming (that rule is
    about ambiguity, and one candidate is not ambiguous), but an operator approving a change to a
    `netN` key they never typed should be able to see that NOA chose it.
    """

    nic: NetworkInterface
    auto_selected: bool


async def collect_nic_state(
    client: ProxmoxClient, *, node: str, vmid: int, requested_net: str | None
) -> VMNICState | ToolPayload:
    """Read one VM's interfaces and run state, or refuse. Internal — never an MCP tool.

    One required read and one tolerated. The config carries the `netN` lines the whole decision
    rests on, so a failure there is a refusal — a card that cannot describe what it is asking
    about is the state the provenance rule exists to prevent.

    The run state is tolerated because it changes nothing about what the change *does*. It tells
    an operator what taking the link down will interrupt, and losing that is worth less than
    refusing the change over it. It is named in `unavailable_reads` rather than silently absent.

    The digest this read also returns is deliberately dropped — see the module docstring. The
    runner re-reads and writes under a fresh one, so carrying it here would be a stale token in
    front of an operator, and a trap for the next reader of the evidence.

    `requested_net` is taken only to answer "there are no NICs at all" before the selection runs,
    so the emptiest case gets the clearest code rather than an ambiguity refusal with no choices
    in it.
    """
    config_result = await client.get_qemu_config(node, vmid)
    if config_result.get("ok") is not True:
        return upstream_failure(config_result, fallback="Proxmox VM config lookup failed")

    config = config_result.get("config")
    config_map: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    nics = list_nics(config_map)
    if not nics:
        return tool_failure(ERROR_NO_NICS_FOUND, MESSAGE_NO_NICS_FOUND)

    unavailable_reads: list[str] = []
    status_result = await client.get_qemu_status_current(node, vmid)
    run_status: str | None = None
    if status_result.get("ok") is True:
        data = status_result.get("data")
        if isinstance(data, Mapping):
            run_status = text_or_none(data.get("status"))
    if run_status is None:
        unavailable_reads.append("run_status")

    return VMNICState(
        name=text_or_none(config_map.get("name")),
        run_status=run_status,
        nics=nics,
        unavailable_reads=unavailable_reads,
    )


def select_requested_nic(
    nics: list[NetworkInterface], *, requested_net: str | None
) -> NICSelection | ToolPayload:
    """The NIC this call is about, or a refusal that lists the candidates.

    Three outcomes and only one of them is a pick:

    - a `net` was named and exists → that one, `auto_selected=False`.
    - no `net` and exactly one interface → that one, `auto_selected=True`. One candidate is not
      an ambiguity, and refusing it would make the common case a two-turn conversation.
    - anything else → `choices`, never a guess. Disabling the wrong interface on a VM with two is
      a machine cut off the network on the strength of NOA's coin flip.
    """
    if requested_net is not None:
        nic = find_nic(nics, requested_net)
        if nic is None:
            return tool_failure(
                ERROR_NET_NOT_FOUND,
                f"This VM has no interface named `{requested_net}`. Name one of the interfaces "
                "it does have.",
                choices=[candidate.as_choice() for candidate in nics],
            )
        return NICSelection(nic=nic, auto_selected=False)

    if len(nics) == 1:
        return NICSelection(nic=nics[0], auto_selected=True)

    return tool_failure(
        ERROR_NET_SELECTION_REQUIRED,
        "This VM has more than one network interface. Ask the operator which one to change, then "
        "call again with `net` set to its name.",
        choices=[candidate.as_choice() for candidate in nics],
    )


def register_proxmox_nic_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register the Proxmox NIC CHANGE tool; return its name and risk.

    **One tool, one `action`** — the whole of DECISIONS section 9's collapse, and the reason this
    registrar returns a single entry where `noa-old` had two tools. `Literal` rather than a free
    string is what puts the two words into the published input schema, so a model reads the pair
    from `tools/list` instead of from the description.

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call — it opens an approval request and executes nothing — and what makes
    `registry.assert_change_runners_cover` demand a runner for the name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_PROXMOX_VM_NIC,
        description=DESCRIPTION_PROXMOX_VM_NIC,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split that
        # matters is the approval gate; the classification that matters is the risk returned
        # below. `idempotentHint` is True because asking for a state the NIC is already in is a
        # `no_op` rather than a second change.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def proxmox_vm_nic_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
        node: Annotated[
            str,
            Field(
                description=(
                    "The Proxmox node the VM runs on, exactly as Proxmox names it (for example "
                    "`pve1`). It is the cluster member, not the VM."
                )
            ),
        ],
        vmid: Annotated[
            int,
            Field(
                ge=1,
                description="The VM's numeric id in Proxmox (its VMID), for example 110.",
            ),
        ],
        action: Annotated[
            Literal["enable", "disable"],
            Field(
                description=(
                    "`disable` takes the interface's link down, cutting the VM off the network. "
                    "`enable` brings it back up. There is no other value."
                )
            ),
        ],
        net: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Which interface, as Proxmox names it (`net0`, `net1`, …). Leave it out when "
                    "the VM has exactly one interface — NOA will use that one and say so on the "
                    "approval card. With more than one, leaving it out returns the list rather "
                    "than a guess."
                ),
            ),
        ] = None,
    ) -> ToolAnswer:
        return await proxmox_vm_nic(
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            action=action,
            net=net,
            context=context,
        )

    return {TOOL_PROXMOX_VM_NIC: ToolRisk.CHANGE}


__all__ = [
    "ACTIONS",
    "ACTION_DISABLE",
    "ACTION_ENABLE",
    "DESCRIPTION_PROXMOX_VM_NIC",
    "ERROR_INVALID_ACTION",
    "ERROR_INVALID_VMID",
    "ERROR_NET_NOT_FOUND",
    "ERROR_NET_SELECTION_REQUIRED",
    "ERROR_NODE_REQUIRED",
    "ERROR_NO_NICS_FOUND",
    "EVIDENCE_ACTION",
    "EVIDENCE_NET",
    "EVIDENCE_NIC",
    "EVIDENCE_NODE",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_VM",
    "EVIDENCE_VMID",
    "MESSAGE_INVALID_ACTION",
    "MESSAGE_INVALID_VMID",
    "MESSAGE_NODE_REQUIRED",
    "MESSAGE_NO_NICS_FOUND",
    "TOOL_PROXMOX_VM_NIC",
    "NICSelection",
    "VMNICState",
    "collect_nic_state",
    "link_state_for",
    "proxmox_vm_nic",
    "register_proxmox_nic_tools",
    "select_requested_nic",
]
