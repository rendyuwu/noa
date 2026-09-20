"""`whm_firewall_release_and_allow` — the call that opens a question.

The tool half: the guards, the in-process preflight, the gate response and the mount. Its runner
lives on the far side of the cookie/CSRF boundary and is asserted in
`test_whm_firewall_release_runner.py`; the two are separate files because together they run past the
900-line budget, and the split falls on the boundary the design already draws — nothing here can
change anything, and nothing there is reachable without an approval.

Driven through the real `open_change_request` inside a real request context, so
`current_mcp_identity` and `read_conversation_ref` are production functions rather than patched
names. Seams are the preflight tool's: the real resolver, a real `SecretCipher`, real CSF and
Imunify parsing, real command composition through `core.remote_exec.sudo`, real
`sanitize_tool_errors`. Only the SSH socket is doubled — in all three modules a firewall call
crosses — so a test asserts the whole command sequence rather than one hop of it.

**What is new on this side, beyond mirroring the suspend tool.**

1. **`duration_minutes` is required and bounded.** No server-side default, 1 to 525600,
   and the bound is asserted on the published schema as well as in the body — the schema is what
   the model is told, and the body is where a caller reaching the function directly meets it.
2. **The before-state is the receipt's first half** (DECISIONS section 6.5). What lands on the row
   is
   why the address was blocked and the log lines it was read from, not a verdict on its own.
3. **The IPv4-only rule read from the CHANGE side.** Every target kind the preflight deliberately
   accepts
   is refused here, because this one writes firewall rules.

There is deliberately **no no-op test**, because there is no no-op: the allow entry carries a TTL,
so a repeat always moves the expiry. `test_an_address_no_backend_has_heard_of_still_opens_a_request`
and `test_an_already_allowlisted_address_still_opens_a_request` are that claim stated as cases.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

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
from noa_api.mcp_tools.whm_firewall import (
    ERROR_INVALID_TARGET,
    ERROR_TARGET_REQUIRED,
    NOA_COMMENT_MARKER,
    noa_firewall_comment,
)
from noa_api.mcp_tools.whm_firewall_change import (
    ERROR_DURATION_INVALID,
    EVIDENCE_DURATION_MINUTES,
    EVIDENCE_FIREWALL,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    build_whm_firewall_change_runners,
)
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.remote_exec import install_fake_ssh_exec
from support.secrets import build_cipher
from support.servers import EMBED_BASE_URL, SECRETS, build_tool_context
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    CSF_READ,
    IMUNIFY_ADD,
    IMUNIFY_CLEAN,
    IMUNIFY_DELETE,
    IMUNIFY_DROP,
    IMUNIFY_WHITE,
    IMUNIFY_WHITE_AND_DROP,
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
    working_box,
)
from support.whm_firewall_change import (
    DURATION_MINUTES,
    changes,
    csf_commands,
    release,
    release_context,
)

# --------------------------------------------------------------------------------------
# The call opens a question and changes nothing
# --------------------------------------------------------------------------------------


async def test_a_release_call_opens_a_pending_request_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole gate in one assertion pair: a row exists, and no firewall was written to.

    The second half is the one that matters and it is counted rather than inferred — the call
    *does* reach the server, for its preflight, so "no SSH happened" would be false and "the
    payload said pending" would pass against a tool that released the address and then said so.
    """
    fixture, fake = release_context(monkeypatch)

    answer, user_id = await release(fixture)

    request = fixture.action_requests.only
    assert request.status is ActionRequestStatus.PENDING
    assert request.tool_name == TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW
    assert request.requested_by_user_id == user_id
    assert fixture.action_requests.commits == [ActionRequestStatus.PENDING.value]
    assert isinstance(answer, ToolResult)
    # One read per backend and nothing else: no `-tr`, no `-dr`, no `-ta`, no Imunify write.
    assert [csf_step(command) for command in csf_commands(fake)] == [CSF_READ]
    assert all(
        IMUNIFY_DELETE not in command and f" {IMUNIFY_ADD} " not in command
        for command in changes(fake)
    )


