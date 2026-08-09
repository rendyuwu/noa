"""The CHANGE gate's answer: an approval surface, in the shape T59 measured (T32).

T33's file next door proves the gate *writes* the row. This one proves what the tool hands
back, and the claims are different in kind: a row is right or wrong against the database,
while this is right or wrong against a renderer NOA does not own. So the assertions here are
deliberately literal — the `ui://` scheme, the `text/uri-list` mime type, the URL as the
resource body — because those three strings are what was carried into Chromium against
LibreChat pin `45cc53c4` on 2026-08-08 and measured to produce an iframe on NOA's origin with
the session cookie riding in (R29, R31c, R12). A test that asserted "some resource is
present" would stay green through the exact drift C21 exists to catch.

Four invariants, and each has its own section below:

- **V24** — one function, three branches, and the active one ships the UI resource *and* the
  link-out text together rather than choosing.
- **V25** — the plain address is in the text block on every branch that has one. T43 bounded
  what that is worth and what it is not: a click needs `allow-popups`, absent at one of the
  two render sites, and a tab opened by a click inherits the frame's sandbox (R32, V94) — so
  the thing that survives is an address a human can copy, not a link.
- **V26** — the URL carries an id and nothing else, and neither does the rest of the result.
- **C8 / V15 / V71** — the word an operator types on the card does not appear in text a model
  reads.

No mount-level test here for the same reason `test_mcp_change_gate.py` has none: no CHANGE
tool is registered yet (T22-T29 are all `.`), so nothing reaches this function through
`tools/call`. T22 is where it first runs over the real mount.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from mcp.types import EmbeddedResource, TextContent

from core.approvals.errors import ChangeGateBranchUnavailableError, ChangeGateError
from core.db.lifecycle import ActionRequestStatus
from noa_api.mcp_audit import CONVERSATION_REF_HEADER
from noa_api.mcp_tools.change_gate import (
    ACTIVE_CHANGE_GATE_BRANCH,
    APPROVAL_CARD_PATH,
    UI_RESOURCE_MIME_TYPE,
    UI_RESOURCE_URI_PREFIX,
    ChangeGateBranch,
    PendingChangeRequest,
    approval_card_url,
    build_change_gate_response,
    open_change_request,
)
from support.mcp_identity import EMAIL, LIBRECHAT_USER, authenticated_caller, http_request_context
from support.servers import EMBED_BASE_URL, build_tool_context

# The first CHANGE tool (T22), named rather than built: this file tests the response shape.
CHANGE_TOOL = "whm_suspend_account"

# The two branches that exist. `ELICITATION` is declared and refuses — see its own test.
BUILT_BRANCHES = (ChangeGateBranch.LINK_OUT, ChangeGateBranch.UI_RESOURCE)


def pending(action_request_id: UUID | None = None) -> PendingChangeRequest:
    """What `open_change_request` returns, without the write."""
    return PendingChangeRequest(
        action_request_id=action_request_id or uuid4(),
        status=ActionRequestStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(seconds=900),
    )


def text_block(result: Any) -> str:
    """The one `TextContent` in a result, or fail saying there was not exactly one."""
    blocks = [block for block in result.content if isinstance(block, TextContent)]
    assert len(blocks) == 1, f"expected one text block, got {len(blocks)}"
    return blocks[0].text


def resource_block(result: Any) -> EmbeddedResource:
    """The one `EmbeddedResource` in a result, or fail saying there was not exactly one."""
    blocks = [block for block in result.content if isinstance(block, EmbeddedResource)]
    assert len(blocks) == 1, f"expected one resource block, got {len(blocks)}"
    return blocks[0]


# --------------------------------------------------------------------------------------
# V24: one function, three branches, and the active one ships two halves
# --------------------------------------------------------------------------------------


def test_active_branch_ships_the_ui_resource_and_the_link_out_text_together() -> None:
    """V24: two branches ship TOGETHER, not either/or.

    The frame can fail to load — a CSP mistake, a bad deploy, a future upstream bump — and
    the address in the text is the only door left when it does. Asserting on the *count*
    rather than on "a resource exists" is what makes a regression that drops the text block
    fail here instead of passing quietly.
    """
    tools = build_tool_context()
    request = pending()

    result = build_change_gate_response(request, tool_name=CHANGE_TOOL, context=tools.context)

    assert ACTIVE_CHANGE_GATE_BRANCH is ChangeGateBranch.UI_RESOURCE
    assert len(result.content) == 2
    assert isinstance(result.content[0], TextContent)
    assert isinstance(result.content[1], EmbeddedResource)


def test_the_link_out_branch_is_text_only_and_still_carries_the_address() -> None:
    """V24/V25: the branch that becomes primary if a bump fails T59 (C21).

    It is one block, and that block still has the URL in it — which is the whole reason this
    branch is survivable at all.
    """
    tools = build_tool_context()
    request = pending()

    result = build_change_gate_response(
        request, tool_name=CHANGE_TOOL, context=tools.context, branch=ChangeGateBranch.LINK_OUT
    )

    assert len(result.content) == 1
    assert approval_card_url(
        request.action_request_id, embed_base_url=EMBED_BASE_URL
    ) in text_block(result)


def test_the_elicitation_branch_is_declared_and_refuses() -> None:
    """V24: three branches, and the third is future — declared, not silently missing.

    It refuses with a `ChangeGateError` rather than returning something that looks like an
    approval surface, and rather than a bare exception: `sanitize_tool_errors` passes a
    `NoaError`'s code through to the model, so the failure names itself (V19, V73).
    """
    tools = build_tool_context()

    with pytest.raises(ChangeGateBranchUnavailableError) as raised:
        build_change_gate_response(
            pending(),
            tool_name=CHANGE_TOOL,
            context=tools.context,
            branch=ChangeGateBranch.ELICITATION,
        )

    assert isinstance(raised.value, ChangeGateError)
    assert raised.value.error_code == "change_gate_branch_unavailable"
    assert len(list(ChangeGateBranch)) == 3


# --------------------------------------------------------------------------------------
# R29 / R31c / R12: the resource is the shape that was measured, not one that resembles it
# --------------------------------------------------------------------------------------


def test_the_ui_resource_is_the_shape_the_render_gate_measured() -> None:
    """R29: `ui://` + `text/uri-list` + the URL as the body, all three or none of them.

    `ui://` is LibreChat's classifier (`parsers.ts:183`, R31c) — without it the resource is
    an ordinary attachment and no card renders. `text/uri-list` is mcp-ui's selector for the
    `src` render mode (R12); `text/html` renders `srcDoc`, whose opaque origin the T59 run
    measured as unable to read `document.cookie` at all (C17). Asserted literally because
    both strings belong to code NOA does not own.
    """
    tools = build_tool_context()
    request = pending()

    resource = resource_block(
        build_change_gate_response(request, tool_name=CHANGE_TOOL, context=tools.context)
    ).resource

    assert str(resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{request.action_request_id}"
    assert str(resource.uri) == f"ui://noa/approval/{request.action_request_id}"
    assert resource.mimeType == UI_RESOURCE_MIME_TYPE == "text/uri-list"
    # The negative half of the same measurement: `text/html` is the branch that died.
    assert resource.mimeType != "text/html"
    assert getattr(resource, "text", None) == approval_card_url(
        request.action_request_id, embed_base_url=EMBED_BASE_URL
    )


# --------------------------------------------------------------------------------------
# V25: the plain address, on every branch that has a text block
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("branch", BUILT_BRANCHES)
def test_every_built_branch_carries_the_plain_address_in_the_text(
    branch: ChangeGateBranch,
) -> None:
    """V25: "always" means on every branch, not only the one shipping today.

    And it is the *address*, not markup: no `<a>`, no `href`, nothing that needs a control
    the frame's sandbox can withhold (V94, R32). What an operator can do with it is copy it.
    """
    tools = build_tool_context()
    request = pending()

    text = text_block(
        build_change_gate_response(
            request, tool_name=CHANGE_TOOL, context=tools.context, branch=branch
        )
    )

    assert approval_card_url(request.action_request_id, embed_base_url=EMBED_BASE_URL) in text
    assert "href" not in text
    assert "<a " not in text


def test_the_text_names_the_tool_the_request_and_the_deadline() -> None:
    """What the block has to say for itself: nothing ran, here is the id, here is the TTL.

    The id is in the text rather than only in the URL because `noa_get_action_result` takes
    one (T63), and V23 answers "did this run?" from a row — so the model needs the id to ask,
    and is told to ask rather than to assume.
    """
    tools = build_tool_context()
    request = pending()

    text = text_block(
        build_change_gate_response(request, tool_name=CHANGE_TOOL, context=tools.context)
    )

    assert CHANGE_TOOL in text
    assert str(request.action_request_id) in text
    assert request.expires_at.isoformat() in text
    assert "noa_get_action_result" in text
    assert "ran nothing" in text


# --------------------------------------------------------------------------------------
# V26 / C8: an id, and nothing else, in front of the model
# --------------------------------------------------------------------------------------


def test_the_approval_url_carries_the_id_and_nothing_else() -> None:
    """V26: no token, no tool name, no arguments — the result lives in LibreChat's Mongo.

    Asserted by taking the URL apart rather than by matching a string, so a query parameter
    appended later fails here instead of hiding inside a `startswith`.
    """
    request = pending()

    parts = urlsplit(approval_card_url(request.action_request_id, embed_base_url=EMBED_BASE_URL))

    assert f"{parts.scheme}://{parts.netloc}" == EMBED_BASE_URL
    assert parts.path == f"{APPROVAL_CARD_PATH}/{request.action_request_id}"
    assert parts.query == ""
    assert parts.fragment == ""


async def test_the_response_carries_no_arguments_evidence_or_requester() -> None:
    """V26/V17: the card reads those behind a cookie; the transcript never gets them.

    Driven through the real gate rather than a hand-built value, because the claim is about
    what survives the hop from `open_change_request` to `build_change_gate_response`: the
    arguments and the preflight go into `approval_context` on the row, and the only thing
    that comes back out is an id. Sentinels in all three places, one assertion each.
    """
    tools = build_tool_context()
    user, _ = authenticated_caller()

    with http_request_context({CONVERSATION_REF_HEADER: "conv-1"}, user=user):
        opened = await open_change_request(
            tool_name=CHANGE_TOOL,
            arguments={"server_ref": "alpha", "account": "acmeco-sentinel"},
            evidence={"suspended": False, "domain": "evidence-sentinel.example"},
            context=tools.context,
        )

    serialized = build_change_gate_response(
        opened, tool_name=CHANGE_TOOL, context=tools.context
    ).model_dump_json()

    assert "acmeco-sentinel" not in serialized
    assert "evidence-sentinel.example" not in serialized
    assert EMAIL not in serialized
    assert LIBRECHAT_USER not in serialized
    assert str(opened.action_request_id) in serialized


@pytest.mark.parametrize("branch", BUILT_BRANCHES)
def test_the_result_text_never_mentions_a_reason(branch: ChangeGateBranch) -> None:
    """C8/V15/V71: the reason is born on the card, and the model is not told it exists.

    V71 keeps reason-writing out of the agent's system prompt for this reason; the same rule
    has to hold for text NOA itself emits, which is the only other model-facing surface the
    gate controls. A model that knows there is a field is a model that can be argued into
    filling it, and C8's boundary is that it never has one to relay.
    """
    tools = build_tool_context()

    text = text_block(
        build_change_gate_response(
            pending(), tool_name=CHANGE_TOOL, context=tools.context, branch=branch
        )
    )

    assert "reason" not in text.lower()
    assert "justif" not in text.lower()


# --------------------------------------------------------------------------------------
# The address comes from configuration, not from a literal
# --------------------------------------------------------------------------------------


def test_the_url_is_built_from_the_configured_base() -> None:
    """The fixture's base is not `Settings`' default, so a hardcoded one cannot pass (V87)."""
    tools = build_tool_context(embed_base_url="https://approvals.example.test")
    request = pending()

    url = approval_card_url(request.action_request_id, embed_base_url=tools.context.embed_base_url)

    assert url.startswith("https://approvals.example.test/")
    assert url in text_block(
        build_change_gate_response(request, tool_name=CHANGE_TOOL, context=tools.context)
    )


def test_the_url_joins_the_configured_base_exactly_once() -> None:
    """A base with a trailing slash produces one separator, not two.

    `core.config` already strips them, so this guards the other callers: tests, and T56's
    table surface, which will build its own path off the same base.
    """
    request = pending()

    url = approval_card_url(request.action_request_id, embed_base_url="https://embed.test/")

    assert url == f"https://embed.test{APPROVAL_CARD_PATH}/{request.action_request_id}"
    assert "//approvals" not in url


def test_create_app_passes_the_configured_embed_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production wiring, not a fixture's: `create_app` reads `Settings` and passes it.

    The configured value is deliberately not the `Settings` default, and `mounted_app` is not
    used, for the reason its twin one file over gives (`test_mcp_change_gate.py`): a harness
    that patched in the default could not separate a wiring that read the setting from one
    that hardcoded the same string.
    """
    from noa_api import main
    from noa_api.mcp_tools.context import build_mcp_tool_context
    from support.auth import build_settings

    configured = "https://embed.configured.test"
    captured: dict[str, Any] = {}

    def capturing_builder(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return build_mcp_tool_context(**kwargs)

    monkeypatch.setattr(main, "get_settings", lambda: build_settings(noa_embed_base_url=configured))
    monkeypatch.setattr(main, "build_mcp_tool_context", capturing_builder)
    main.create_app()

    assert captured["embed_base_url"] == configured


def test_the_gate_reads_the_base_off_the_context_it_was_given() -> None:
    """Two contexts, two addresses: nothing in the response builder holds its own copy."""
    first = build_tool_context(embed_base_url="https://one.test")
    second = build_tool_context(embed_base_url="https://two.test")
    request = pending()

    one = text_block(
        build_change_gate_response(request, tool_name=CHANGE_TOOL, context=first.context)
    )
    two = text_block(
        build_change_gate_response(request, tool_name=CHANGE_TOOL, context=second.context)
    )

    assert "https://one.test/approvals/" in one
    assert "https://two.test/approvals/" in two
