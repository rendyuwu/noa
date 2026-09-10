"""`pmg_whitelist` — one tool for two directions.

The tool half: the guards, the enum, the in-process preflight, the two no-ops, the gate response
and the mount. Its runner lives on the far side of the cookie/CSRF boundary and is asserted in
`test_pmg_whitelist_runner.py`; the two are separate files because together they run past the
file-size cap, and the split falls on the boundary the design already draws — **nothing here can
change anything**, and nothing there is reachable without an approval.

Driven through the real `open_change_request` inside a real request context, so
`current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
names. The seams are the account search's: the real resolver, a real `SecretCipher` with real
ciphertext on the row, the real `pmgsh` command composition, the real `mynetworks` parser, real
`sanitize_tool_errors`. Only the SSH socket is doubled.

**What is new on this side, beyond mirroring the earlier CHANGE tools.**

1. **The enum is the tool** (DECISIONS section 9). One name where `noa-old` had two, and `action` is
   asserted on the *published schema* rather than on behaviour — a model reads the pair of words
   from `tools/list`, and a free-string parameter that happened to work would leave the collapse
   undone where it is visible.
2. **The exact-membership rule bites harder than it does on the search tool.** A masked target is a
   request about a
   whole network, and this one *writes* it. So the two spellings are asserted together — on the
   card, in the no-op sentence and in the answer — because either alone is a different claim.
3. **Two no-ops, one per direction.** `add` against an address already there and `remove` against
   one that is not both have nothing to authorise, and both must leave the whitelist and the
   `action_requests` table untouched.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.errors import ChangeReasonForbiddenError
from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    APPROVAL_CARD_PATH,
    FORBIDDEN_REASON_KEYS,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
    assert_no_reason_argument,
)
from noa_api.mcp_tools.change_target import STATUS_NO_OP
from noa_api.mcp_tools.pmg_read import ERROR_INVALID_TARGET, ERROR_TARGET_REQUIRED
from noa_api.mcp_tools.pmg_whitelist import (
    ACTION_ADD,
    ACTION_REMOVE,
    ERROR_INVALID_ACTION,
    EVIDENCE_ACTION,
    EVIDENCE_ENDPOINT,
    EVIDENCE_MATCHES,
    EVIDENCE_NORMALIZED_TARGET,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    EVIDENCE_TOTAL_ENTRIES,
    TOOL_PMG_WHITELIST,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from support.pmg import (
    BYSTANDER,
    SERVER_NAME,
    TARGET,
    TARGET_NORMALIZED,
    FakePMGWhitelist,
    call_whitelist,
    whitelist_change_context,
    whitelist_server,
)
from support.remote_exec import command_result
from support.secrets import build_cipher
from support.servers import EMBED_BASE_URL, SECRETS

# A target whose host bits are set. `ipaddress` masks them, so this is a question — and, here, a
# write — about the network, which is the whole of the exact-membership warning on a CHANGE.
MASKED_TARGET = "203.0.113.10/24"
MASKED_NETWORK = "203.0.113.0/24"


def evidence_of(fixture: Any) -> dict[str, Any]:
    """The preflight the gate persisted on the one row it opened."""
    [request] = fixture.action_requests.requests
    return request.approval_context["evidence"]


def registered_tool(fixture: Any) -> Any:
    """This tool as `tools/list` publishes it.

    `run_middleware=False`: the RBAC gate reads a caller off the request context, and every
    test using this is about the *schema* a tool declares, not about who may see it.
    """
    server = build_mcp_server(tool_context=fixture.context)
    register_mcp_tools(server, context=fixture.context)
    return server


# --- Guards, before any I/O ---


@pytest.mark.parametrize(
    ("action", "target", "expected"),
    [
        pytest.param(ACTION_ADD, "   ", ERROR_TARGET_REQUIRED, id="whitespace-target"),
        pytest.param(ACTION_ADD, "", ERROR_TARGET_REQUIRED, id="empty-target"),
        pytest.param(ACTION_ADD, "mail.example.com", ERROR_INVALID_TARGET, id="hostname"),
        pytest.param(ACTION_REMOVE, "203.0.113", ERROR_INVALID_TARGET, id="not-an-address"),
        pytest.param(ACTION_ADD, "203.0.113.10/99", ERROR_INVALID_TARGET, id="impossible-prefix"),
        pytest.param("ADD", TARGET, ERROR_INVALID_ACTION, id="case-is-not-the-enum"),
        pytest.param("delete", TARGET, ERROR_INVALID_ACTION, id="a-third-word"),
        pytest.param("", TARGET, ERROR_INVALID_ACTION, id="empty-action"),
    ],
)
async def test_a_malformed_call_is_refused_before_any_command(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    target: str,
    expected: str,
) -> None:
    """A refusal the schema cannot express, and it costs no round trip.

    `min_length` counts whitespace, so a blank target has to be caught here; a hostname is refused
    rather than resolved, because `mynetworks` holds CIDRs and DNS would decide what this call was
    about; and the enum is re-checked in the body for the caller that reaches the function
    directly, bypassing pydantic (the three-place discipline).

    `box.commands == []` is the half a returned envelope cannot prove.
    """
    fixture, box = whitelist_change_context(monkeypatch)

    answer, _ = await call_whitelist(fixture, action=action, target=target)

    assert answer["ok"] is False
    assert answer["error_code"] == expected
    assert box.commands == []
    assert fixture.action_requests.requests == []


async def test_an_unknown_server_is_refused_without_reaching_pmg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution comes first; a name nobody has means no SSH hop and no card."""
    fixture, box = whitelist_change_context(monkeypatch)

    answer, _ = await call_whitelist(fixture, server_ref="pmg-nowhere")

    assert answer["ok"] is False
    assert box.commands == []
    assert fixture.action_requests.requests == []


