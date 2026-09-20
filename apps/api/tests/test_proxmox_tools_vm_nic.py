"""`proxmox_vm_nic` — one tool for two directions.

The tool half: the guards, the enum, the in-process preflight, the two ambiguity refusals, the
no-op, the gate response and the mount. Its runner lives on the far side of the approval boundary
and is asserted in `test_proxmox_nic_runner.py`; the two are separate files because together they
run past the line budget, and the split falls on the boundary the design already draws — **nothing
here can change anything**, and nothing there is reachable without an approval.

Driven through the real `open_change_request` inside a real request context, so
`current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
names. The seams are the ones the account search established: the real resolver, a real
`SecretCipher` with real ciphertext on the row, the real `ProxmoxClient`, the real codec, the real
failure normalisation, real `sanitize_tool_errors`. Only the HTTP socket is doubled.

**What is new on this side, beyond mirroring the earlier tools.**

1. **The enum is the tool** (DECISIONS section 9). One name where `noa-old` had two, and `action`
   is asserted on the *published schema* rather than on behaviour — a model reads the pair of
   words from `tools/list`, and a free-string parameter that happened to work would leave the
   collapse undone where it is visible.
2. **Two ambiguity refusals, and one inference.** A VM with several interfaces and no
   `net` named gets the list; a VM with one gets it chosen, and the card says so.
3. **No digest anywhere on this side.** `noa-old` handed the config digest to the model and took
   it back on the change call. With an approval gate in that window the runner re-reads instead,
   so the evidence carries no token to go stale — asserted, because "we did not add a field" is
   prose until something looks.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    APPROVAL_CARD_PATH,
    FORBIDDEN_REASON_KEYS,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
)
from noa_api.mcp_tools.change_target import STATUS_NO_OP
from noa_api.mcp_tools.proxmox_nic import (
    ACTION_DISABLE,
    ACTION_ENABLE,
    ERROR_INVALID_ACTION,
    ERROR_INVALID_VMID,
    ERROR_NET_NOT_FOUND,
    ERROR_NET_SELECTION_REQUIRED,
    ERROR_NO_NICS_FOUND,
    ERROR_NODE_REQUIRED,
    EVIDENCE_ACTION,
    EVIDENCE_NET,
    EVIDENCE_NIC,
    EVIDENCE_NODE,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_VM,
    EVIDENCE_VMID,
    TOOL_PROXMOX_VM_NIC,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from support.proxmox_nic import (
    NET0,
    NET0_UP,
    NET1,
    NET1_UP,
    NODE,
    SERVER_NAME,
    VM_NAME,
    VMID,
    FakeProxmoxNICVM,
    call_nic,
    nic_context,
)
from support.servers import EMBED_BASE_URL, SECRETS, proxmox_server

TWO_NICS = {NET0: NET0_UP, NET1: NET1_UP}


def evidence_of(fixture: Any) -> dict[str, Any]:
    """The preflight the gate persisted on the one row it opened."""
    [request] = fixture.action_requests.requests
    return request.approval_context["evidence"]


# --- Guards, before any I/O ---


@pytest.mark.parametrize(
    ("node", "vmid", "action", "expected"),
    [
        pytest.param("   ", VMID, ACTION_DISABLE, ERROR_NODE_REQUIRED, id="blank-node"),
        pytest.param("", VMID, ACTION_DISABLE, ERROR_NODE_REQUIRED, id="empty-node"),
        pytest.param(NODE, 0, ACTION_DISABLE, ERROR_INVALID_VMID, id="zero-vmid"),
        pytest.param(NODE, -1, ACTION_DISABLE, ERROR_INVALID_VMID, id="negative-vmid"),
        pytest.param(NODE, True, ACTION_DISABLE, ERROR_INVALID_VMID, id="bool-is-not-a-vmid"),
        pytest.param(NODE, VMID, "DISABLE", ERROR_INVALID_ACTION, id="case-is-not-the-enum"),
        pytest.param(NODE, VMID, "toggle", ERROR_INVALID_ACTION, id="a-third-word"),
        pytest.param(NODE, VMID, "", ERROR_INVALID_ACTION, id="empty-action"),
    ],
)
async def test_a_malformed_call_is_refused_before_any_request(
    node: str, vmid: Any, action: str, expected: str
) -> None:
    """The assertion that matters is `vm.requests == []`.

    A refusal that still made a round trip would mean the guards run after the client is built,
    and a `vmid` of `0` or `True` goes into a URL path. `True` is here because `bool` is an `int`
    in Python and pydantic's `ge=1` would let it through as `1`.

    The three `action` rows are the enum's third place: the schema publishes the
    enum, the body re-checks it for a caller that reaches the function directly, and the runner
    re-checks it after the JSONB round trip.
    """
    fixture, vm = nic_context()

    answer, _ = await call_nic(fixture, node=node, vmid=vmid, action=action)

    assert answer["ok"] is False
    assert answer["error_code"] == expected
    assert vm.requests == []
    assert fixture.action_requests.requests == []


async def test_an_unknown_server_is_refused_without_reaching_proxmox() -> None:
    """A reference nobody can resolve is a `host_not_found` result the model can act on."""
    fixture, vm = nic_context()

    answer, _ = await call_nic(fixture, server_ref="nope")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_not_found"
    assert vm.requests == []


async def test_an_ambiguous_server_returns_choices_rather_than_a_pick() -> None:
    """A tie is `choices`, never a pick — on a CHANGE path a guess cuts a machine nobody named
    off the network."""
    fixture, vm = nic_context(
        servers=[proxmox_server("Pve1"), proxmox_server("pve1")],
    )

    answer, _ = await call_nic(fixture, server_ref="PVE1")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["Pve1", "pve1"]
    assert vm.requests == []


# --- One tool, and the enum is on the published schema ---


async def test_one_tool_carries_the_action_enum_and_the_two_old_names_are_gone() -> None:
    """The DECISIONS section 9 collapse, asserted where a model actually reads it.

    `noa-old` exposed `proxmox_enable_vm_nic` and `proxmox_disable_vm_nic`. The merge is only
    done if the two words are in the *schema* — a free-string `action` that happened to work would
    leave a model guessing at them from prose, and would let a fourth word through to the body
    guard on every call.
    """
    context = nic_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    # `run_middleware=False`: the RBAC gate reads a caller off the request context, and
    # this test is about the *schema* a tool declares, not about who may see it.
    tools = await server.list_tools(run_middleware=False)
    names = {tool.name for tool in tools}
    [tool] = [candidate for candidate in tools if candidate.name == TOOL_PROXMOX_VM_NIC]

    assert "proxmox_enable_vm_nic" not in names
    assert "proxmox_disable_vm_nic" not in names
    assert tool.parameters["properties"]["action"]["enum"] == [ACTION_ENABLE, ACTION_DISABLE]


async def test_the_tool_schema_carries_no_reason_parameter() -> None:
    """The reason boundary at the surface the LLM actually sees.

    The reason is typed by an operator on the card. A parameter of any of these names would ask
    the model to author one, which is the thing the reason rule forbids — and the schema is where
    a model learns what it may say.
    """
    context = nic_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    [tool] = [
        candidate
        for candidate in await server.list_tools(run_middleware=False)
        if candidate.name == TOOL_PROXMOX_VM_NIC
    ]
    properties = tool.parameters["properties"]

    assert set(properties) == {"server_ref", "node", "vmid", "action", "net"}
    assert not FORBIDDEN_REASON_KEYS & set(properties)


async def test_the_tool_schema_carries_no_digest_parameter() -> None:
    """This tool's departure from `noa-old`, asserted rather than described.

    There the digest was a tool argument: a preflight handed it to the model and the change call
    handed it back. With an approval gate in that window the runner re-reads instead, so there is
    nothing for a model to carry — and a `digest` parameter would be a compare-and-set token going
    stale in a transcript.
    """
    context = nic_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    [tool] = [
        candidate
        for candidate in await server.list_tools(run_middleware=False)
        if candidate.name == TOOL_PROXMOX_VM_NIC
    ]

    assert "digest" not in tool.parameters["properties"]


async def test_the_tool_is_registered_as_a_change_in_the_catalog() -> None:
    """The risk is declared at registration, and the name is one grants can name.

    `ToolRisk.CHANGE` is what keeps `ToolRunAuditMiddleware` from writing a `tool_runs` row for a
    call that executed nothing, and what makes the registry demand a runner at startup — which is
    the same call, so a runner missing for this name would raise here rather than in front of an
    operator who has already pressed Approve.
    """
    context = nic_context()[0].context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert registered[TOOL_PROXMOX_VM_NIC] is ToolRisk.CHANGE
    assert TOOL_PROXMOX_VM_NIC in TOOL_CATALOG


async def test_no_nic_preflight_is_exposed_as_a_tool() -> None:
    """The preflight runs in-process inside the CHANGE call, and is not callable."""
    context = nic_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    names = {tool.name for tool in await server.list_tools(run_middleware=False)}

    assert not {name for name in names if "preflight" in name and "nic" in name}
    assert not {name for name in names if "preflight" in name} - TOOL_CATALOG


# --- Which interface ---


async def test_two_nics_and_no_net_named_returns_choices_rather_than_a_pick() -> None:
    """The refusal that keeps NOA from cutting the wrong interface on a coin flip.

    `choices` carries both, so the next call can name one — a refusal that did not say what the
    options were would make the model ask the operator a question NOA could have answered.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets=dict(TWO_NICS)))

    answer, _ = await call_nic(fixture, net=None)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_NET_SELECTION_REQUIRED
    assert [choice["net"] for choice in answer["choices"]] == [NET0, NET1]
    assert vm.config_writes == []
    assert fixture.action_requests.requests == []


