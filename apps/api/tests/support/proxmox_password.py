"""A Proxmox VM that answers the password-reset workflow, and the calls into it.

`support/proxmox.py` owns the client-level double — a `ProxmoxClient` over an
`httpx.MockTransport` — which is what `test_proxmox_client*.py` need. This owns what a *tool*
test needs: a VM with state that the reset workflow actually changes, the two call helpers for
the tool and its runner, and the fixture shapes both lanes share. Its own module because those
lanes are two test files, split so neither runs past the line budget, and helpers duplicated
across files are helpers that drift.

**`FakeProxmoxVM` holds state rather than replaying canned answers**, and that is what makes the
happy path worth running. Writing `cipassword` recomputes the VM's stored crypt hash with the
**real** `crypt(3)`, so the rendered user-data the runner then reads is a document the real
`verify_cloudinit_password` really has to agree with. A fixture that returned a pre-baked "matching"
dump would make the crypt compare a formality and would pass with the comparison deleted.

Every knob is a *failure* knob, and each names one step of the workflow, because what the runner
tests are actually about is which failure ships the yopass URL and which does not.

`requests` records method and path for every call, which is how "yopass failed, so nothing was
written" is asserted on the **absence of a POST** rather than on a return value the code under
test produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from core.approvals.execution import ChangeExecutionRequest
from core.integrations.proxmox.cloudinit import crypt_password
from noa_api.mcp_tools import proxmox_password_runner
from noa_api.mcp_tools.proxmox_password import (
    EVIDENCE_NODE,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_USERNAME,
    EVIDENCE_VM,
    EVIDENCE_VMID,
    TOOL_PROXMOX_RESET_VM_PASSWORD,
    proxmox_reset_vm_password,
)
from support.action_decisions import REASON
from support.mcp_identity import authenticated_caller, http_request_context
from support.servers import ToolFixture, build_tool_context, proxmox_server

SERVER_NAME = "pve-cluster"
NODE = "pve1"
VMID = 110
USERNAME = "ubuntu"
VM_NAME = "customer-web-01"

# A SHA-512 salt, so the hash the fake stores is the shape a real cloud-init drive carries.
SALT = "$6$noatestsalt$"

# The password the VM starts out with. Asserting against it is how "the reset actually changed
# something" is separable from "the dump happened to parse".
OLD_PASSWORD = "the-old-one"

UPID = "UPID:pve1:0000AB12:00CDEF34:66B00000:qmconfig:110:noa@pve!token:"


def _password_line(password_hash: str) -> str:
    """One rendered `#cloud-config` user-data document carrying `password_hash`.

    The surrounding keys are there so `extract_cloudinit_password_hash` has a document to find
    the line *in* rather than a bare string, and so a parser that returned the first colon-bearing
    line would fail.
    """
    return (
        f"#cloud-config\nuser: {USERNAME}\npassword: {password_hash}\nchpasswd:\n  expire: False\n"
    )


@dataclass
class FakeProxmoxVM:
    """One VM the reset workflow can be driven against, with per-step failure knobs.

    The defaults describe a healthy cloud-init VM whose reset succeeds and verifies. Each knob
    below turns exactly one step into a failure, which is what lets a runner test name the branch
    it is about instead of arranging a whole broken world.
    """

    ciuser: str | None = USERNAME
    vm_name: str | None = VM_NAME
    run_status: str | None = "running"
    # The crypt hash the VM currently carries. Replaced when `cipassword` is written.
    password_hash: str | None = None

    # --- failure knobs, one per step ---
    config_error: dict[str, Any] | None = None
    cloudinit_error: dict[str, Any] | None = None
    status_error: dict[str, Any] | None = None
    set_error: dict[str, Any] | None = None
    regenerate_error: dict[str, Any] | None = None
    dump_error: dict[str, Any] | None = None
    # `None` → the write answers synchronously (`data: null`). A string → that UPID is polled.
    set_upid: str | None = UPID
    task_exit_status: str = "OK"
    # The task never reaches a terminal state — the poll-timeout branch.
    task_never_finishes: bool = False
    # Accept the write but do not let it change the stored hash: the mismatch branch, i.e. a VM
    # whose cloud-init still shows somebody else's password.
    ignore_password_write: bool = False
    # Render a document with no `password:` line at all.
    drop_password_line: bool = False

    requests: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.password_hash is None:
            self.password_hash = crypt_password(OLD_PASSWORD, SALT)

    # --- the transport ---

    def transport(self) -> httpx.MockTransport:
        """An `httpx` transport answering this VM. The only doubled thing in a tool test."""
        return httpx.MockTransport(self._handle)

    @property
    def config_writes(self) -> list[tuple[str, str]]:
        """Every request that would have changed the VM. Empty is the verdict rule's abort
        assertion.
        """
        return [(method, path) for method, path in self.requests if method in {"POST", "PUT"}]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))

        if request.method == "POST" and path.endswith(f"/qemu/{VMID}/config"):
            return self._set_password(request)
        if request.method == "PUT" and path.endswith(f"/qemu/{VMID}/cloudinit"):
            return self._answer(self.regenerate_error, data=None)
        if path.endswith(f"/qemu/{VMID}/config"):
            return self._answer(self.config_error, data=self._config())
        if path.endswith("/cloudinit/dump"):
            return self._answer(self.dump_error, data=self._dump())
        if path.endswith(f"/qemu/{VMID}/cloudinit"):
            return self._answer(self.cloudinit_error, data=self._cloudinit())
        if path.endswith("/status/current"):
            return self._answer(self.status_error, data={"status": self.run_status})
        if "/tasks/" in path:
            return self._answer(None, data=self._task())
        return httpx.Response(404, json={"data": None}, request=request)

    # --- state ---

    def _set_password(self, request: httpx.Request) -> httpx.Response:
        if self.set_error is not None:
            return self._answer(self.set_error, data=None)
        submitted = _form_value(request, "cipassword")
        if submitted is not None and not self.ignore_password_write:
            self.password_hash = crypt_password(submitted, SALT)
        return self._answer(None, data=self.set_upid)

    def _config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"digest": "0f1e2d3c", "name": self.vm_name}
        if self.ciuser is not None:
            config["ciuser"] = self.ciuser
        if self.password_hash is not None:
            # Proxmox never returns the plaintext; it reports the field as set.
            config["cipassword"] = "**********"
        return config

    def _cloudinit(self) -> list[dict[str, str]]:
        entries = [{"key": "ciuser", "value": self.ciuser or ""}]
        if self.password_hash is not None:
            entries.append({"key": "cipassword", "value": "**********"})
        return entries

    def _dump(self) -> str:
        if self.drop_password_line or self.password_hash is None:
            return f"#cloud-config\nuser: {USERNAME}\n"
        return _password_line(self.password_hash)

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


def _form_value(request: httpx.Request, key: str) -> str | None:
    """One field out of an `application/x-www-form-urlencoded` body."""
    body = request.content.decode("utf-8")
    for pair in body.split("&"):
        name, _, _value = pair.partition("=")
        if name == key:
            return httpx.QueryParams(pair).get(key)
    return None


def reset_context(
    *,
    vm: FakeProxmoxVM | None = None,
    servers: list[Any] | None = None,
    **kwargs: Any,
) -> tuple[ToolFixture, FakeProxmoxVM]:
    """A tool context whose Proxmox endpoint is reachable only through `vm`.

    The row's `api_token_secret` is real ciphertext under the fixture's own cipher, so the
    production decrypt site runs on the way to every call — a plaintext-shaped literal
    would let the client work with the one thing this path is supposed to exercise removed.
    """
    box = vm or FakeProxmoxVM()
    fixture = build_tool_context(proxmox_transport=box.transport(), **kwargs)
    rows = servers if servers is not None else [proxmox_server(SERVER_NAME)]
    for row in rows:
        row.api_token_secret = fixture.cipher.encrypt_text("proxmox-token-secret")
    fixture.proxmox_servers.servers = list(rows)
    return fixture, box


async def reset(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    node: str = NODE,
    vmid: int = VMID,
    username: str = USERNAME,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The context is not decoration: `open_change_request` reads the requester from the
    authenticated identity rather than from an argument, so a call outside it would be
    asserting against an identity the test planted.
    """
    user, resolved = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await proxmox_reset_vm_password(
            server_ref=server_ref,
            node=node,
            vmid=vmid,
            username=username,
            context=fixture.context,
        )
    return answer, resolved


