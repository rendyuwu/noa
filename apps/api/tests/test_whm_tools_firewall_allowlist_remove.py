"""`whm_firewall_allowlist_remove` — the call that opens a question.

The tool half: the guards, the in-process preflight, the no-op, the gate response and the mount.
Its runner lives on the far side of the cookie/CSRF boundary and is asserted in
`test_whm_firewall_allowlist_runner.py`; the two are separate files because together they run
past the file-size cap, and the split falls on the boundary the design already draws — nothing
here can change anything, and nothing there is reachable without an approval.

Driven through the real `open_change_request` inside a real request context, so
`current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
names. Seams are the preflight read's and the release-and-allow tool's: the real resolver, a real
`SecretCipher`, real CSF and Imunify parsing, real command composition, real
`sanitize_tool_errors`. Only the SSH socket is doubled.

**What is new on this side, beyond mirroring the release-and-allow tool.**

1. **There is a no-op, and the release-and-allow tool's reason for having none is why.** Its
   allow entry carries a TTL, so a repeat always moves the expiry; a removal has no such
   property, and an address with no allow entry anywhere is already in the state this change
   would produce.
2. **The no-op is gated on a full answer.** "There is nothing to remove" is a claim about
   absence, and a backend that stayed silent has not made it — so one unanswered backend opens
   the card instead.
3. **The no-op answer is a transcript surface.** It is a plain tool result rather
   than a gate response, and it is decided from evidence lines that carry the marker and reason
   the release-and-allow tool wrote onto the entry this call is about. So it is built from the
   server name, the address and one measured boolean, and nothing else.

The default box in `release_context` reads *clean*, which is the no-op state here — so every
test that expects a card to open passes `_allowlisted_box()` explicitly. That is deliberate:
a shared default that opened a request would hide which state each case is actually about.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ActionRequestStatus, ToolRisk
from core.integrations.whm.firewall_gate import ERROR_NO_FIREWALL_BACKEND
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.change_gate import (
    APPROVAL_CARD_PATH,
    FORBIDDEN_REASON_KEYS,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
)
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.results import ERROR_TIMEOUT
from noa_api.mcp_tools.whm_firewall import ERROR_INVALID_TARGET, ERROR_TARGET_REQUIRED
from noa_api.mcp_tools.whm_firewall_allowlist import (
    EVIDENCE_FIREWALL,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    STATUS_NO_OP,
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
)
from support.action_decisions import REASON
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.remote_exec import install_fake_ssh_exec
from support.secrets import build_cipher
from support.servers import EMBED_BASE_URL, SECRETS, build_tool_context
from support.whm_firewall import (
    CSF_ALLOW_AND_DENY_OUTPUT,
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    CSF_READ,
    IMUNIFY_CLEAN,
    IMUNIFY_WHITE,
    SERVER_NAME,
    SSH_PASSWORD_PLAINTEXT,
    SSH_PRIVATE_KEY_PLAINTEXT,
    TARGET,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    csf_step,
    imunify_answer,
    imunify_backend,
    preflight_server,
)
from support.whm_firewall_change import allowlist_remove, changes, csf_commands, release_context

# The address is on both allow lists: the state an operator asks for a removal from, and the one
# every test below that expects a card to open needs.
#
# The id is an *earlier* approval's, and that is the point rather than a detail. This tool takes no
# reason and opens its own row; the reason csf echoes back on this entry was typed at the approval
# that created the entry, for a decision nobody is being asked to make again. `NOA_ALLOW_LINE_CUT`
# is what survives `BackendLookup`'s cut — the marker, which is the address of the row where that
# reason stays readable behind the operator's own cookie.
EARLIER_APPROVAL = "d3b07384-d9a0-4f1e-8e2b-5a6c7d8e9f01"
NOA_ALLOW_LINE = f"{CSF_ALLOW_LINE} noa:{EARLIER_APPROVAL} {REASON}"
NOA_ALLOW_LINE_CUT = f"{CSF_ALLOW_LINE} noa:{EARLIER_APPROVAL}"


def _allowlisted_box(*, csf: str = CSF_ALLOW_LINE, imunify: str = IMUNIFY_WHITE) -> FakeFirewallBox:
    """A box whose preflight says the address is allowed, so there is something to remove."""
    return FakeFirewallBox(
        csf=csf_backend(csf_answer(csf)),
        imunify=imunify_backend(imunify_answer(imunify)),
    )


# --------------------------------------------------------------------------------------
# READ now, CHANGE through the gate: the call opens a question and changes nothing
# --------------------------------------------------------------------------------------


async def test_a_removal_call_opens_a_pending_request_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole of "CHANGE goes through the gate" in one assertion pair: a row exists, and no
    firewall was written to.

    The second half is the one that matters and it is counted rather than inferred — the call
    *does* reach the server, for its preflight, so "no SSH happened" would be false and "the
    payload said pending" would pass against a tool that removed the entry and then said so.
    """
    fixture, fake = release_context(monkeypatch, box=_allowlisted_box())

    answer, user_id = await allowlist_remove(fixture)

    request = fixture.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.tool_name == TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE
    assert request.requested_by_user_id == user_id
    assert fixture.action_requests.commits == [ActionRequestStatus.PENDING.value]
    assert isinstance(answer, ToolResult)
    # One read per backend and nothing else: no `-tra`, no `-ar`, no Imunify delete.
    assert [csf_step(command) for command in csf_commands(fake)] == [CSF_READ]
    assert all("delete" not in command for command in changes(fake))