async def test_one_nic_is_inferred_and_the_card_says_so() -> None:
    """The negative control for the refusal above: one candidate is not an ambiguity.

    Without this case, "return `choices`" passes just as well as a rule that never picks — and the
    common call becomes a two-turn conversation. `auto_selected` is on the evidence because an
    operator approving a change to a `netN` key they never typed should be able to see it.
    """
    fixture, _ = nic_context()

    answer, _ = await call_nic(fixture, net=None)

    assert isinstance(answer, ToolResult)
    nic = evidence_of(fixture)[EVIDENCE_NIC]
    assert nic["net"] == NET0
    assert nic["auto_selected"] is True


async def test_a_named_nic_is_not_marked_auto_selected() -> None:
    """The other half of the same flag: an operator's own word is not NOA's inference."""
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(nets=dict(TWO_NICS)))

    answer, _ = await call_nic(fixture, net=NET1)

    assert isinstance(answer, ToolResult)
    nic = evidence_of(fixture)[EVIDENCE_NIC]
    assert nic["net"] == NET1
    assert nic["auto_selected"] is False


async def test_a_net_this_vm_does_not_have_lists_the_ones_it_does() -> None:
    """The ambiguity rule again, and the codes are two because the remedies read differently."""
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets=dict(TWO_NICS)))

    answer, _ = await call_nic(fixture, net="net7")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_NET_NOT_FOUND
    assert [choice["net"] for choice in answer["choices"]] == [NET0, NET1]
    assert vm.config_writes == []


