"""`proxmox_reset_vm_password` — the call that opens a question (T27).

The first Proxmox CHANGE tool, and the first CHANGE of any system whose whole point is a value
NOA generates and the model must never see (C15, V49).

**Two halves, and only the first is here.** This module runs the in-process preflight (C9, V17)
and opens an `action_requests` row; it executes nothing, and the LLM can reach it. The half that
performs the reset is `proxmox_password_runner.py`, reachable only from
`core.approvals.execution` after an operator approved (V22). They are two files for C14 — a
CHANGE tool with a delivery hop, a task poll and a crypt compare runs past 900 lines — and the
split falls on the boundary the design already draws. The dependency runs one way: the runner
imports this module's evidence keys and tool name, and nothing here imports the runner, so
`registry.py` and `change_runners.py` reach the two halves without a cycle.

**No reason parameter, and nowhere to add one** (C8, V15, V43). The word is typed by an operator
on the approval card after this tool's result has been rendered and forgotten, and
`open_change_request` refuses a reason-shaped argument even for a caller that reaches the
function directly.

**No `new_password` parameter either**, which is the same rule one field over (C15, V49). That
was `noa-old`'s bug (GH #91): its reset tool took the password from the model and echoed it back,
putting the plaintext in the prompt, the model context and the stored transcript. NOA generates it
in the runner's frame, and the schema gives a model nowhere to put one.

**There is no no-op branch**, unlike T22, T23 and T26. A password reset has no
already-in-that-state reading — the new password is new every time — so every well-formed call
opens a card.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.proxmox.client import ProxmoxClient
from core.integrations.proxmox.cloudinit import cloudinit_carries_password
from core.servers.proxmox_ref import resolve_proxmox_server_ref
from noa_api.mcp_tools.change_gate import build_change_gate_response, open_change_request
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
)

TOOL_PROXMOX_RESET_VM_PASSWORD = "proxmox_reset_vm_password"  # noqa: S105 — a tool name

# --- Evidence keys ---
#
# This tool's own, not shared with the WHM sets. Two of them spell the same word as a firewall
# key by coincidence, and T25's note is the reason that matters: a key meaning two things in two
# contracts is how a JSONB read silently returns the wrong field. They cross a boundary in time
# as well as in code — the tool writes them into `approval_context`, the runner reads them back
# minutes later — so a misspelling reads as an absent value rather than as an error.
EVIDENCE_SERVER_ID: Final = "server_id"
EVIDENCE_SERVER_NAME: Final = "server"
EVIDENCE_NODE: Final = "node"
EVIDENCE_VMID: Final = "vmid"
EVIDENCE_USERNAME: Final = "username"
EVIDENCE_VM: Final = "vm"

# --- Refusals ---

ERROR_NODE_REQUIRED: Final = "node_required"
MESSAGE_NODE_REQUIRED: Final = (
    "A Proxmox node name is required — the cluster member the VM runs on."
)

ERROR_USERNAME_REQUIRED: Final = "username_required"
MESSAGE_USERNAME_REQUIRED: Final = (
    "A guest username is required. It is the account the new password belongs to, and it is what "
    "the operator will see beside the password."
)

ERROR_INVALID_VMID: Final = "invalid_vmid"
MESSAGE_INVALID_VMID: Final = "A VM id must be a positive whole number."

# An approved change names a Proxmox endpoint that is no longer resolvable. Its own code rather
# than `change_target`'s `whm_server_unavailable`: the code names the inventory an administrator
# has to fix, and those are two different rows in two different tables. Distinct from the
# tool-time resolution failures, which the model can fix by asking again — by the time this fires
# an operator has already typed a reason and pressed Approve.
#
# Here rather than in `proxmox_password_runner`, where T27 first wrote it, because T28 made a
# second Proxmox runner and this is the module both already import (V66) — and it is where
# `change_target`'s docstring says the Proxmox pair lives. `proxmox_password_runner` re-exports
# both names, so callers that reach for them there keep one import path.
ERROR_SERVER_UNAVAILABLE: Final = "proxmox_server_unavailable"
MESSAGE_SERVER_UNAVAILABLE: Final = (
    "The Proxmox server this change was approved for is no longer available. Contact an "
    "administrator."
)

# The VM has a cloud-init user and it is not the one asked for. Refused rather than gated: the
# password would be set for `ciuser` while the delivered blob names somebody else, so the
# operator would hand a customer a login that cannot work — and would have approved a card that
# said so in small print.
ERROR_CLOUDINIT_USER_MISMATCH: Final = "cloudinit_user_mismatch"

# S105 fires on the constant's *name*, not its value: this is the model-facing prose, and the
# only credential anywhere near this module is generated at run time and never a literal.
DESCRIPTION_PROXMOX_RESET_VM_PASSWORD: Final = (
    "Set a new cloud-init password on one Proxmox VM and deliver it to the operator through a "  # noqa: S105
    "one-time yopass link. NOA generates the password itself — you cannot supply one, and you "
    "will never see it. This changes a live system, so it does not run when you call it: NOA "
    "reads the VM's current cloud-init state, opens an approval request, and answers with the "
    "address of a card where an operator decides. The new password reaches the guest when its "
    "cloud-init drive is next read, which for a running VM means its next boot. Read the outcome "
    "with `noa_get_action_result`, pass on the link it returns, and never report a password as "
    "changed without it."
)

SERVER_REF_DESCRIPTION: Final = (
    "Which Proxmox server: its id, its name in NOA, or its hostname. Ask the operator if they "
    "have not named one."
)

# --- The tool: it opens a question and changes nothing (V16, V22, V23) ---


@sanitize_tool_errors(TOOL_PROXMOX_RESET_VM_PASSWORD)
async def proxmox_reset_vm_password(
    *,
    server_ref: str,
    node: str,
    vmid: int,
    username: str,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one VM's cloud-init password to be reset; change nothing (T27 — V16, V17, V23).

    Three guards run before any I/O, so a malformed call costs no round trip (V21): a blank or
    whitespace-only `node` or `username` is refused — the schema cannot express it, because
    `min_length` counts whitespace — and a `vmid` that is not a positive whole number is refused
    here as well as by the schema, because a caller reaching this function directly bypasses
    pydantic and this is a value that goes into a URL path.

    Then one database session — resolve the operator's word to a server (V18: a tie is `choices`,
    never a pick) and turn that row into a client — and the session closes before the HTTP hops,
    which is T21's rule.

    The preflight is this call's own and runs in-process (C9, V17): the VM's config, its
    cloud-init values and its run state, born here, milliseconds old, same user, reaching the
    operator through `approval_context` rather than through a transcript.

    **One preflight answer is a refusal rather than evidence.** A VM whose `ciuser` is set to
    somebody other than `username` would take the password for `ciuser` while the delivered blob
    named the argument — a login that cannot work, handed over as though it could. A VM with no
    `ciuser` at all is *not* refused: Proxmox applies `cipassword` to the image's default account,
    which is a real and ordinary configuration, and the card shows `ciuser` as absent so the
    operator sees exactly that.

    There is no no-op branch, unlike T22/T23/T26. A password reset has no already-in-that-state
    reading: the new password is new every time, and there is nothing to compare a request
    against.
    """
    normalized_node = node.strip()
    if not normalized_node:
        return tool_failure(ERROR_NODE_REQUIRED, MESSAGE_NODE_REQUIRED)

    normalized_username = username.strip()
    if not normalized_username:
        return tool_failure(ERROR_USERNAME_REQUIRED, MESSAGE_USERNAME_REQUIRED)

    if isinstance(vmid, bool) or not isinstance(vmid, int) or vmid < 1:
        return tool_failure(ERROR_INVALID_VMID, MESSAGE_INVALID_VMID)

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
        state = await collect_vm_state(client, node=normalized_node, vmid=vmid)

    if not isinstance(state, VMCloudInitState):
        return state

    if state.ciuser is not None and state.ciuser != normalized_username:
        return tool_failure(
            ERROR_CLOUDINIT_USER_MISMATCH,
            (
                f"VM {vmid} on {server_name} has cloud-init user `{state.ciuser}`, not "
                f"`{normalized_username}`. Resetting the password would change "
                f"`{state.ciuser}`'s credentials. Ask the operator which account they mean."
            ),
        )

    opened = await open_change_request(
        tool_name=TOOL_PROXMOX_RESET_VM_PASSWORD,
        arguments={
            "server_ref": server_ref,
            "node": normalized_node,
            "vmid": vmid,
            "username": normalized_username,
        },
        evidence={
            EVIDENCE_SERVER_ID: server_id,
            EVIDENCE_SERVER_NAME: server_name,
            EVIDENCE_NODE: normalized_node,
            EVIDENCE_VMID: vmid,
            EVIDENCE_USERNAME: normalized_username,
            EVIDENCE_VM: state.as_evidence(),
        },
        context=context,
    )
    return build_change_gate_response(
        opened, tool_name=TOOL_PROXMOX_RESET_VM_PASSWORD, context=context
    )


