"""The CHANGE gate: a `tools/call` opens a request, it never runs a change.

The tool surface splits in two. A READ executes immediately and its `tool_runs` row is
written around it by `ToolRunAuditMiddleware`. A CHANGE does not execute at all: it
runs its preflight in-process, calls `open_change_request` with the evidence that
produced, and hands the result to `build_change_gate_response`. Nothing in this module can
execute anything, and that is the point — the code path an LLM can reach ends at an INSERT
and a URL.

**Two halves, one module.** One half writes the row; the other shapes the answer. They live together
because they are two steps of one call — a CHANGE tool's whole body is `open_change_request`
then `build_change_gate_response` — and because the second takes the first's return value.
`noa_api.mcp_tools.results` would otherwise be the home for a result shape, and it cannot be:
it is imported by `noa_api.mcp_audit`, which this module imports, so the dependency would
close a cycle.

**Why a function and not a middleware.** Every other cross-cutting rule on the tool path is a
`Middleware` precisely because a tool cannot forget one (RBAC, then audit, at one seam). This one
cannot be, and the reason is one workflow, one tool: the evidence the approval card shows is the
tool's own in-process preflight, and a middleware sits outside the tool and has none. What replaces
"a tool cannot forget it" is narrower but real — this is the only writer of a PENDING row
(`core.approvals.repository` writes no other status), so a CHANGE tool that skipped the gate and
executed would have no authorization row at all, and the verdict is read from the row.

**Three refusals, and all three refuse before the INSERT** (`core.approvals.errors`):

- an argument that reads as a reason — see `assert_no_reason_argument`;
- no preflight evidence — a card that asks for authorisation and describes
  nothing is the state the provenance rule exists to prevent;
- the write itself failing, which refuses the change rather than letting it proceed
  unrecorded (the tool-run writer's fail-closed argument, one table over).

**The caller is read here, not passed in.** `current_mcp_identity()` for who, and
`read_conversation_ref()` for the grouping label — the same function `noa_api.mcp_audit`
uses, imported rather than re-implemented, because `conversation_ref` is one audit label
with one home. A tool that could pass either would be a tool that could get
either wrong: `requested_by_user_id` is what the requester-match compares the approving
operator against, so an argument-supplied requester is an argument-supplied authorization.

**`status` is never an input.** No parameter here, and no branch, admits a claim that a
change is already approved. A repeated call with an APPROVED request's id in its arguments
opens a *new* PENDING request, because "may this run?" is answered from the row's own
`status` column and from nowhere else — not from an LLM claim, not from a tool argument.
Execution after approval is the decision endpoint and the executor, reached by a cookie POST from
a NOA-origin document, which is a path that does not pass through here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

import structlog
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.approvals.context import (
    CONTEXT_ARGUMENTS_KEY,
    CONTEXT_EVIDENCE_KEY,
    CONTEXT_REQUESTER_KEY,
)
from core.approvals.errors import (
    ChangeEvidenceRequiredError,
    ChangeGateBranchUnavailableError,
    ChangeGateUnavailableError,
    ChangeReasonForbiddenError,
)
from core.db.lifecycle import ActionRequestStatus
from core.secrets.redaction import redact_sensitive_data
from noa_api.mcp_audit import read_conversation_ref
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.ui_resource import (
    # The three fields that make an iframe render at all, and the join onto the embed base. Hoisted
    # when the table surface became the second result carrying a NOA-origin document:
    # summary-plus-URL says that surface uses *this* mechanism rather than a second one, and two
    # spellings of "what makes LibreChat render a frame" is how one of them goes stale.
    UI_RESOURCE_MIME_TYPE as UI_RESOURCE_MIME_TYPE,
)
from noa_api.mcp_tools.ui_resource import (
    build_ui_resource,
    embed_url,
)

# Argument names that would carry an LLM-authored reason. An explicit set, not a substring rule:
# `reason` has to be refused, while a legitimate argument such as `reason_code` on some future
# integration is not what the reason boundary is about, and a rule that guessed would eventually
# refuse a change for the wrong cause. Compared case-insensitively and stripped, the way
# `core.secrets.redaction.is_sensitive_key` compares.
FORBIDDEN_REASON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "reason",
        "proposed_reason",
        "change_reason",
        "approval_reason",
        "justification",
    }
)

# The two operator-facing strings every CHANGE tool writes into its evidence, and the only two
# keys the evidence dict is required to carry. They are constants for the reason
# `build_approval_context`'s three are: a tool writes them into `approval_context` JSONB and the
# card reads them back minutes later, and a misspelt key in JSONB reads as an absent one — which
# on these two is a card that silently falls back to a humanised tool name nobody notices is
# wrong.
#
# `headline` names the change in the operator's words; `asked` restates the request as what was
# asked. Both are composed in Python, beside the sentence each runner already composes, because
# the runner is the only party holding the family's own vocabulary — a table mapping each tool to
# a phrasing in the renderer would be a second copy of six runners' vocabularies with nothing
# reading it against them.
#
# `asked` is an **imperative**, never a prediction: "remove 203.0.113.24 from the deny lists" is
# the arguments restated, and "203.0.113.24 will be unblocked" is a claim with nothing measured
# behind it. The card refuses a predicted sentence, and the grammar is the whole difference.
EVIDENCE_HEADLINE: Final = "headline"
EVIDENCE_ASKED: Final = "asked"

# The heading over the target system's own text, on the tools that carry a raw evidence block —
# `Why it was blocked`, `What was allowed`, `What was on the list`. Written in Python for the same
# reason the two above are: the words are the family's, and a table mapping each tool to a phrase
# in the renderer would be a second copy of six runners' vocabularies with nothing reading it
# against them.
#
# **Optional by design, and its presence is the decision.** Four of the seven CHANGE tools read
# structured fields rather than vendor free-form text and have no block to head, so this cannot
# join the required keys below without failing four gates that are correct. The renderer draws the
# block where the key is present and draws nothing where it is absent — which is how a PMG *add*
# gets no empty heading, its `matches` being empty because the address was not on the list and
# that absence being the before-state itself.
EVIDENCE_HEADING: Final = "evidence_heading"

# Required on every gate call, checked at the one seam below rather than per tool.
REQUIRED_EVIDENCE_KEYS: Final[tuple[str, ...]] = (EVIDENCE_HEADLINE, EVIDENCE_ASKED)

# One structured event per opened request, so an operator asking "why is there a card waiting" is
# answerable from the logs. Identifiers only — never the arguments, never the evidence.
LOG_CHANGE_REQUEST_OPENED: Final = "mcp_change_request_opened"

# The write failed and the change was refused. NOA declined to run something it could not
# record as pending.
LOG_CHANGE_GATE_WRITE_FAILED: Final = "mcp_change_gate_write_failed"

# Where the approval card is served from, under `NOA_EMBED_BASE_URL`. The route itself is
# `apps/web-embed/src/app/approvals/[id]/page.tsx` — two languages, so this constant and that
# directory cannot be checked against each other by a compiler. Named here rather than
# formatted inline so the one place it has to be edited is findable from the route's name.
APPROVAL_CARD_PATH: Final = "/approvals"

# The MCP resource identifier for an approval card. `ui://` is not decoration: LibreChat's
# parser is what classifies a resource as a UI resource, and it classifies on this scheme
# (`packages/api/src/mcp/parsers.ts:183` at pin `45cc53c4`). A resource without it
# arrives as an ordinary attachment and never renders.
UI_RESOURCE_URI_PREFIX: Final = "ui://noa/approval/"


class ChangeGateBranch(StrEnum):
    """The three shapes for a CHANGE tool result.

    All three are named, including the one that does not exist yet, because "three branches"
    is the invariant and a branch that is merely absent reads as a branch nobody thought of.
    `build_change_gate_response` refuses `ELICITATION` rather than pretending — see
    `ChangeGateBranchUnavailableError`.
    """

    # The plain address in the text block, and nothing else. This is what a client that
    # renders no UI resource sees, and what remains if a LibreChat bump fails the render-path
    # re-verification — at which point it becomes the primary surface again.
    LINK_OUT = "link_out"
    # The iframe, plus the link-out text beside it. Both, never one.
    UI_RESOURCE = "ui_resource"
    # MCP elicitation. Future: it would move the decision into the client's own prompt, which
    # is a different answer to "where does the operator type" and a different threat model
    # for the cookie/CSRF boundary. Declared, not built.
    ELICITATION = "elicitation"


# The branch NOA ships, and it was **selected by measurement rather than assumed**: the render gate
# ran against LibreChat pin `45cc53c4` on 2026-08-08 and the `text/uri-list` resource rendered
# as an iframe `src` on NOA's origin, with an in-frame authenticated `POST` returning 200
# . Swapping this constant is the whole upgrade path — nothing behind the gate changes.
ACTIVE_CHANGE_GATE_BRANCH: Final = ChangeGateBranch.UI_RESOURCE

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PendingChangeRequest:
    """The request the gate just opened — what the change gate turns into a tool result.

    `status` is carried rather than assumed so a caller reads the state that was written
    instead of hardcoding the word "pending" in a second place; it is `PENDING` for every
    row this module can produce (`core.approvals.repository` writes no other).

    No `reason` field, and there is nowhere for one to be added: the reason is typed by an
    operator on the approval card after this value has been rendered and forgotten.
    """

    action_request_id: UUID
    status: ActionRequestStatus
    expires_at: datetime


def assert_no_reason_argument(arguments: Mapping[str, Any]) -> None:
    """Refuse arguments that carry a reason under any of its spellings.

    The reason rule puts the boundary on the *schema*: a CHANGE tool must not declare a reason
    parameter of any name, so the LLM is never asked to author one and never has one to relay. This
    is that boundary enforced at the door the schema leads to, which is what makes it hold for a
    tool built later by someone who read the tool next to it rather than the spec.

    Top level only, unlike redaction's recursive walk. A reason is a first-class parameter of
    a tool call or it is not a reason — `{"account": {"reason": ...}}` is an integration's
    own payload shape, and refusing on it would make the guard a source of false refusals
    for CHANGEs that have nothing to do with the reason boundary.
    """
    offending = sorted(
        key for key in arguments if str(key).strip().lower() in FORBIDDEN_REASON_KEYS
    )
    if offending:
        raise ChangeReasonForbiddenError(
            f"CHANGE arguments carried reason-shaped key(s) {offending}; the reason is "
            "operator-typed at approve time"
        )


def assert_evidence_usable(evidence: Mapping[str, Any], *, tool_name: str) -> None:
    """Refuse a gate call whose evidence is missing or cannot describe the card.

    Two refusals under one error, because they are one failure: a card that asks for
    authorisation and describes nothing. Empty evidence is the original case. The two required
    keys are the second — without them the card falls back to a humanised tool name, which is a
    heading that reads like a heading and tells an operator nothing about what they are
    approving, and nothing errors.

    **At the mechanism, not per tool.** Every CHANGE tool reaches the gate through
    `open_change_request`, so a tool built later by someone who read the tool beside it rather
    than the spec fails here, with a sentence naming the key it forgot, rather than shipping a
    card an operator reads a tool name off. A per-tool check is a check the next tool forgets.

    Blank is missing: a key present with an empty or whitespace-only string renders as an empty
    heading, which is the same card with a less findable cause.
    """
    if not evidence:
        raise ChangeEvidenceRequiredError(
            f"`{tool_name}` opened the gate with no preflight evidence"
        )
    missing = [
        key
        for key in REQUIRED_EVIDENCE_KEYS
        if not isinstance(evidence.get(key), str) or not str(evidence.get(key)).strip()
    ]
    if missing:
        raise ChangeEvidenceRequiredError(
            f"`{tool_name}` opened the gate with no {' and no '.join(missing)} in its evidence"
        )


def build_approval_context(
    *,
    arguments: Mapping[str, Any],
    evidence: Mapping[str, Any],
    requester_email: str,
    librechat_user_id: str,
) -> dict[str, Any]:
    """The payload persisted on the row, built once, at gate time.

    **It holds only what no column holds.** `tool_name`, `conversation_ref`,
    `requested_by_user_id` and the created-at stamp are all columns on `action_requests`,
    and copying any of them in here would be two records of one moment that can
    disagree — which is the table's own argument for dropping `args`, `risk`, `decided_by_user_id`
    and `updated_at` from that table. What is left is the three things the row cannot say:

    - `arguments` — what the model asked for, redacted through the same function the audit
      path uses. CHANGE arguments should carry no credential by construction (passwords are
      generated server-side, plaintext never past the LLM), so this is a net rather than a fix, and
      the net is where a future tool's argument name lands.
    - `requester` — the email and the LibreChat account behind the call. The user id is the
      FK column; these two are not, and they are what the card shows as provenance and
      origin. Persisted rather than joined at render time because the FK is `SET NULL`
      — a deleted operator would otherwise erase the identity from a decision that was made.
    - `evidence` — the in-process preflight. Never from the transcript, never from
      a tool argument: it is born inside the CHANGE call, milliseconds old, same user.

    Plain `dict`s and JSON-native values throughout: this lands in JSONB and comes back as
    ordinary Python, so a `Mapping` subclass or a `datetime` handed in here would round-trip
    into something a comparison against the original would not match.

    The three keys come from `core.approvals.context`, which is also where the readers get
    them (the decision endpoint, the result tool). A misspelt key in JSONB reads as an absent one
    and answers `{}`, so the writer and its readers naming one constant is what keeps that
    from being silent.
    """
    redacted_arguments = redact_sensitive_data(dict(arguments))
    return {
        CONTEXT_ARGUMENTS_KEY: redacted_arguments if isinstance(redacted_arguments, dict) else {},
        CONTEXT_REQUESTER_KEY: {
            "email": requester_email,
            "librechat_user_id": librechat_user_id,
        },
        CONTEXT_EVIDENCE_KEY: dict(evidence),
    }


async def open_change_request(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: McpToolContext,
) -> PendingChangeRequest:
    """Open the approval question for one CHANGE call; never answer it.

    Order is load-bearing: both guards run before anything is written, so a refused call
    leaves no row an operator has to wonder about and no card that cannot be described.

    `expires_at` comes from `APPROVAL_PENDING_TTL_SECONDS` on the tool context. The column is
    NOT NULL by the table's design — a request without a deadline cannot expire, and "pending
    forever" is the state the TTL removes — so this is the one field the gate computes rather
    than receives.

    Raises `ChangeReasonForbiddenError`, `ChangeEvidenceRequiredError` or
    `ChangeGateUnavailableError`; all three are `NoaError`s, so `sanitize_tool_errors` hands
    the model the code that names the cause rather than a generic failure.
    """
    assert_no_reason_argument(arguments)
    assert_evidence_usable(evidence, tool_name=tool_name)

    identity = current_mcp_identity()
    conversation_ref = read_conversation_ref()
    expires_at = datetime.now(UTC) + timedelta(seconds=context.pending_ttl_seconds)
    approval_context = build_approval_context(
        arguments=arguments,
        evidence=evidence,
        requester_email=identity.email,
        librechat_user_id=identity.librechat_user_id,
    )

    action_request_id = await _write_pending(
        context,
        tool_name=tool_name,
        requested_by_user_id=identity.user_id,
        conversation_ref=conversation_ref,
        approval_context=approval_context,
        expires_at=expires_at,
    )

    logger.info(
        LOG_CHANGE_REQUEST_OPENED,
        tool=tool_name,
        action_request_id=str(action_request_id),
        requested_by_user_id=str(identity.user_id),
        conversation_ref=conversation_ref,
        expires_at=expires_at.isoformat(),
    )
    return PendingChangeRequest(
        action_request_id=action_request_id,
        status=ActionRequestStatus.PENDING,
        expires_at=expires_at,
    )


def approval_card_url(action_request_id: UUID, *, embed_base_url: str) -> str:
    """The address of one approval card.

    **The id and nothing else.** No token, no tool name, no arguments: the tool result this
    ends up in persists in LibreChat's MongoDB, so everything in this string is readable by a
    LibreChat administrator forever. Authorisation to see the card behind it is the
    `noa_session` cookie plus the requester-match, evaluated when the card is fetched — the
    URL is a name, not a key, and a URL that were a key would be one an operator could paste
    into a chat.

    The join itself is `noa_api.mcp_tools.ui_resource.embed_url`, shared with the table
    surface since that landed — it strips a trailing slash even though `core.config`
    already does, because both are also called with literals in tests and one doubled slash is
    the kind of thing that only shows up in front of an operator.
    """
    return embed_url(
        embed_base_url=embed_base_url,
        path=APPROVAL_CARD_PATH,
        identifier=str(action_request_id),
    )


def build_change_gate_response(
    request: PendingChangeRequest,
    *,
    tool_name: str,
    context: McpToolContext,
    branch: ChangeGateBranch = ACTIVE_CHANGE_GATE_BRANCH,
) -> ToolResult:
    """Shape every CHANGE tool's result.

    One function for all of them, so the approval surface cannot be spelled one way in
    `whm_suspend_account` and another in `pmg_whitelist`. A CHANGE tool's body is two calls:
    `open_change_request` writes the row, this turns the row's id into the surface.

    **The active branch ships both halves, and that is not belt-and-braces.** The iframe is where an
    operator decides, and the plain address in the text block is what remains when the frame does
    not load — the link-out is permanent rather than a fallback that exists only on paper. It is an
    *address*, not a link, deliberately: the 401 card measured that a `target="_blank"` clicked
    inside the frame opens nothing at all under `ToolCallInfo`'s sandbox, silently, and that a tab
    it does open elsewhere inherits the frame's flags — a popup inherits the sandbox, so the address
    ships as text too. Text a human can copy is the one door upstream cannot withhold.

    **What is not in here.** No arguments, no preflight evidence, no requester: all three are on the
    row (`build_approval_context`) and the card reads them behind the operator's cookie. Putting any
    of them here would publish them to the transcript, which is the thing the id-only URL assumes a
    LibreChat administrator can read. And nothing about *why* — that word is born when an operator
    types it on the card, after this result has been rendered and forgotten, and the model-facing
    safety text keeps it out for the same reason: a model that is told the field exists is a model
    that can be talked into filling it.

    No `structured_content`. The live render gate measured a content-only result end to end; an
    envelope beside it would be a third place the URL lives and a shape nothing has rendered.
    """
    url = approval_card_url(request.action_request_id, embed_base_url=context.embed_base_url)
    text = TextContent(
        type="text",
        text=_change_gate_text(request, tool_name=tool_name, url=url),
    )

    if branch is ChangeGateBranch.LINK_OUT:
        return ToolResult(content=[text])
    if branch is ChangeGateBranch.UI_RESOURCE:
        return ToolResult(content=[text, _approval_ui_resource(request, url=url)])
    raise ChangeGateBranchUnavailableError(
        f"`{tool_name}` asked for the `{branch.value}` change-gate branch, which is declared "
        "but not built"
    )


def _change_gate_text(request: PendingChangeRequest, *, tool_name: str, url: str) -> str:
    """The text block, identical on every branch that has one.

    One wording rather than one per branch, so "the text always carries the address" is a
    single claim about a single string. It reads correctly whether or not a card rendered
    beside it, which is exactly the case it has to cover.

    Four things it says, and the last two are said to the model rather than to the operator:
    nothing has run, here is the address, here is the deadline, and the outcome is read from
    NOA rather than assumed ("may this run?" is answered by a row, never by a claim).
    """
    return (
        f"Approval required. `{tool_name}` changes a live system, so NOA opened approval "
        f"request {request.action_request_id} and ran nothing.\n\n"
        f"Approve or deny on the NOA approval card: {url}\n"
        "If the card does not open here, paste that address into a browser tab.\n\n"
        f"The request expires at {request.expires_at.isoformat()}. Read the outcome with "
        "`noa_get_action_result`; never report a change as done without it."
    )


def _approval_ui_resource(request: PendingChangeRequest, *, url: str) -> EmbeddedResource:
    """The iframe half, in the shape the render gate measured live.

    Three fields and all three are load-bearing together: the `ui://` scheme is what makes
    LibreChat treat this as a UI resource at all, `text/uri-list` is what makes mcp-ui render
    it as an iframe `src` instead of `srcDoc`, and the body is the URL because that is what a
    uri-list *is*. Change any one and the card either does not render or renders on an opaque
    origin where the session cookie cannot follow. The shape lives in
    `noa_api.mcp_tools.ui_resource`; what is local here is which `ui://` name this surface has.
    """
    return build_ui_resource(
        uri=f"{UI_RESOURCE_URI_PREFIX}{request.action_request_id}",
        url=url,
    )


async def _write_pending(
    context: McpToolContext,
    *,
    tool_name: str,
    requested_by_user_id: UUID,
    conversation_ref: str | None,
    approval_context: dict[str, Any],
    expires_at: datetime,
) -> UUID:
    """Commit the PENDING row, or refuse the change.

    Fail-closed, and `Exception` rather than a driver-specific class for the same reason the
    audit path catches broadly: every way this can fail ends in "no authorization row", and
    the verdict has no truthful answer without one. The cause is logged and dropped rather than
    returned — a connection string or a constraint name in front of the model is exactly what
    the error envelope closes.

    Its own session, opened and closed here: the tool has none open by this point (a pooled
    connection held across the preflight's hop to someone else's host is how a slow remote
    becomes a database outage — the account search's rule).
    """
    try:
        async with context.session_factory() as session:
            repository = context.action_request_repository_factory(session)
            action_request_id = await repository.create_pending(
                tool_name=tool_name,
                requested_by_user_id=requested_by_user_id,
                conversation_ref=conversation_ref,
                approval_context=approval_context,
                expires_at=expires_at,
            )
            await repository.commit()
            return action_request_id
    except Exception as exc:
        logger.error(
            LOG_CHANGE_GATE_WRITE_FAILED,
            tool=tool_name,
            cause=type(exc).__name__,
            detail=str(exc),
        )
        raise ChangeGateUnavailableError(
            f"`action_requests` INSERT for `{tool_name}` failed: {type(exc).__name__}"
        ) from exc