def execution_request(
    *,
    server_id: UUID | str,
    node: Any = NODE,
    vmid: Any = VMID,
    username: Any = USERNAME,
    reason: str = REASON,
    action_request_id: UUID | None = None,
    server_ref: str = "some-other-endpoint",
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved reset.

    The evidence is what the gate wrote and what the operator saw; the arguments deliberately name
    a `server_ref` the runner must ignore, so every runner test that resolves a target also asserts
    resolution comes from the evidence, never the arguments.
    """
    return ChangeExecutionRequest(
        action_request_id=action_request_id or uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_PROXMOX_RESET_VM_PASSWORD,
        arguments={
            "server_ref": server_ref,
            "node": node,
            "vmid": vmid,
            "username": username,
        },
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_NODE: node,
            EVIDENCE_VMID: vmid,
            EVIDENCE_USERNAME: username,
            EVIDENCE_VM: {
                "name": VM_NAME,
                "ciuser": USERNAME,
                "has_cloudinit_password": True,
                "run_status": "running",
                "unavailable_reads": [],
            },
        },
        reason=reason,
    )


def no_polling_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop both poll intervals to zero.

    The counts stay — a timeout test still makes thirty round trips — so what is removed is
    fifteen seconds of wall clock and nothing about the behaviour under test. Patched on the
    module constants rather than on `asyncio.sleep`, which is shared by everything else running
    in the loop.
    """
    monkeypatch.setattr(proxmox_password_runner, "TASK_POLL_DELAY_SECONDS", 0)
    monkeypatch.setattr(proxmox_password_runner, "VERIFICATION_POLL_DELAY_SECONDS", 0)


def payload_text(payload: Any) -> str:
    """Everything a payload would carry into an audit row, as one string.

    `default=str` for the reason `core.audit.summaries` uses it: this is asserted *against*, so a
    value that would not serialize must still show up rather than raise and leave the assertion
    unmade.
    """
    return json.dumps(payload, default=str)


__all__ = [
    "NODE",
    "OLD_PASSWORD",
    "SALT",
    "SERVER_NAME",
    "UPID",
    "USERNAME",
    "VMID",
    "VM_NAME",
    "FakeProxmoxVM",
    "execution_request",
    "no_polling_delay",
    "payload_text",
    "reset",
    "reset_context",
]