# --- The preflight (C9, V17) ---


@dataclass(frozen=True)
class VMCloudInitState:
    """The before-state an operator authorises a password reset against (V33, V35).

    **A fixed set of fields, and that is the point.** It is built by naming what goes in rather
    than by sanitizing what came out of Proxmox — the same structural argument V26 makes about
    the gate's URL and V76 makes about `ActionResultView`. A VM config carries `cipassword`
    among a hundred other keys, and a redaction pass over the whole document is a rule that has
    to keep being right as Proxmox adds keys; a structure with nowhere to put one cannot leak by
    an omission nobody noticed (V93's shape).

    `unavailable_reads` names any preflight read that could not answer, rather than letting an
    absent field read as a measured absence (V86). Only the run state is allowed to be missing:
    the config and the cloud-init values are what the decision rests on, so a failure there is a
    refusal, not a gap.
    """

    name: str | None
    ciuser: str | None
    has_cloudinit_password: bool
    run_status: str | None
    unavailable_reads: list[str]

    def as_evidence(self) -> dict[str, Any]:
        """JSON-native, for `approval_context` JSONB (T33's rule)."""
        return {
            "name": self.name,
            "ciuser": self.ciuser,
            "has_cloudinit_password": self.has_cloudinit_password,
            "run_status": self.run_status,
            "unavailable_reads": list(self.unavailable_reads),
        }