async def test_an_ambiguous_server_returns_choices_rather_than_a_pick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tie is `choices`, never a guess — whitelisting the wrong gateway is a change
    to a machine nobody named."""
    cipher = build_cipher()
    shared = "gw.example.com"
    fixture, box = whitelist_change_context(
        monkeypatch,
        cipher=cipher,
        servers=[
            whitelist_server("pmg-edge-a", cipher=cipher, ssh_host=shared),
            whitelist_server("pmg-edge-b", cipher=cipher, ssh_host=shared),
        ],
    )

    answer, _ = await call_whitelist(fixture, server_ref=shared)

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert len(answer["choices"]) == 2
    assert box.commands == []


# --- DECISIONS section 9: one tool, and the enum is on the published schema ---


async def test_one_tool_carries_the_action_enum_and_the_two_old_names_are_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DECISIONS section 9's second collapse, asserted where a model actually reads it.

    `noa-old` exposed `pmg_whitelist_add` and `pmg_whitelist_remove`. The merge is only done if
    the two words are in the *schema* — a free-string `action` that happened to work would leave a
    model guessing at them from prose, and would let a third word through to the body guard on
    every call.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    server = registered_tool(fixture)

    tools = await server.list_tools(run_middleware=False)
    names = {tool.name for tool in tools}
    [tool] = [candidate for candidate in tools if candidate.name == TOOL_PMG_WHITELIST]

    assert "pmg_whitelist_add" not in names
    assert "pmg_whitelist_remove" not in names
    assert tool.parameters["properties"]["action"]["enum"] == [ACTION_ADD, ACTION_REMOVE]


async def test_the_tool_schema_carries_no_reason_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason boundary at the surface the LLM actually sees.

    The reason is typed by an operator on the card. A parameter of any of these names would ask the
    model to author one, which is the thing the reason rule forbids — and the schema is where a
    model learns what it may say.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    server = registered_tool(fixture)

    [tool] = [
        candidate
        for candidate in await server.list_tools(run_middleware=False)
        if candidate.name == TOOL_PMG_WHITELIST
    ]
    properties = tool.parameters["properties"]

    assert set(properties) == {"server_ref", "action", "target"}
    assert not FORBIDDEN_REASON_KEYS & set(properties)


async def test_a_reason_shaped_argument_is_refused_at_the_gate() -> None:
    """The same boundary one layer in, for a caller that reaches `open_change_request` directly."""
    with pytest.raises(ChangeReasonForbiddenError):
        assert_no_reason_argument(
            {"server_ref": SERVER_NAME, "action": ACTION_ADD, "reason": "customer asked"}
        )


async def test_the_tool_is_registered_as_a_change_in_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The risk is declared at registration, and the name is one grants can name.

    `ToolRisk.CHANGE` is what keeps `ToolRunAuditMiddleware` from writing a `tool_runs` row for a
    call that executed nothing, and what makes the registry demand a runner at startup — which is
    the same call, so a runner missing for this name would raise here rather than in front of an
    operator who has already pressed Approve.
    """
    fixture, _ = whitelist_change_context(monkeypatch)

    registered = register_mcp_tools(
        build_mcp_server(tool_context=fixture.context), context=fixture.context
    )

    assert registered[TOOL_PMG_WHITELIST] is ToolRisk.CHANGE
    assert TOOL_PMG_WHITELIST in TOOL_CATALOG


async def test_no_whitelist_preflight_is_exposed_as_a_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One workflow, one tool: the preflight runs in-process inside the CHANGE call, and is not
    callable.

    `read_pmg_mynetworks` is shared with the two READ tools and is not registered by any of the
    three — the discovery surface a model gets is `pmg_whitelist_search`, which answers a
    membership question rather than handing back a gate's before-state.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    server = registered_tool(fixture)

    names = {tool.name for tool in await server.list_tools(run_middleware=False)}

    assert "read_pmg_mynetworks" not in names
    assert not {name for name in names if "preflight" in name} - TOOL_CATALOG