async def test_a_vm_with_no_nics_gets_the_emptiest_code_rather_than_an_ambiguity() -> None:
    """`no_nics_found` rather than a `choices` refusal with nothing in it."""
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(nets={}))

    answer, _ = await call_nic(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_NO_NICS_FOUND
    assert "choices" not in answer


# --- The no-op: an answer, not a question ---


@pytest.mark.parametrize(
    ("line", "action"),
    [
        pytest.param(f"{NET0_UP},link_down=1", ACTION_DISABLE, id="already-disabled"),
        pytest.param(NET0_UP, ACTION_ENABLE, id="already-enabled"),
    ],
)
async def test_a_nic_already_in_the_asked_for_state_opens_nothing(line: str, action: str) -> None:
    """The no-op shape the other tools share: there is nothing for an operator to authorise.

    The payload is transcript, so it is built from the server, the VM, the interface and
    one measured boolean — not from the config it was decided from.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET0: line}))

    answer, _ = await call_nic(fixture, action=action)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert answer["net"] == NET0
    assert answer["action"] == action
    assert vm.config_writes == []
    assert fixture.action_requests.requests == []
    # The transcript surface carries no NIC line, MAC or bridge — the identifiers and the verdict.
    assert set(answer) == {
        "ok",
        "status",
        "server",
        "node",
        "vmid",
        "net",
        "action",
        "link_state",
        "message",
    }


@pytest.mark.parametrize(
    ("line", "action"),
    [
        pytest.param(NET0_UP, ACTION_DISABLE, id="up-then-disable"),
        pytest.param(f"{NET0_UP},link_down=1", ACTION_ENABLE, id="down-then-enable"),
    ],
)
async def test_a_nic_that_has_to_move_opens_a_card(line: str, action: str) -> None:
    """The negative control for the no-op above.

    Without it, "answer `no_op`" passes against a tool that never opens a card at all — and the
    approval gate would be gone with every test still green.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET0: line}))

    answer, _ = await call_nic(fixture, action=action)

    assert isinstance(answer, ToolResult)
    assert vm.config_writes == []
    [request] = fixture.action_requests.requests
    assert request.tool_name == TOOL_PROXMOX_VM_NIC
    assert request.status is ActionRequestStatus.PENDING