async def collect_vm_state(
    client: ProxmoxClient, *, node: str, vmid: int
) -> VMCloudInitState | ToolPayload:
    """Read one VM's cloud-init before-state, or refuse. Internal — ⊥ an MCP tool (C9, V17).

    Two required reads and one tolerated. The config says who the cloud-init user is and whether
    a password is set at all; the cloud-init endpoint is the second opinion on the password,
    because Proxmox reports a *pending* value there that the config may not show yet. A failure
    of either is a refusal — a card that cannot describe what it is asking about is the state V35
    exists to prevent.

    The run state is tolerated because it changes nothing about what the reset *does*: it tells
    an operator when the new password will take effect (a running VM reads its cloud-init drive
    at next boot), and losing that is worth less than refusing the change over it. It is named in
    `unavailable_reads` rather than silently absent (V86).
    """
    config_result = await client.get_qemu_config(node, vmid)
    if config_result.get("ok") is not True:
        return upstream_failure(config_result, fallback="Proxmox VM config lookup failed")

    cloudinit_result = await client.get_qemu_cloudinit(node, vmid)
    if cloudinit_result.get("ok") is not True:
        return upstream_failure(cloudinit_result, fallback="Proxmox cloud-init lookup failed")

    config = config_result.get("config")
    config_map: Mapping[str, Any] = config if isinstance(config, Mapping) else {}

    unavailable_reads: list[str] = []
    status_result = await client.get_qemu_status_current(node, vmid)
    run_status: str | None = None
    if status_result.get("ok") is True:
        data = status_result.get("data")
        if isinstance(data, Mapping):
            run_status = text_or_none(data.get("status"))
    if run_status is None:
        unavailable_reads.append("run_status")

    return VMCloudInitState(
        name=text_or_none(config_map.get("name")),
        ciuser=text_or_none(config_map.get("ciuser")),
        # Either source counts: the config holds the applied value, the cloud-init endpoint the
        # pending one, and "this VM already has a cloud-init password" is true if either says so.
        has_cloudinit_password=(
            text_or_none(config_map.get("cipassword")) is not None
            or cloudinit_carries_password(cloudinit_result.get("data"))
        ),
        run_status=run_status,
        unavailable_reads=unavailable_reads,
    )