async def test_the_preflight_runs_inside_the_call_and_lands_on_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One workflow, one call: evidence born in it, persisted for the card.

    The before-state DECISIONS section 6.5 asks the receipt for is exactly this: why the address was
    blocked, and the `csf.deny` / Imunify lines it was read from. Asserted on the evidence lines
    rather than on the verdict alone, because a card that says "blocked" and shows nothing is the
    state section 6.5's "block reason + log evidence" wording exists to prevent.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_DENY_LINE)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_DROP)),
        ),
    )

    await release(fixture)

    evidence = fixture.action_requests.only.approval_context["evidence"]
    assert evidence[EVIDENCE_SERVER_NAME] == SERVER_NAME
    assert evidence[EVIDENCE_SERVER_ID] == str(fixture.servers.servers[0].id)
    assert evidence[EVIDENCE_TARGET] == TARGET
    assert evidence[EVIDENCE_DURATION_MINUTES] == DURATION_MINUTES

    firewall = evidence[EVIDENCE_FIREWALL]
    assert firewall["combined_verdict"] == "blocked"
    assert firewall["available_backends"] == {"csf": True, "imunify": True}
    assert firewall["unanswered_backends"] == []
    assert CSF_DENY_LINE in firewall["matches"]
    assert f"Imunify blacklist: {TARGET}" in " ".join(firewall["matches"])
    # The evidence carries its own bound, on the card as much as in a tool result.
    assert firewall["total_matches"] == len(firewall["matches"])
    assert firewall["truncated"] is False


async def test_an_earlier_approvals_reason_does_not_reach_this_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one operator-typed reason field does not travel from one decision to the next.

    The shape is the live one rather than a string: this tool writes `noa:<id> <reason>` into the
    comment of every allow entry it creates, csf echoes that comment back through `csf -g`, and
    this before-state is rendered onto the approval card and into the block an operator copies
    into a ticket. So an address released last week arrives at today's card carrying last week's
    typed words — authored by a different operator, for a decision nobody is being asked to make
    here. `EARLIER_APPROVAL` is deliberately not this request's id, because that is the case:
    the entry outlives the approval that authorised it.

    Driven through the parse site, not around it. A test that hand-built the evidence dict would
    assert about its own fixture and would stay green with the cut deleted.

    The second half is the one that makes the first mean anything: a cut that ate the whole line
    would satisfy "the reason is gone" and destroy the evidence the card exists to show. So the
    marker survives — `noa:<id>` is the address of the approval row where that reason is readable
    behind the operator's own cookie — and so does everything csf and Imunify wrote themselves,
    including an administrator's own `office` note, which is the target system's text and not
    anybody's approval reason.
    """
    earlier_approval = uuid4()
    earlier_reason = "customer confirmed, ticket NOC-4471"
    noa_allow_line = (
        f"{CSF_ALLOW_LINE} ({noa_firewall_comment(earlier_approval, reason=earlier_reason)})"
    )
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(f"{CSF_DENY_LINE}\n{noa_allow_line}")),
            imunify=imunify_backend(imunify_answer(IMUNIFY_WHITE_AND_DROP)),
        ),
    )

    await release(fixture)

    approval_context = fixture.action_requests.only.approval_context
    # On the serialized row rather than on one key: the reason is not a field here, it is text
    # inside an evidence line, and a key comparison would pass straight over it.
    assert earlier_reason not in json.dumps(approval_context)

    matches = approval_context["evidence"][EVIDENCE_FIREWALL]["matches"]
    assert f"{CSF_ALLOW_LINE} ({NOA_COMMENT_MARKER}{earlier_approval}" in matches
    assert CSF_DENY_LINE in matches
    assert "office" in " ".join(matches)
    assert "smtpauth brute force" in " ".join(matches)
    # The cut shortens lines and never drops one, so the bound the card states is unmoved.
    assert approval_context["evidence"][EVIDENCE_FIREWALL]["total_matches"] == len(matches)


async def test_the_recorded_arguments_are_the_three_the_schema_declares(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row records what was asked for, and no reason is among it.

    Asserted on the keys rather than on the absence of one name, so a future argument cannot
    arrive here unnoticed — and cross-checked against `FORBIDDEN_REASON_KEYS` so the claim is
    about the whole family of spellings the gate refuses.
    """
    fixture, _ = release_context(monkeypatch)

    await release(fixture)

    arguments = fixture.action_requests.only.approval_context["arguments"]
    assert set(arguments) == {"server_ref", "target", "duration_minutes"}
    assert arguments["duration_minutes"] == DURATION_MINUTES
    assert FORBIDDEN_REASON_KEYS.isdisjoint(arguments)


async def test_an_address_no_backend_has_heard_of_still_opens_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no no-op here, and this is that claim as a case.

    The suspend and unsuspend tools answer instead of gating when the account is already in the
    state the change would produce. A clean address is not that state: the release finds nothing,
    and the allow — with the TTL the operator stated — is still the change that was asked for.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_CLEAN)),
        ),
    )

    answer, _ = await release(fixture)

    assert isinstance(answer, ToolResult)
    assert fixture.action_requests.only.status is ActionRequestStatus.PENDING
    assert (
        fixture.action_requests.only.approval_context["evidence"][EVIDENCE_FIREWALL][
            "combined_verdict"
        ]
        == "not_found"
    )