# --- It opens a question and changes nothing ---


async def test_a_well_formed_call_writes_nothing_and_opens_one_pending_request() -> None:
    """The whole of the opens-a-question rule on this tool, asserted from both sides.

    `vm.config_writes == []` is the half a returned envelope cannot prove: a tool that flipped the
    link and *then* opened a card would answer identically.
    """
    fixture, vm = nic_context()

    answer, caller = await call_nic(fixture)

    assert isinstance(answer, ToolResult)
    assert vm.config_writes == []
    assert vm.link_state() == "up"
    [request] = fixture.action_requests.requests
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == caller


async def test_the_row_carries_the_preflight_the_operator_will_see() -> None:
    """Born in-process, persisted on the row, never rebuilt from a transcript.

    Every interface is on the evidence, not only the one being changed: an operator deciding
    whether to cut a VM off the network needs to know whether it keeps another live one, which is
    the difference between an inconvenience and a machine nobody can reach.
    """
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(nets=dict(TWO_NICS)))

    await call_nic(fixture, net=NET0, action=ACTION_DISABLE)

    evidence = evidence_of(fixture)
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_NODE] == NODE
    assert evidence[EVIDENCE_VMID] == VMID
    assert evidence[EVIDENCE_NET] == NET0
    assert evidence[EVIDENCE_ACTION] == ACTION_DISABLE
    assert evidence[EVIDENCE_NIC]["link_state"] == "up"
    assert evidence[EVIDENCE_NIC]["bridge"] == "vmbr0"
    assert evidence[EVIDENCE_VM]["name"] == VM_NAME
    assert [nic["net"] for nic in evidence[EVIDENCE_VM]["nics"]] == [NET0, NET1]


async def test_the_evidence_carries_no_digest() -> None:
    """The other half of the schema test above: nothing persisted it either.

    A digest on the evidence would be a compare-and-set token an operator's card carried for
    minutes and the runner deliberately ignored — a stale value in front of the next reader, and
    the trap that makes somebody wire it back in.
    """
    fixture, _ = nic_context()

    await call_nic(fixture)

    evidence = evidence_of(fixture)
    assert "digest" not in evidence
    assert "digest" not in evidence[EVIDENCE_NIC]
    assert "digest" not in evidence[EVIDENCE_VM]