async def test_the_preflight_runs_inside_the_call_and_lands_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One call, evidence born in it, persisted for the card.

    The before-state is the allow entry the operator is deciding to delete, shown with NOA's
    marker on it and the reason behind that marker cut away. The card is the operator's own
    surface, but the reason on this line is not this decision's — it was typed at the approval
    that created the entry, so rendering it here would carry one operator's words onto another
    operator's card and into whatever they copy off it.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box(csf=NOA_ALLOW_LINE))

    await allowlist_remove(fixture)

    evidence = fixture.action_requests.only.approval_context["evidence"]
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_SERVER_ID] == str(fixture.servers.servers[0].id)
    assert evidence[EVIDENCE_TARGET] == TARGET

    firewall = evidence[EVIDENCE_FIREWALL]
    assert firewall["combined_verdict"] == "allowlisted"
    assert firewall["available_backends"] == {"csf": True, "imunify": True}
    assert firewall["unanswered_backends"] == []
    assert NOA_ALLOW_LINE_CUT in firewall["matches"]
    assert REASON not in " ".join(firewall["matches"])
    # Row-cap rule: the evidence carries its own bound, on the card as much as in a tool result.
    assert firewall["total_matches"] == len(firewall["matches"])
    assert firewall["truncated"] is False


async def test_the_recorded_arguments_are_the_two_the_schema_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row records what was asked for, and no reason is among it.

    Asserted on the keys rather than on the absence of one name, so a future argument cannot
    arrive here unnoticed — and cross-checked against `FORBIDDEN_REASON_KEYS` so the claim is
    about the whole family of spellings the gate refuses.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box())

    await allowlist_remove(fixture)

    arguments = fixture.action_requests.only.approval_context["arguments"]
    assert set(arguments) == {"server_ref", "target"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(arguments)


# --------------------------------------------------------------------------------------
# The no-op, and the bound on it
# --------------------------------------------------------------------------------------


async def test_an_address_on_no_allow_list_is_answered_rather_than_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The suspend and unsuspend tools' rule, and the case the release-and-allow tool does not
    have.

    There is nothing to remove, so there is nothing for an operator to authorise. No
    `action_requests` row is written and no firewall command is sent — the only trace is a
    structured log line, which is the right amount for a call that changed nothing.
    """
    fixture, fake = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_CLEAN)),
        ),
    )

    answer, _ = await allowlist_remove(fixture)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert answer["allowlisted"] is False
    assert answer["target"] == TARGET
    assert answer["server"] == SERVER_NAME
    assert fixture.action_requests.requests == []
    # The reads happened; nothing after them did.
    assert [csf_step(command) for command in csf_commands(fake)] == [CSF_READ]


async def test_an_address_blocked_but_not_allowed_is_also_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`not_found` is not the only state with nothing to remove.

    A denied address has no allow entry either, and the question this tool asks is only about
    the allow lists. Without this case, the no-op would fire on exactly one verdict and an
    operator would be asked to authorise a removal of nothing on every blocked address.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_DENY_LINE)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_CLEAN)),
        ),
    )

    answer, _ = await allowlist_remove(fixture)

    assert answer["status"] == STATUS_NO_OP
    assert fixture.action_requests.requests == []


async def test_an_allow_entry_a_block_outranks_still_opens_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-op's negative control, and the reason `allow_entry` exists.

    The combined verdict here is `blocked`, because both backends resolve a conflict block-first
    — and there *is* an allow entry to remove. A no-op decided from the verdict would refuse to
    open a card for the one case where an operator most obviously has something to delete.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box(csf=CSF_ALLOW_AND_DENY_OUTPUT))

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)
    assert fixture.action_requests.only.status is ActionRequestStatus.PENDING
    assert (
        fixture.action_requests.only.approval_context["evidence"][EVIDENCE_FIREWALL][
            "combined_verdict"
        ]
        == "blocked"
    )


async def test_a_silent_backend_opens_a_request_rather_than_claiming_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-op is a claim about absence, and one backend did not make it.

    CSF says the address is clean; Imunify's read is unreadable. "There is nothing to remove" is
    then a statement about a list nobody read, and answering it would leave an entry in place on
    the backend that stayed silent — with no card, no run and no record. So the question opens,
    and the card names the backend that did not answer.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer("not json at all")),
        ),
    )

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)
    assert fixture.action_requests.only.status is ActionRequestStatus.PENDING
    assert fixture.action_requests.only.approval_context["evidence"][EVIDENCE_FIREWALL][
        "unanswered_backends"
    ] == ["imunify"]


async def test_a_backend_that_is_not_installed_does_not_block_the_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the case above — a negative control proving the distinction still
    separates, and the no-op's own bound.

    One backend *installed* is not one backend silent. A box without Imunify is answered in full
    by CSF — the missing backend is never asked, so it is not in `lookups` and not unanswered —
    and treating it as an unanswered one would open a card for nothing on every CSF-only server,
    which is most of them.
    """
    fixture, _ = release_context(
        monkeypatch, box=FakeFirewallBox(csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)))
    )

    answer, _ = await allowlist_remove(fixture)

    assert answer["status"] == STATUS_NO_OP
    assert fixture.action_requests.requests == []


