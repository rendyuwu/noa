"""`proxmox_reset_vm_password` — the call that opens a question.

The tool half: the guards, the in-process preflight, the `ciuser` refusal, the gate response and
the mount. Its runner lives on the far side of the approval boundary and is asserted in
`test_proxmox_reset_password_runner.py`; the two are separate files because together they run
past the line budget, and the split falls on the boundary the design already draws — **nothing
here can change anything**, and nothing there is reachable without an approval.

Driven through the real `open_change_request` inside a real request context, so
`current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
names. The seams are the ones the first CHANGE test lane established: the real resolver, a real
`SecretCipher` with real ciphertext on the row, the real `ProxmoxClient`, the real failure
normalisation, real `sanitize_tool_errors`. Only the HTTP socket is doubled.

**What is new on this side, beyond mirroring the earlier CHANGE lanes.**

1. **The tool must not be able to name a password**, and that is asserted on the *schema* rather
   than on behaviour — the reason rule, held at the schema. A reason parameter and a `new_password`
   parameter are two spellings of the same mistake: a value the LLM authors reaching a CHANGE.
2. **There is no no-op**, unlike the firewall and suspension pairs. A password reset has no
   already-in-that-state
   reading, so every well-formed call opens a card.
3. **A `ciuser` mismatch is refused rather than gated.** The password would be set for `ciuser`
   while the delivered blob names the argument — a login that cannot work, approved as though it
   could.
4. **The preflight names what it could not read.** The run state is the one tolerated
   read, and its absence is a named gap rather than a `null` an operator reads as "not running".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.errors import ChangeReasonForbiddenError
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from core.servers.proxmox_ref import NODE_DESCRIPTION, SERVER_REF_DESCRIPTION
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    APPROVAL_CARD_PATH,
    FORBIDDEN_REASON_KEYS,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
    assert_no_reason_argument,
)
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_password import (
    ERROR_CLOUDINIT_USER_MISMATCH,
    ERROR_INVALID_VMID,
    ERROR_NODE_REQUIRED,
    ERROR_USERNAME_REQUIRED,
    EVIDENCE_NODE,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_USERNAME,
    EVIDENCE_VM,
    EVIDENCE_VMID,
    TOOL_PROXMOX_RESET_VM_PASSWORD,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from support.proxmox_password import (
    NODE,
    SERVER_NAME,
    USERNAME,
    VM_NAME,
    VMID,
    FakeProxmoxVM,
    reset,
    reset_context,
)
from support.servers import EMBED_BASE_URL, SECRETS, proxmox_server

# --- Guards, before any I/O ---


@pytest.mark.parametrize(
    ("node", "username", "vmid", "expected"),
    [
        pytest.param("   ", USERNAME, VMID, ERROR_NODE_REQUIRED, id="blank-node"),
        pytest.param("", USERNAME, VMID, ERROR_NODE_REQUIRED, id="empty-node"),
        pytest.param(NODE, "\t\n", VMID, ERROR_USERNAME_REQUIRED, id="blank-username"),
        pytest.param(NODE, USERNAME, 0, ERROR_INVALID_VMID, id="zero-vmid"),
        pytest.param(NODE, USERNAME, -1, ERROR_INVALID_VMID, id="negative-vmid"),
        pytest.param(NODE, USERNAME, True, ERROR_INVALID_VMID, id="bool-is-not-a-vmid"),
    ],
)
async def test_a_malformed_call_is_refused_before_any_request(
    node: str, username: str, vmid: Any, expected: str
) -> None:
    """A malformed call costs no round trip, and the assertion that matters is `vm.requests == []`.

    A refusal that still made a round trip would mean the guards run after the client is built,
    and a `vmid` of `0` or `True` goes into a URL path. `True` is here because `bool` is an `int`
    in Python and pydantic's `ge=1` would let `True` through as `1`.
    """
    fixture, vm = reset_context()

    answer, _ = await reset(fixture, node=node, username=username, vmid=vmid)

    assert answer["ok"] is False
    assert answer["error_code"] == expected
    assert vm.requests == []
    assert fixture.action_requests.requests == []


async def test_an_unknown_server_is_refused_without_reaching_proxmox() -> None:
    """A reference nobody can resolve is a `host_not_found` result the model can act on."""
    fixture, vm = reset_context()

    answer, _ = await reset(fixture, server_ref="nope")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_not_found"
    assert vm.requests == []


async def test_an_ambiguous_server_returns_choices_rather_than_a_pick() -> None:
    """No guessing on a CHANGE path: a guess resets a password on a machine nobody named."""
    fixture, vm = reset_context(
        servers=[proxmox_server("Pve1"), proxmox_server("pve1")],
    )

    answer, _ = await reset(fixture, server_ref="PVE1")

    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["Pve1", "pve1"]
    assert vm.requests == []


# --- The preflight, in-process ---


async def test_a_well_formed_call_opens_a_pending_request_and_changes_nothing() -> None:
    """The whole point of the CHANGE gate, asserted on both halves.

    The row is `PENDING` and the VM saw only reads. A `POST` or a `PUT` here would mean the tool
    executed the change the operator has not authorised yet.
    """
    fixture, vm = reset_context()

    answer, caller = await reset(fixture)

    created = fixture.action_requests.only
    assert created.tool_name == TOOL_PROXMOX_RESET_VM_PASSWORD
    assert created.requested_by_user_id == caller
    assert created.status is ActionRequestStatus.PENDING
    # Committed, not merely flushed: the verdict reads a row that is durable.
    assert created.committed == 1
    assert isinstance(answer, ToolResult)
    assert vm.config_writes == []


async def test_the_evidence_carries_the_preflight_state(caplog: pytest.LogCaptureFixture) -> None:
    """The card describes the VM the operator is authorising a change to.

    Built by naming fields rather than by sanitizing the config document, so this is also the
    assertion that a Proxmox key nobody anticipated cannot ride along — `cipassword` is in the
    config the preflight read, and it is not here.
    """
    fixture, _ = reset_context()

    await reset(fixture)

    created = fixture.action_requests.only
    evidence = created.approval_context["evidence"]

    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_SERVER_ID] == str(fixture.proxmox_servers.servers[0].id)
    assert evidence[EVIDENCE_NODE] == NODE
    assert evidence[EVIDENCE_VMID] == VMID
    assert evidence[EVIDENCE_USERNAME] == USERNAME
    assert evidence[EVIDENCE_VM] == {
        "name": VM_NAME,
        "ciuser": USERNAME,
        "has_cloudinit_password": True,
        "run_status": "running",
        "unavailable_reads": [],
    }
    assert "cipassword" not in evidence[EVIDENCE_VM]


async def test_the_preflight_reads_are_trimmed_before_they_reach_the_evidence() -> None:
    """A pasted node name is an operator artifact, and the runner reads this back later.

    Untrimmed here, the runner would build a URL with a trailing space in the path.
    """
    fixture, _ = reset_context()

    await reset(fixture, node=f"  {NODE}  ", username=f" {USERNAME}\n")

    created = fixture.action_requests.only
    evidence = created.approval_context["evidence"]
    assert evidence[EVIDENCE_NODE] == NODE
    assert evidence[EVIDENCE_USERNAME] == USERNAME


async def test_a_vm_with_no_cloudinit_user_still_opens_a_card() -> None:
    """Proxmox applies `cipassword` to the image's default account when `ciuser` is unset.

    An ordinary configuration, so it is *not* refused — and the card says `ciuser: null` so the
    operator sees exactly which case they are approving rather than being told nothing.
    """
    fixture, _ = reset_context(vm=FakeProxmoxVM(ciuser=None))

    answer, _ = await reset(fixture)

    assert isinstance(answer, ToolResult)
    created = fixture.action_requests.only
    assert created.approval_context["evidence"][EVIDENCE_VM]["ciuser"] is None


async def test_an_unreadable_run_state_is_named_rather_than_dropped() -> None:
    """The one tolerated read says so when it could not answer.

    An absent `run_status` with nothing beside it reads as "this VM is not running", which is a
    measurement nobody took — and it is the fact that tells the operator whether the new password
    takes effect now or at the next boot.
    """
    fixture, _ = reset_context(vm=FakeProxmoxVM(status_error={"status": 500}))

    answer, _ = await reset(fixture)

    assert isinstance(answer, ToolResult)
    vm_state = fixture.action_requests.only.approval_context["evidence"][EVIDENCE_VM]
    assert vm_state["run_status"] is None
    assert vm_state["unavailable_reads"] == ["run_status"]


@pytest.mark.parametrize(
    "vm",
    [
        pytest.param(FakeProxmoxVM(config_error={"status": 500}), id="config"),
        pytest.param(FakeProxmoxVM(cloudinit_error={"status": 500}), id="cloudinit"),
    ],
)
async def test_a_required_preflight_read_that_fails_refuses_the_change(vm: FakeProxmoxVM) -> None:
    """A card that cannot describe what it is asking about must not be opened.

    Unlike the run state, these two are what the decision rests on — who the cloud-init user is,
    and whether the VM has a cloud-init password at all.
    """
    fixture, _ = reset_context(vm=vm)

    answer, _ = await reset(fixture)

    assert answer["ok"] is False
    assert fixture.action_requests.requests == []


async def test_a_proxmox_permission_failure_keeps_the_code_that_names_the_fix() -> None:
    """An integration code passes through rather than being flattened.

    `permission_denied` sends an administrator to Proxmox's ACLs and `auth_failed` to NOA's
    server row, and those are the only actionable parts of the answer.
    """
    fixture, _ = reset_context(vm=FakeProxmoxVM(config_error={"status": 403}))

    answer, _ = await reset(fixture)

    assert answer["error_code"] == "permission_denied"


# --- The ciuser refusal ---


async def test_a_cloudinit_user_mismatch_is_refused() -> None:
    """The refusal that is this tool's own.

    Setting `cipassword` changes the password of `ciuser`. Asked for `ubuntu` on a VM whose
    cloud-init user is `debian`, the change would work, the operator would approve it, and the
    credential handed to the customer would name an account it does not belong to.
    """
    fixture, vm = reset_context(vm=FakeProxmoxVM(ciuser="debian"))

    answer, _ = await reset(fixture, username="ubuntu")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_CLOUDINIT_USER_MISMATCH
    assert "debian" in answer["message"]
    assert "ubuntu" in answer["message"]
    assert fixture.action_requests.requests == []
    assert vm.config_writes == []


async def test_the_matching_cloudinit_user_is_not_refused() -> None:
    """The negative control: the guard separates rather than refusing everything.

    Without it, a comparison written the wrong way round — or one that refused whenever `ciuser`
    was present — would pass the test above and block every real reset.
    """
    fixture, _ = reset_context(vm=FakeProxmoxVM(ciuser="debian"))

    answer, _ = await reset(fixture, username="debian")

    assert isinstance(answer, ToolResult)
    assert len(fixture.action_requests.requests) == 1


# --- The reason rule, at the schema and not just the behaviour ---


async def test_the_tool_schema_carries_no_reason_parameter() -> None:
    """The reason rule, held at the surface the LLM actually sees.

    The reason is typed by an operator on the card. A parameter of any of these names would ask
    the model to author one, which is the thing the design forbids — and the schema is where a
    model learns what it may say.
    """
    context = reset_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    # `run_middleware=False`: the RBAC gate reads a caller off the request context, and
    # this test is about the *schema* a tool declares, not about who may see it.
    [tool] = [
        tool
        for tool in await server.list_tools(run_middleware=False)
        if tool.name == TOOL_PROXMOX_RESET_VM_PASSWORD
    ]
    properties = tool.parameters["properties"]

    assert set(properties) == {"server_ref", "node", "vmid", "username"}
    assert not FORBIDDEN_REASON_KEYS & set(properties)


async def test_the_tool_schema_carries_no_password_parameter() -> None:
    """The no-password-parameter rule at the same surface, and this is the `noa-old` bug (GH #91)
    restated.

    That repo's reset tool took `new_password` from the model and echoed it back, which put the
    plaintext in the prompt, the model context and the stored transcript. The password is
    generated server-side, so there is no argument for one — and there must not be.
    """
    context = reset_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    # `run_middleware=False`: the RBAC gate reads a caller off the request context, and
    # this test is about the *schema* a tool declares, not about who may see it.
    [tool] = [
        tool
        for tool in await server.list_tools(run_middleware=False)
        if tool.name == TOOL_PROXMOX_RESET_VM_PASSWORD
    ]

    assert not {"password", "new_password", "cipassword"} & set(tool.parameters["properties"])


@pytest.mark.parametrize("tool_name", [TOOL_PROXMOX_RESET_VM_PASSWORD, TOOL_PROXMOX_VM_NIC])
async def test_both_proxmox_tools_publish_the_same_identity_descriptions(tool_name: str) -> None:
    """Both Proxmox tools publish one `server_ref` description and one `node` description.

    **Why the NIC tool is asserted from this file.** Registration is whole — `register_mcp_tools`
    mounts every tool — so the schema of both Proxmox tools is reachable from either lane's
    fixtures, and the property under test is that the two agree. Splitting it across two files
    would give each half its own copy of the expectation, which is the drift this test exists to
    catch.

    **Why identity rather than a substring.** The two modules held byte-identical copies of both
    strings until they moved to `core.servers.proxmox_ref`; a future edit to one file is exactly
    how they would stop agreeing, and a model asking the NIC tool and the password tool for
    different things about the same field is a bug no behaviour test would show.

    **The word `node` is asserted because a description that lost it leaves the mechanism
    unused** — the model would go back to asking the operator which server, for a reference the
    resolver can already work out.

    **What this does not bind: the promise to the mechanism.** A `server_ref` description reworded
    to *forbid* node names would still carry the word `node` and this test would stay green.
    Nothing here can tell a correct clause from an inverted one. What the resolver actually does
    with a node name is bound in `test_proxmox_server_ref.py`, by the section covering a node name
    resolving to the server that runs it; this file binds only that the two schemas agree and that
    the word survives. The gap is stated rather than closed on purpose — closing it means pinning
    a sentence, which turns every reword into a false failure.
    """
    context = reset_context()[0].context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)

    # `run_middleware=False`: the RBAC gate reads a caller off the request context, and
    # this test is about the *schema* a tool declares, not about who may see it.
    [tool] = [
        tool for tool in await server.list_tools(run_middleware=False) if tool.name == tool_name
    ]
    properties = tool.parameters["properties"]
    server_ref_description = properties["server_ref"]["description"]

    assert server_ref_description == SERVER_REF_DESCRIPTION
    assert properties["node"]["description"] == NODE_DESCRIPTION
    assert "node" in server_ref_description
    # The `node` description once ended "and not the `server_ref`", one parameter away from a
    # `server_ref` description that now accepts exactly that. Restoring it would keep both equality
    # assertions green — one string per parameter, still deduplicated — and ship two strings that
    # contradict each other. Pins a parameter name rather than prose, so a reword cannot false-fail.
    assert "server_ref" not in properties["node"]["description"]


async def test_a_reason_shaped_argument_is_refused_at_the_gate() -> None:
    """The same boundary one layer in, for a caller that reaches `open_change_request` directly.

    Asserted here rather than only in `test_mcp_change_gate.py` because this is the tool whose
    arguments a future edit is most likely to widen — a "why is this being reset" field reads as
    helpful right up until it is the LLM writing it.
    """
    with pytest.raises(ChangeReasonForbiddenError):
        assert_no_reason_argument({"server_ref": SERVER_NAME, "reason": "customer asked"})


async def test_the_recorded_arguments_are_what_the_model_asked_for() -> None:
    """The card shows what was requested, and nothing about a password.

    `arguments` goes through the gate's redaction, so this is also the check that a future
    argument named after a credential would be stored `[redacted]` rather than in the clear.
    """
    fixture, _ = reset_context()

    await reset(fixture)

    created = fixture.action_requests.only
    assert created.approval_context["arguments"] == {
        "server_ref": SERVER_NAME,
        "node": NODE,
        "vmid": VMID,
        "username": USERNAME,
    }


# --- The gate response ---


async def test_the_gate_response_carries_the_text_first_then_the_card() -> None:
    """Both halves ship, in that order, asserted on the count.

    The iframe is where an operator decides and the plain address is what remains when the frame
    does not load. "Two blocks, text first" is the claim, because "a resource exists" would pass
    against a result that had lost its link-out.
    """
    fixture, _ = reset_context()

    answer, _ = await reset(fixture)

    assert isinstance(answer, ToolResult)
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)
    assert len(answer.content) == 2


async def test_the_card_url_carries_the_request_id_and_nothing_else() -> None:
    """The tool result persists in LibreChat's MongoDB, so everything in it is readable.

    No node, no VM id, no username, no server name in the URL — the address is a name, and
    authorisation to read the card behind it is the cookie plus the requester-match.
    """
    fixture, _ = reset_context()

    answer, _ = await reset(fixture)

    created = fixture.action_requests.only
    assert isinstance(answer, ToolResult)
    text = answer.content[0]
    assert isinstance(text, TextContent)
    expected = f"{EMBED_BASE_URL}{APPROVAL_CARD_PATH}/{created.action_request_id}"
    assert expected in text.text
    resource = answer.content[1]
    assert isinstance(resource, EmbeddedResource)
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{created.action_request_id}"
    assert resource.resource.mimeType == UI_RESOURCE_MIME_TYPE


async def test_no_operational_detail_reaches_the_transcript() -> None:
    """Not the credentials, and not the VM's state either.

    The evidence is on the row behind the operator's cookie. A gate response that named the VM
    or its cloud-init user would publish the preflight into a transcript a LibreChat
    administrator can read.
    """
    fixture, _ = reset_context()

    answer, _ = await reset(fixture)

    assert isinstance(answer, ToolResult)
    rendered = "".join(block.text for block in answer.content if isinstance(block, TextContent))
    for secret in SECRETS:
        assert secret not in rendered
    assert VM_NAME not in rendered
    assert USERNAME not in rendered


# --- Sanitized failures, and the mount ---


async def test_an_unexpected_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raw exception never reaches the model, and a timeout keeps its own name."""
    from core.integrations.proxmox import client as proxmox_client

    async def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("the node did not answer")

    monkeypatch.setattr(proxmox_client.ProxmoxClient, "get_qemu_config", boom)
    fixture, _ = reset_context()

    answer, _ = await reset(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "the node did not answer" not in answer["message"]


async def test_the_tool_is_registered_as_a_change_in_the_catalog() -> None:
    """The risk is declared at registration, and the name is one grants can name.

    `ToolRisk.CHANGE` is what keeps `ToolRunAuditMiddleware` from writing a `tool_runs` row for a
    call that executed nothing, and what makes the registry demand a runner at startup.
    """
    context = reset_context()[0].context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert registered[TOOL_PROXMOX_RESET_VM_PASSWORD] is ToolRisk.CHANGE
    assert TOOL_PROXMOX_RESET_VM_PASSWORD in TOOL_CATALOG