async def test_the_arguments_on_the_row_carry_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason rule at the third place it can leak: what the gate recorded, not just what was
    declared."""
    fixture, _ = nic_context()

    await call_nic(fixture)

    [request] = fixture.action_requests.requests
    assert not FORBIDDEN_REASON_KEYS & set(request.approval_context["arguments"])


# --- A read that could not answer is named ---


async def test_an_unreadable_run_state_is_named_rather_than_absent() -> None:
    """Silence is not evidence of absence. A `null` run status reads as "not running"."""
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(status_error={"status": 500}))

    answer, _ = await call_nic(fixture)

    assert isinstance(answer, ToolResult)
    vm_evidence = evidence_of(fixture)[EVIDENCE_VM]
    assert vm_evidence["run_status"] is None
    assert vm_evidence["unavailable_reads"] == ["run_status"]


async def test_a_readable_run_state_names_no_gaps() -> None:
    """The negative control: without it, "everything is unavailable" passes the test above."""
    fixture, _ = nic_context()

    await call_nic(fixture)

    vm_evidence = evidence_of(fixture)[EVIDENCE_VM]
    assert vm_evidence["run_status"] == "running"
    assert vm_evidence["unavailable_reads"] == []


async def test_an_unreadable_config_is_a_refusal_rather_than_a_gap() -> None:
    """The config is what the decision rests on — a card that cannot describe what it is asking
    about is the state the evidence rule exists to prevent."""
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(config_error={"status": 500}))

    answer, _ = await call_nic(fixture)

    assert answer["ok"] is False
    assert fixture.action_requests.requests == []


# --- What a failure looks like in front of a model ---


async def test_an_unexpected_failure_reaches_the_model_as_a_named_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw exception never crosses the boundary, and its text never does either."""
    fixture, _ = nic_context()

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("the node did not answer within 20s at 10.0.0.9")

    monkeypatch.setattr("core.integrations.proxmox.client.ProxmoxClient.get_qemu_config", explode)

    answer, _ = await call_nic(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "10.0.0.9" not in answer["message"]


# --- The gate response ---


async def test_the_gate_answer_is_the_text_block_then_the_ui_resource() -> None:
    """The two branches ship together, asserted on the count and the order.

    The text carries the address plainly because the frame can fail to load and then the URL is
    the only door, and the resource is what LibreChat renders.
    """
    fixture, _ = nic_context()

    answer, _ = await call_nic(fixture)

    assert isinstance(answer, ToolResult)
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)
    assert resource.resource.mimeType == UI_RESOURCE_MIME_TYPE
    assert str(resource.resource.uri).startswith(UI_RESOURCE_URI_PREFIX)


async def test_the_approval_url_carries_the_request_id_and_nothing_else() -> None:
    """This string persists in LibreChat's MongoDB, so everything in it is readable there.

    Taken apart rather than matched by prefix — a query parameter added later would hide on the
    end of a `startswith` and go red here instead.
    """
    fixture, _ = nic_context()

    answer, _ = await call_nic(fixture)

    assert isinstance(answer, ToolResult)
    created = fixture.action_requests.only
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)

    emitted = str(resource.resource.text)
    url = urlparse(emitted)
    assert url.path == f"{APPROVAL_CARD_PATH}/{created.action_request_id}"
    assert url.query == ""
    assert url.fragment == ""
    assert emitted == f"{EMBED_BASE_URL}{APPROVAL_CARD_PATH}/{created.action_request_id}"
    # The plain address rides in the text block too — the door that survives an iframe that did
    # not load.
    assert emitted in text.text
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{created.action_request_id}"


async def test_no_secret_from_the_server_row_reaches_the_answer() -> None:
    """No credential reaches the surface that becomes a transcript."""
    fixture, _ = nic_context()

    answer, _ = await call_nic(fixture)

    assert isinstance(answer, ToolResult)
    rendered = "".join(block.text for block in answer.content if isinstance(block, TextContent))
    for secret in SECRETS:
        assert secret not in rendered