async def test_an_already_allowlisted_address_still_opens_a_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of "no no-op", and the case an operator actually re-runs.

    An address already on the allow list is re-released when its window is about to close — the
    entry carries a TTL, so a repeat moves the expiry and is a real change.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_ALLOW_LINE)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_WHITE)),
        ),
    )

    answer, _ = await release(fixture)

    assert isinstance(answer, ToolResult)
    assert fixture.action_requests.only.status is ActionRequestStatus.PENDING


# --------------------------------------------------------------------------------------
# What the model is handed
# --------------------------------------------------------------------------------------


async def test_the_result_carries_the_card_address_and_the_iframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two blocks, text first, and the plain address inside the text.

    Asserted on the count and the order because that is the claim — "both, never one" — and
    a test that only looked for a resource would pass on a result with no address in it, which is
    the case where the frame fails to load and the operator has no door.
    """
    fixture, _ = release_context(monkeypatch)

    answer, _ = await release(fixture)

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

    The before-state is on the row, behind the operator's cookie. Putting it in the tool result
    would publish it to LibreChat's MongoDB, and a `csf.deny` line names hosts and services.
    """
    fixture, _ = release_context(
        monkeypatch, box=FakeFirewallBox(csf=csf_backend(csf_answer(CSF_DENY_LINE)))
    )

    answer, _ = await release(fixture)

    assert isinstance(answer, ToolResult)
    assert CSF_DENY_LINE not in answer.model_dump_json()


async def test_the_tool_schema_carries_no_reason_parameter() -> None:
    """The reason boundary is on the schema, so the schema is where it is asserted.

    Read off the registered server rather than off the function signature: what the rule bounds is
    what the model is *told* it may send, and that is what `tools/list` publishes.
    """
    tools = await _published_tools()
    properties = set(tools[TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW].parameters["properties"])

    assert properties == {"server_ref", "target", "duration_minutes"}
    assert FORBIDDEN_REASON_KEYS.isdisjoint(properties)


async def test_the_schema_declares_the_duration_bound_and_no_default() -> None:
    """The duration rule on the published schema: required, 1 to 525600, and no server-side default.

    A default here would be the thing the rule forbids most directly — a permanent-ish allow entry
    nobody stated the length of — and it would be invisible from the body, because a caller that
    omitted the argument would never reach the body's own check.
    """
    tools = await _published_tools()
    schema = tools[TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW].parameters
    duration = schema["properties"]["duration_minutes"]

    assert duration["type"] == "integer"
    assert duration["minimum"] == MIN_DURATION_MINUTES == 1
    assert duration["maximum"] == MAX_DURATION_MINUTES == 525_600
    assert "default" not in duration
    assert set(schema["required"]) == {"server_ref", "target", "duration_minutes"}


def test_the_tool_is_catalogued_and_classified_as_a_change() -> None:
    """A name outside the catalog is a capability no role can be granted, and a CHANGE
    that registered as a READ would have the audit middleware write a row for a change that has
    not happened."""
    context = _tool_context()
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW in TOOL_CATALOG
    assert registered[TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW] is ToolRisk.CHANGE
    assert set(build_whm_firewall_change_runners(context=context)) == {
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW
    }


# --------------------------------------------------------------------------------------
# The guards, all of them before a row is written
# --------------------------------------------------------------------------------------


async def test_a_blank_target_is_refused_before_any_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blank-target guard is placed where the schema cannot reach: `min_length` counts
    whitespace, so `"  "` would otherwise be carried to a server."""
    fixture, fake = release_context(monkeypatch)

    answer, _ = await release(fixture, target="   ")

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

    Every kind the preflight deliberately accepts is refused here, which is the same invariant read
    from the other side — see `test_whm_tools_firewall_preflight.py` for the READ half.
    """
    fixture, fake = release_context(monkeypatch)

    answer, _ = await release(fixture, target=target)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_INVALID_TARGET
    assert fake.commands == []
    assert fixture.action_requests.requests == []