async def test_the_no_op_answer_carries_no_evidence_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The door this tool opens that the release-and-allow tool did not have.

     A no-op answer is a plain tool result — it lands in LibreChat's MongoDB and in front of the
     model — and it is decided from `csf -g` output that, on the entries the release-and-allow tool
    wrote, carries the
     operator's reason behind NOA's marker. So it is built from the server name, the address and
     the boolean NOA measured, never from the lines it was decided from.

     Driven with an allow line for a *different* address, so the read matches nothing for this
     target and the answer is still a no-op while the leaky text is genuinely in front of the code
     under test. Asserting on a clean read would prove nothing.
    """
    other = f"Found 198.51.100.7 in /etc/csf/csf.allow noa:{EARLIER_APPROVAL} {REASON}"
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(f"{CSF_CLEAN_OUTPUT}\n{other}")),
            imunify=imunify_backend(imunify_answer(IMUNIFY_CLEAN)),
        ),
    )

    answer, _ = await allowlist_remove(fixture)

    rendered = str(answer)
    assert answer["status"] == STATUS_NO_OP
    assert REASON not in rendered
    assert "csf.allow" not in rendered


# --------------------------------------------------------------------------------------
# Two blocks always, text beside the frame: what the model is handed
# --------------------------------------------------------------------------------------


async def test_the_result_carries_the_card_address_and_the_iframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two blocks always, text first, and the plain address inside the text.

    Asserted on the count and the order because that is what the shared response shape claims —
    "both, never one" — and
    a test that only looked for a resource would pass on a result with no address in it, which is
    the case where the frame fails to load and the operator has no door.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box())

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)
    text, resource = answer.content
    assert isinstance(text, TextContent)
    assert isinstance(resource, EmbeddedResource)

    action_request_id = fixture.action_requests.only.action_request_id
    assert f"{EMBED_BASE_URL}{APPROVAL_CARD_PATH}/{action_request_id}" in text.text
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{action_request_id}"
    assert resource.resource.mimeType == UI_RESOURCE_MIME_TYPE


async def test_no_evidence_line_reaches_the_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate response carries the card's address and nothing about the reading.

    The before-state is on the row, behind the operator's cookie — and here it holds the reason
    NOA wrote onto the entry, so publishing it to LibreChat's MongoDB would be the one reason
    field the LLM never sees, reaching the model through a tool result.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box(csf=NOA_ALLOW_LINE))

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)
    serialized = answer.model_dump_json()
    assert CSF_ALLOW_LINE not in serialized
    assert REASON not in serialized


async def test_the_tool_schema_carries_no_reason_parameter() -> None:
    """The boundary is on the schema, so the schema is where it is asserted.

    Read off the registered server rather than off the function signature: what the reason
    boundary bounds is what
    the model is *told* it may send, and that is what `tools/list` publishes.
    """
    tools = await _published_tools()
    schema = tools[TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE].parameters
    properties = set(schema["properties"])

    assert properties == {"server_ref", "target"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(properties)
    assert set(schema["required"]) == {"server_ref", "target"}


def test_the_tool_is_catalogued_and_classified_as_a_change() -> None:
    """A name outside the catalog is a capability no role can be granted, and a CHANGE
    that registered as a READ would have the audit middleware write a row for a change that has
    not happened."""
    context = _tool_context()
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE in TOOL_CATALOG
    assert registered[TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE] is ToolRisk.CHANGE


# --------------------------------------------------------------------------------------
# The guards, all of them before a row is written
# --------------------------------------------------------------------------------------


async def test_a_blank_target_is_refused_before_any_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is placed where the schema cannot reach: `min_length` counts
    whitespace, so `"  "` would otherwise be carried to a server."""
    fixture, fake = release_context(monkeypatch, box=_allowlisted_box())

    answer, _ = await allowlist_remove(fixture, target="   ")

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TARGET_REQUIRED
    assert fixture.servers.reads == 0
    assert fake.commands == []
    assert fixture.action_requests.requests == []