def register_proxmox_password_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the Proxmox password CHANGE tool; return its name and risk (I.mcp, V20).

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call (T73) — it opens an approval request and executes nothing, and V46's row belongs to the
    executor that runs after a decision. It is also what makes
    `registry.assert_change_runners_cover` demand a runner for the name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_PROXMOX_RESET_VM_PASSWORD,
        description=DESCRIPTION_PROXMOX_RESET_VM_PASSWORD,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split
        # that matters is the approval gate (V16); the classification that matters is the risk
        # returned below. `destructiveHint` is True because the old password stops working, and
        # `idempotentHint` is False because every run mints a different password.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
    )
    async def proxmox_reset_vm_password_tool(
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
        username: Annotated[
            str,
            Field(
                description=(
                    "The guest account the new password belongs to — the VM's cloud-init user. "
                    "It is delivered beside the password so the operator knows which login it "
                    "is for. NOA refuses the change if the VM's cloud-init user is someone else."
                )
            ),
        ],
    ) -> ToolAnswer:
        return await proxmox_reset_vm_password(
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            username=username,
            context=context,
        )

    return {TOOL_PROXMOX_RESET_VM_PASSWORD: ToolRisk.CHANGE}


# --- Internals ---


def upstream_failure(result: Mapping[str, Any], *, fallback: str) -> ToolPayload:
    """One `ProxmoxClient` refusal as a tool failure, keeping the code that names the remedy.

    The client's strings are stable and split by operator action — `permission_denied` sends
    somebody to Proxmox's ACLs, `auth_failed` to NOA's server row — so collapsing them would
    throw away the only part of the answer that is actionable (V19's argument for passing a
    `NoaError`'s own code through).
    """
    return tool_failure(
        str(result.get("error_code") or ERROR_UNKNOWN),
        str(result.get("message") or fallback),
    )


def text_or_none(value: Any) -> str | None:
    """A stripped non-empty string, or `None`. Non-strings are `None`, ⊥ stringified.

    Public because the runner half reads the same Proxmox payloads back and "absent" has to mean
    the same thing on both sides of the approval (V66).
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


__all__ = [
    "DESCRIPTION_PROXMOX_RESET_VM_PASSWORD",
    "ERROR_CLOUDINIT_USER_MISMATCH",
    "ERROR_INVALID_VMID",
    "ERROR_NODE_REQUIRED",
    "ERROR_SERVER_UNAVAILABLE",
    "ERROR_USERNAME_REQUIRED",
    "EVIDENCE_NODE",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_USERNAME",
    "EVIDENCE_VM",
    "EVIDENCE_VMID",
    "MESSAGE_INVALID_VMID",
    "MESSAGE_NODE_REQUIRED",
    "MESSAGE_SERVER_UNAVAILABLE",
    "MESSAGE_USERNAME_REQUIRED",
    "TOOL_PROXMOX_RESET_VM_PASSWORD",
    "VMCloudInitState",
    "collect_vm_state",
    "proxmox_reset_vm_password",
    "register_proxmox_password_tools",
    "text_or_none",
    "upstream_failure",
]
