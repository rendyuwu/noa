"""A Proxmox VM whose NICs can actually be flipped, and the calls into it.

`support/proxmox.py` owns the client-level double and `support/proxmox_password.py` owns T27's
VM. This owns what `proxmox_vm_nic`'s two lanes need: a VM whose `netN` lines are **state**, the
two call helpers, and the fixture shapes both lanes share. Its own module because those lanes are
two test files, split so neither runs past C14's line budget, and helpers duplicated across files
are helpers that drift.

**`FakeProxmoxNICVM` stores lines, not answers.** A config write parses the submitted `netN` value
and keeps it, so a later read returns what the runner actually sent. That is what makes the
postflight worth running: a fixture that replayed a canned "now it is down" document would pass
with the whole write-then-verify path deleted.

It also **enforces the digest**, the way Proxmox does. A write carrying a digest that is not the
current one is refused with Proxmox's own shape, so "the runner re-reads before it writes" is a
property the transport can fail rather than a comment. `bump_digest()` is how a test plays the
config moving while an approval card sat pending.

`requests` records method and path for every call, which is how "nothing was written" is asserted
on the **absence of a POST** rather than on a return value the code under test produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from core.approvals.execution import ChangeExecutionRequest
from core.integrations.proxmox.nic import read_nic
from noa_api.mcp_tools import proxmox_nic_runner
from noa_api.mcp_tools.proxmox_nic import (
    ACTION_DISABLE,
    EVIDENCE_ACTION,
    EVIDENCE_NET,
    EVIDENCE_NIC,
    EVIDENCE_NODE,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_VM,
    EVIDENCE_VMID,
    TOOL_PROXMOX_VM_NIC,
    proxmox_vm_nic,
)
from support.action_decisions import REASON
from support.mcp_identity import authenticated_caller, http_request_context
from support.servers import ToolFixture, build_tool_context, proxmox_server

SERVER_NAME = "pve-cluster"
NODE = "pve1"
VMID = 110
VM_NAME = "customer-web-01"

NET0 = "net0"
NET1 = "net1"

# One ordinary NIC line, and a second one for the ambiguity cases. The MAC is what `read_nic`
# recognises the model segment by, so these carry real ones.
NET0_UP = "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr0,firewall=1"
NET1_UP = "e1000=AA:BB:CC:DD:EE:02,bridge=vmbr1,tag=42"

UPID = "UPID:pve1:0000AB12:00CDEF34:66B00000:qmconfig:110:noa@pve!token:"

DIGEST = "0f1e2d3c4b5a6978"


@dataclass
class FakeProxmoxNICVM:
    """One VM whose `netN` lines a change can actually move, with per-step failure knobs.

    The defaults describe a healthy running VM with a single interface whose link is up — the
    shape of the common call. Each knob turns exactly one step into a failure, so a test names
    the branch it is about instead of arranging a whole broken world.
    """

    nets: dict[str, str] = field(default_factory=lambda: {NET0: NET0_UP})
    vm_name: str | None = VM_NAME
    run_status: str | None = "running"
    digest: str = DIGEST

    # --- failure knobs, one per step ---
    config_error: dict[str, Any] | None = None
    status_error: dict[str, Any] | None = None
    write_error: dict[str, Any] | None = None
    # Answers config reads with a payload carrying no digest at all.
    drop_digest: bool = False
    # `None` → the write answers synchronously (`data: null`). A string → that UPID is polled.
    write_upid: str | None = UPID
    task_exit_status: str = "OK"
    # The task never reaches a terminal state — the poll-timeout branch.
    task_never_finishes: bool = False
    # Accept the write and keep the old line: the VM whose link did not move. `task_exit_status`
    # stays `OK`, which is what makes this the §V.97 case — the task says yes, the NIC says no.
    ignore_write: bool = False
    # Fail every config read taken *after* the first write — the postflight-unavailable branch,
    # with the preflight left readable so the change still happens.
    fail_reads_after_write: bool = False

    requests: list[tuple[str, str]] = field(default_factory=list)
    written_values: list[tuple[str, str]] = field(default_factory=list)
    _writes: int = 0

    # --- the transport ---

    def transport(self) -> httpx.MockTransport:
        """An `httpx` transport answering this VM. The only doubled thing in a tool test."""
        return httpx.MockTransport(self._handle)

    @property
    def config_writes(self) -> list[tuple[str, str]]:
        """Every request that would have changed the VM. Empty is the "opened a question" assert."""
        return [(method, path) for method, path in self.requests if method == "POST"]

    def bump_digest(self) -> None:
        """The config moved — somebody else edited this VM. Any digest read before now is stale."""
        self.digest = f"{self.digest}-moved"

    def link_state(self, net: str = NET0) -> str:
        """What this VM's NIC line actually says now, read through the production codec."""
        return read_nic(net, self.nets[net]).link_state

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))

        if request.method == "POST" and path.endswith(f"/qemu/{VMID}/config"):
            return self._write(request)
        if path.endswith(f"/qemu/{VMID}/config"):
            return self._read_config()
        if path.endswith("/status/current"):
            return self._answer(self.status_error, data={"status": self.run_status})
        if "/tasks/" in path:
            return self._answer(None, data=self._task())
        return httpx.Response(404, json={"data": None}, request=request)

    # --- state ---

    def _read_config(self) -> httpx.Response:
        if self.fail_reads_after_write and self._writes:
            return self._answer({"status": 500, "message": "node offline"}, data=None)
        return self._answer(self.config_error, data=self._config())

    def _write(self, request: httpx.Request) -> httpx.Response:
        if self.write_error is not None:
            return self._answer(self.write_error, data=None)

        form = _form(request)
        submitted_digest = form.get("digest")
        if submitted_digest is not None and submitted_digest != self.digest:
            # Proxmox's own refusal for a stale compare-and-set token. Matched by
            # `ProxmoxClient` on the word "digest" plus a change word.
            return self._answer(
                {"status": 500, "message": "VM 110 - unable to parse value: digest mismatch"},
                data=None,
            )

        self._writes += 1
        for key, value in form.items():
            if key.startswith("net"):
                self.written_values.append((key, value))
                if not self.ignore_write:
                    self.nets[key] = value
        return self._answer(None, data=self.write_upid)

    def _config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"name": self.vm_name, **self.nets}
        if not self.drop_digest:
            config["digest"] = self.digest
        return config

    def _task(self) -> dict[str, Any]:
        if self.task_never_finishes:
            return {"status": "running", "exitstatus": None}
        return {"status": "stopped", "exitstatus": self.task_exit_status}

    def _answer(self, error: dict[str, Any] | None, *, data: Any) -> httpx.Response:
        """A Proxmox envelope, or the refusal a knob asked for.

        Refusals go out in Proxmox's own shapes so `ProxmoxClient` classifies them for real: a
        status code alone, or a body carrying `errors`/`message`.
        """
        if error is not None:
            return httpx.Response(
                error.get("status", 500),
                json=error.get("body", {"message": error.get("message", "boom")}),
            )
        return httpx.Response(200, json={"data": data})