@pytest.mark.parametrize(
    "target",
    ["203.0.113.0/24", "2001:db8::1", "2001:db8::/32", "host.example.com", "not-an-address"],
)
async def test_only_an_ipv4_address_is_accepted(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A CHANGE tool writes firewall rules, so it takes the one kind it can write.

    Every kind the preflight read deliberately accepts is refused here, which is the same
    invariant read from
    the other side — see `test_whm_tools_firewall_preflight.py` for the READ half.
    """
    fixture, fake = release_context(monkeypatch, box=_allowlisted_box())

    answer, _ = await allowlist_remove(fixture, target=target)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_INVALID_TARGET
    assert fake.commands == []
    assert fixture.action_requests.requests == []


async def test_an_ipv4_address_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IPv4-only guard's negative control.

    Without it, "every non-IPv4 target is refused" passes just as well against a tool that
    refuses every target.
    """
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box())

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)


async def test_an_ambiguous_server_ref_returns_choices_and_opens_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CHANGE that guessed which machine an operator meant is the whole hazard."""
    cipher = build_cipher()
    shared = "https://shared.example.net:2087"
    fixture, fake = release_context(
        monkeypatch,
        box=_allowlisted_box(),
        cipher=cipher,
        servers=[
            preflight_server("one", cipher=cipher, base_url=shared),
            preflight_server("two", cipher=cipher, base_url=shared),
        ],
    )

    answer, _ = await allowlist_remove(fixture, server_ref="shared.example.net")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["one", "two"]
    assert fake.commands == []
    assert fixture.action_requests.requests == []


async def test_zero_usable_backends_refuses_rather_than_answering_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero backends refuse rather than answer, and on this tool the harm has a second shape.

    The tool holds no copy of the check: it is `firewall_gate.run_on_usable_backends`' refusal,
    raised where the backend set becomes work and travelling back through `sanitize_tool_errors`.
    Without the door, a box NOA cannot read at all would produce a *no-op* — "there is
    nothing on the allow lists" from a machine whose allow lists were never opened.
    """
    fixture, _ = release_context(monkeypatch, box=FakeFirewallBox())

    answer, _ = await allowlist_remove(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_NO_FIREWALL_BACKEND
    assert fixture.action_requests.requests == []


async def test_denied_sudo_names_sudo_rather_than_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`noa-old` GH #82: two causes, two remedies, two codes.

    The binaries are installed; the sudoers line is not. "No firewall tools on this server" sends
    the operator to install software that is already there.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(csf=csf_backend(), imunify=imunify_backend(), sudo_denied=True),
        ssh_username="operator",
    )

    answer, _ = await allowlist_remove(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == SSH_SUDO_REQUIRED_CODE
    assert fixture.action_requests.requests == []


async def test_a_raising_preflight_reaches_the_model_as_a_named_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw exception never reaches the LLM, and a timeout says so by name."""
    from noa_api.mcp_tools import whm_firewall_allowlist

    async def raises(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("the server did not answer")

    fixture, _ = release_context(monkeypatch, box=_allowlisted_box())
    monkeypatch.setattr(whm_firewall_allowlist, "gather_firewall_entries", raises)

    answer, _ = await allowlist_remove(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "the server did not answer" not in answer["message"]
    assert fixture.action_requests.requests == []


async def test_a_gate_write_failure_refuses_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """No authorization row means no authorization, so the call fails rather than answering
    with a card address that leads nowhere."""
    fixture, _ = release_context(monkeypatch, box=_allowlisted_box())
    fixture.action_requests.fail_create = RuntimeError("connection reset")

    answer, _ = await allowlist_remove(fixture)

    assert answer["ok"] is False
    assert "connection reset" not in answer["message"]


async def test_no_credential_reaches_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result persists in LibreChat's MongoDB, so it carries no credential
    material — asserted against the ciphertext in the column and the plaintext behind it."""
    fixture, fake = release_context(monkeypatch, box=_allowlisted_box())

    answer, _ = await allowlist_remove(fixture)

    assert isinstance(answer, ToolResult)
    serialized = answer.model_dump_json()
    assert all(secret not in serialized for secret in SECRETS)
    assert all(
        secret not in serialized for secret in (SSH_PASSWORD_PLAINTEXT, SSH_PRIVATE_KEY_PLAINTEXT)
    )
    # The decrypt really ran: the transport saw the plaintext the column does not hold.
    assert fake.runs[0].config.password == SSH_PASSWORD_PLAINTEXT


# --------------------------------------------------------------------------------------
# The mount: `tools/call` over the real app
# --------------------------------------------------------------------------------------


async def test_the_mounted_call_opens_a_request_and_writes_no_tool_runs_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over `create_app()`, with every middleware in the chain.

    The fourth CHANGE tool to make this claim, and it is re-made rather than inherited: the risk
    comes from `register_whm_firewall_allowlist_tools`, a registrar reached from the aggregate
    for the first time here, and a tool registered CHANGE but classified READ would have the
    production middleware write a `tool_runs` row for a change that has not happened.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    cipher = build_cipher()
    tools = build_tool_context(
        servers=[preflight_server(SERVER_NAME, cipher=cipher)],
        authorization=authorization,
        cipher=cipher,
    )
    install_fake_ssh_exec(monkeypatch, _allowlisted_box())

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        result = session.call_tool(
            TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
            {"server_ref": SERVER_NAME, "target": TARGET},
        )

    assert result.get("isError") is not True
    # Both blocks survive the transport, in order.
    assert [block["type"] for block in result["content"]] == ["text", "resource"]

    request = tools.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.requested_by_user_id == user.id
    # The run row belongs to the executor that runs after a decision, not to this call.
    assert tools.tool_runs.runs == []


# --- Helpers ---


def _tool_context() -> Any:
    return build_tool_context().context


async def _published_tools() -> dict[str, Any]:
    """The tools as `tools/list` publishes them, which is what a model is told."""
    context = _tool_context()
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)
    return {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