async def test_an_ipv4_address_is_not_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IPv4-only guard's negative control.

    Without it, "every non-IPv4 target is refused" passes just as well against a tool that
    refuses every target.
    """
    fixture, _ = release_context(monkeypatch)

    answer, _ = await release(fixture)

    assert isinstance(answer, ToolResult)


@pytest.mark.parametrize("duration", [0, -1, MAX_DURATION_MINUTES + 1])
async def test_a_duration_outside_the_bound_is_refused_before_any_round_trip(
    monkeypatch: pytest.MonkeyPatch, duration: int
) -> None:
    """1 to 525600, checked in the body as well as on the schema.

    The schema is what the model is told; this is what holds for a caller that reaches the
    function directly — the same reason `assert_no_reason_argument` exists behind the reason-free
    schema rule. Refused before any I/O, so a malformed window costs no round trip.
    """
    fixture, fake = release_context(monkeypatch)

    answer, _ = await release(fixture, duration_minutes=duration)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_DURATION_INVALID
    assert fake.commands == []
    assert fixture.action_requests.requests == []


@pytest.mark.parametrize("duration", [MIN_DURATION_MINUTES, MAX_DURATION_MINUTES])
async def test_the_bounds_themselves_are_accepted(
    monkeypatch: pytest.MonkeyPatch, duration: int
) -> None:
    """The bound is inclusive at both ends, and that is the off-by-one this catches."""
    fixture, _ = release_context(monkeypatch)

    answer, _ = await release(fixture, duration_minutes=duration)

    assert isinstance(answer, ToolResult)
    assert (
        fixture.action_requests.only.approval_context["evidence"][EVIDENCE_DURATION_MINUTES]
        == duration
    )


async def test_an_ambiguous_server_ref_returns_choices_and_opens_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CHANGE that guessed which machine an operator meant is the whole hazard."""
    cipher = build_cipher()
    shared = "https://shared.example.net:2087"
    fixture, fake = release_context(
        monkeypatch,
        cipher=cipher,
        servers=[
            preflight_server("one", cipher=cipher, base_url=shared),
            preflight_server("two", cipher=cipher, base_url=shared),
        ],
    )

    answer, _ = await release(fixture, server_ref="shared.example.net")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["one", "two"]
    assert fake.commands == []
    assert fixture.action_requests.requests == []


async def test_zero_usable_backends_refuses_rather_than_changing_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero usable backends, and this is the harm the invariant is actually about.

    The tool holds no copy of the check: it is `firewall_gate.run_on_usable_backends`' refusal,
    raised where the backend set becomes work and travelling back through `sanitize_tool_errors`.
    A card opened here would be an approval for a change NOA could never make.
    """
    fixture, _ = release_context(monkeypatch, box=FakeFirewallBox())

    answer, _ = await release(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_NO_FIREWALL_BACKEND
    assert fixture.action_requests.requests == []


async def test_denied_sudo_names_sudo_rather_than_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two causes, two remedies, two codes — the upstream issue's lesson.

    The binaries are installed; the sudoers line is not. "No firewall tools on this server" sends
    the operator to install software that is already there.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(csf=csf_backend(), imunify=imunify_backend(), sudo_denied=True),
        ssh_username="operator",
    )

    answer, _ = await release(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == SSH_SUDO_REQUIRED_CODE
    assert fixture.action_requests.requests == []


async def test_a_raising_preflight_reaches_the_model_as_a_named_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw exception never reaches the LLM, and a timeout says so by name."""
    from noa_api.mcp_tools import whm_firewall_change

    async def raises(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("the server did not answer")

    fixture, _ = release_context(monkeypatch)
    monkeypatch.setattr(whm_firewall_change, "gather_firewall_entries", raises)

    answer, _ = await release(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_TIMEOUT
    assert "the server did not answer" not in answer["message"]
    assert fixture.action_requests.requests == []


async def test_a_gate_write_failure_refuses_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """No authorization row means no authorization, so the call fails rather than answering
    with a card address that leads nowhere."""
    fixture, _ = release_context(monkeypatch)
    fixture.action_requests.fail_create = RuntimeError("connection reset")

    answer, _ = await release(fixture)

    assert answer["ok"] is False
    assert "connection reset" not in answer["message"]


async def test_no_credential_reaches_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result persists in LibreChat's MongoDB, so it carries no credential
    material — asserted against the ciphertext in the column and the plaintext behind it."""
    fixture, fake = release_context(monkeypatch)

    answer, _ = await release(fixture)

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
    """Gate, classification, audit trail — over `create_app()`, with every middleware in the chain.

    The third CHANGE tool to make this claim, and it is re-made rather than inherited: the risk
    comes from `register_whm_firewall_change_tools`, and a tool registered CHANGE but classified
    READ would have the production middleware write a `tool_runs` row for a change that has not
    happened.
    """
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    cipher = build_cipher()
    tools = build_tool_context(
        servers=[preflight_server(SERVER_NAME, cipher=cipher)],
        authorization=authorization,
        cipher=cipher,
    )
    install_fake_ssh_exec(monkeypatch, working_box())

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        user = authorization.add_user("operator@example.com", roles=(ROLE_SUPPORT,))
        authorization.grant(ROLE_SUPPORT, TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW)
        plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
        session = open_session(fixture.client, plaintext)

        result = session.call_tool(
            TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
            {"server_ref": SERVER_NAME, "target": TARGET, "duration_minutes": DURATION_MINUTES},
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