# --- The no-op: an answer, not a question ---


@pytest.mark.parametrize(
    ("action", "entries"),
    [
        pytest.param(ACTION_ADD, [BYSTANDER, TARGET], id="add-a-bare-host-already-there"),
        pytest.param(ACTION_ADD, [TARGET_NORMALIZED], id="add-the-same-address-spelled-32"),
        pytest.param(ACTION_REMOVE, [BYSTANDER], id="remove-something-that-is-not-there"),
        pytest.param(ACTION_REMOVE, [], id="remove-from-an-empty-whitelist"),
    ],
)
async def test_a_whitelist_already_in_the_asked_for_state_opens_nothing(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    entries: list[str],
) -> None:
    """There is nothing for an operator to authorise, so no card is opened — the earlier CHANGE
    tools' shape.

    The `/32` case is the exact-membership rule in the branch that decides whether anything happens
    at all: an operator who typed `203.0.113.10` against a whitelist holding `203.0.113.10/32` is
    asking about one entry, and comparing raw strings would open a card to add a line that is there.
    """
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=entries))

    answer, _ = await call_whitelist(fixture, action=action, target=TARGET)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert answer["exists"] is (action == ACTION_ADD)
    assert fixture.action_requests.requests == []
    assert box.mutations == []


async def test_the_no_op_answer_is_built_from_facts_and_not_from_pmgsh_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This answer is a plain tool result, so it lands in the transcript LibreChat keeps.

    Built from the node's name, the two spellings of the target and one measured boolean — the
    allowlist-remove tool's rule one system over. A `pmgsh` line, an id column or a neighbouring
    entry appearing here would be the whitelist of a mail gateway sitting in a conversation nobody
    bounded.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET])
    )

    answer, _ = await call_whitelist(fixture, action=ACTION_ADD, target=TARGET)

    assert answer["server"] == SERVER_NAME
    assert answer["target"] == TARGET
    assert answer["normalized_target"] == TARGET_NORMALIZED
    assert answer["total_entries"] == 2
    assert BYSTANDER not in str(answer)
    assert "200 OK" not in str(answer)
    assert "id cidr" not in str(answer)


async def test_a_masked_no_op_names_the_network_rather_than_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact membership: `203.0.113.10/24` is a question about `203.0.113.0/24`.

    Telling an operator that the address they typed is "already whitelisted" when what is
    whitelisted is its whole network is the echo this rule exists to stop — so the sentence names
    the normalised form and the payload carries both.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[MASKED_NETWORK])
    )

    answer, _ = await call_whitelist(fixture, action=ACTION_ADD, target=MASKED_TARGET)

    assert answer["status"] == STATUS_NO_OP
    assert answer["target"] == MASKED_TARGET
    assert answer["normalized_target"] == MASKED_NETWORK
    assert MASKED_NETWORK in answer["message"]


async def test_a_containing_network_is_not_a_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact-membership rule's other half, and the negative control for the no-op above.

    `203.0.113.0/24` in `mynetworks` does not make `203.0.113.10` an entry. Answering otherwise
    would tell an operator their address is whitelisted when the CIDR a removal has to name is a
    different one — and here it would also skip a card for a change that really is a change.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[MASKED_NETWORK])
    )

    answer, _ = await call_whitelist(fixture, action=ACTION_ADD, target=TARGET)

    assert isinstance(answer, ToolResult)
    assert evidence_of(fixture)[EVIDENCE_MATCHES] == []


# --- It opens a question and changes nothing ---