def _form(request: httpx.Request) -> dict[str, str]:
    """An `application/x-www-form-urlencoded` body as a mapping."""
    return dict(httpx.QueryParams(request.content.decode("utf-8")))


def nic_context(
    *,
    vm: FakeProxmoxNICVM | None = None,
    servers: list[Any] | None = None,
    **kwargs: Any,
) -> tuple[ToolFixture, FakeProxmoxNICVM]:
    """A tool context whose Proxmox endpoint is reachable only through `vm`.

    The row's `api_token_secret` is real ciphertext under the fixture's own cipher, so the
    production decrypt site runs on the way to every call.
    """
    box = vm or FakeProxmoxNICVM()
    fixture = build_tool_context(proxmox_transport=box.transport(), **kwargs)
    rows = servers if servers is not None else [proxmox_server(SERVER_NAME)]
    for row in rows:
        row.api_token_secret = fixture.cipher.encrypt_text("proxmox-token-secret")
    fixture.proxmox_servers.servers = list(rows)
    return fixture, box


async def call_nic(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    node: str = NODE,
    vmid: Any = VMID,
    action: str = ACTION_DISABLE,
    net: str | None = None,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The context is not decoration: `open_change_request` reads the requester from the
    authenticated identity rather than from an argument, so a call outside it would be
    asserting against an identity the test planted.
    """
    user, resolved = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await proxmox_vm_nic(
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            action=action,
            net=net,
            context=fixture.context,
        )
    return answer, resolved


def execution_request(
    *,
    server_id: UUID | str,
    node: Any = NODE,
    vmid: Any = VMID,
    net: Any = NET0,
    action: Any = ACTION_DISABLE,
    link_state: str = "up",
    reason: str = REASON,
    action_request_id: UUID | None = None,
    server_ref: str = "some-other-endpoint",
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved NIC change.

    The evidence is what the gate wrote and what the operator saw; the arguments deliberately name
    a `server_ref` the runner must ignore, so every runner test that resolves a target is also a
    V33 assertion.
    """
    return ChangeExecutionRequest(
        action_request_id=action_request_id or uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_PROXMOX_VM_NIC,
        arguments={
            "server_ref": server_ref,
            "node": node,
            "vmid": vmid,
            "action": action,
            "net": net,
        },
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_NODE: node,
            EVIDENCE_VMID: vmid,
            EVIDENCE_NET: net,
            EVIDENCE_ACTION: action,
            EVIDENCE_NIC: {
                "net": net,
                "model": "virtio",
                "mac_address": "AA:BB:CC:DD:EE:01",
                "bridge": "vmbr0",
                "link_state": link_state,
                "auto_selected": True,
            },
            EVIDENCE_VM: {
                "name": VM_NAME,
                "run_status": "running",
                "nics": [],
                "unavailable_reads": [],
            },
        },
        reason=reason,
    )


def no_polling_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the task poll interval to zero.

    The count stays — a timeout test still makes thirty round trips — so what is removed is
    fifteen seconds of wall clock and nothing about the behaviour under test. Patched on the
    module constant rather than on `asyncio.sleep`, which is shared by everything else running in
    the loop.
    """
    monkeypatch.setattr(proxmox_nic_runner, "TASK_POLL_DELAY_SECONDS", 0)


def payload_text(payload: Any) -> str:
    """Everything a payload would carry into an audit row, as one string.

    `default=str` for the reason `core.audit.summaries` uses it: this is asserted *against*, so a
    value that would not serialize must still show up rather than raise and leave the assertion
    unmade.
    """
    return json.dumps(payload, default=str)


__all__ = [
    "DIGEST",
    "NET0",
    "NET0_UP",
    "NET1",
    "NET1_UP",
    "NODE",
    "SERVER_NAME",
    "UPID",
    "VMID",
    "VM_NAME",
    "FakeProxmoxNICVM",
    "call_nic",
    "execution_request",
    "nic_context",
    "no_polling_delay",
    "payload_text",
]