@pytest.mark.parametrize(
    ("action", "entries"),
    [
        pytest.param(ACTION_ADD, [BYSTANDER], id="add-something-new"),
        pytest.param(ACTION_REMOVE, [BYSTANDER, TARGET], id="remove-something-there"),
    ],
)
async def test_a_well_formed_call_writes_nothing_and_opens_one_pending_request(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    entries: list[str],
) -> None:
    """The whole of "READ now, CHANGE through the gate" on this tool, asserted from both sides.

    `box.mutations == []` is the half a returned envelope cannot prove: a tool that edited
    `mynetworks` and *then* opened a card would answer identically. The whitelist is compared
    before and after for the same reason, and `pmgconfig sync` never running is what says the
    change did not reach Postfix either.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=list(entries))
    )

    answer, caller = await call_whitelist(fixture, action=action, target=TARGET)

    assert isinstance(answer, ToolResult)
    assert box.mutations == []
    assert box.synced == 0
    assert box.entries == entries
    [request] = fixture.action_requests.requests
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == caller
    assert request.tool_name == TOOL_PMG_WHITELIST


async def test_the_row_carries_the_preflight_the_operator_will_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Born in-process, persisted on the row, never rebuilt from a transcript.

    Both spellings of the target and every matching line in PMG's own spelling, because those
    lines are exactly what the runner will delete — a card that showed one of two would understate
    the change. `total_entries` is the search tool's argument one surface over: "one entry to
    remove" out of two hundred is a different picture from one out of one.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET, TARGET_NORMALIZED])
    )

    await call_whitelist(fixture, action=ACTION_REMOVE, target=TARGET)

    evidence = evidence_of(fixture)
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_ACTION] == ACTION_REMOVE
    assert evidence[EVIDENCE_TARGET] == TARGET
    assert evidence[EVIDENCE_NORMALIZED_TARGET] == TARGET_NORMALIZED
    assert evidence[EVIDENCE_MATCHES] == [
        {"cidr": TARGET, "normalized": TARGET_NORMALIZED},
        {"cidr": TARGET_NORMALIZED, "normalized": TARGET_NORMALIZED},
    ]
    assert evidence[EVIDENCE_TOTAL_ENTRIES] == 3
    assert evidence[EVIDENCE_ENDPOINT] == MYNETWORKS_PATH


async def test_the_evidence_carries_no_raw_command_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structure with nowhere to put the whole whitelist cannot leak it.

    The card describes one address on one node. `pmgsh ls` prints every network this gateway
    relays for, and persisting that on the row would put it in front of every later reader of the
    request for the sake of a change about one line.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, "192.0.2.0/24"])
    )

    await call_whitelist(fixture, action=ACTION_ADD, target=TARGET)

    evidence = evidence_of(fixture)
    assert BYSTANDER not in str(evidence)
    assert "192.0.2.0/24" not in str(evidence)
    assert "200 OK" not in str(evidence)


async def test_the_arguments_on_the_row_carry_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason boundary at the third place it can leak: what the gate recorded, not just what was
    declared.
    """
    fixture, _ = whitelist_change_context(monkeypatch)

    await call_whitelist(fixture)

    [request] = fixture.action_requests.requests
    assert not FORBIDDEN_REASON_KEYS & set(request.approval_context["arguments"])


# --- What a failure looks like in front of a model ---


async def test_an_unreadable_whitelist_is_a_refusal_rather_than_a_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One source, so there is no partial case — a read that cannot answer is fail-closed.

    The integration's own code travels: `pmgsh_command_failed` and `ssh_sudo_required` name
    different remedies (`noa-old` GH #82), and a card describing a before-state NOA could not read
    is the state the provenance rule exists to prevent.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            list_error=command_result(exit_code=1, stderr="pmgsh: connection refused")
        ),
    )

    answer, _ = await call_whitelist(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == "pmgsh_command_failed"
    assert fixture.action_requests.requests == []


async def test_an_unexpected_failure_reaches_the_model_as_a_named_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw exception never crosses the boundary, and its text never does either."""
    fixture, _ = whitelist_change_context(monkeypatch)

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("pmg1 did not answer within 20s at 10.0.0.9")

    monkeypatch.setattr(
        "noa_api.mcp_tools.pmg_read.run_pmg_mynetworks_list",
        explode,
    )

    answer, _ = await call_whitelist(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "10.0.0.9" not in answer["message"]


# --- The gate response ---


async def test_the_gate_answer_is_the_text_block_then_the_ui_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two branches ship together, asserted on the count and the order.

    The text carries the address plainly because the frame can fail to load and then the URL is
    the only door, and the resource is what LibreChat renders.
    """
    fixture, _ = whitelist_change_context(monkeypatch)

    answer, _ = await call_whitelist(fixture)

    assert isinstance(answer, ToolResult)
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)
    assert resource.resource.mimeType == UI_RESOURCE_MIME_TYPE
    assert str(resource.resource.uri).startswith(UI_RESOURCE_URI_PREFIX)


async def test_the_approval_url_carries_the_request_id_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id-only URL: this string persists in LibreChat's MongoDB, so everything in it is readable
    there.

    Taken apart rather than matched by prefix — a query parameter added later would hide on the
    end of a `startswith` and go red here instead.
    """
    fixture, _ = whitelist_change_context(monkeypatch)

    answer, _ = await call_whitelist(fixture)

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


async def test_no_secret_from_the_server_row_reaches_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error envelope on the surface that becomes a transcript."""
    fixture, _ = whitelist_change_context(monkeypatch)

    answer, _ = await call_whitelist(fixture)

    assert isinstance(answer, ToolResult)
    rendered = "".join(block.text for block in answer.content if isinstance(block, TextContent))
    for secret in SECRETS:
        assert secret not in rendered
