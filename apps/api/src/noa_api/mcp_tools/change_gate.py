"""The CHANGE gate: a `tools/call` opens a request, it never runs a change (T33).

V16 splits the tool surface in two. A READ executes immediately and its `tool_runs` row is
written around it by `ToolRunAuditMiddleware` (T73). A CHANGE does not execute at all: it
runs its preflight in-process (C9, V17), calls `open_change_request` with the evidence that
produced, and returns the approval surface T32 shapes. Nothing in this module can execute
anything, and that is the point — the code path an LLM can reach ends at an INSERT.

**Why a function and not a middleware.** Every other cross-cutting rule on the tool path is
a `Middleware` precisely because a tool cannot forget one (V83b: RBAC, then audit). This one
cannot be, and the reason is C9: the evidence the approval card shows is the tool's own
in-process preflight, and a middleware sits outside the tool and has none. What replaces
"a tool cannot forget it" is narrower but real — this is the only writer of a PENDING row
(`core.approvals.repository` writes no other status), so a CHANGE tool that skipped the gate
and executed would have no authorization row at all, and V23 reads the row.

**Three refusals, and all three refuse before the INSERT** (`core.approvals.errors`):

- an argument that reads as a reason (C8, V15, V43) — see `assert_no_reason_argument`;
- no preflight evidence (V17, V35) — a card that asks for authorisation and describes
  nothing is the state V35 exists to prevent;
- the write itself failing, which refuses the change rather than letting it proceed
  unrecorded (T73(c)'s fail-closed argument, one table over).

**The caller is read here, not passed in.** `current_mcp_identity()` for who, and
`read_conversation_ref()` for the grouping label — the same function `noa_api.mcp_audit`
uses, imported rather than re-implemented, because `conversation_ref` is one audit label
with one home (V66, V47). A tool that could pass either would be a tool that could get
either wrong: `requested_by_user_id` is what V27's requester-match compares the approving
operator against, so an argument-supplied requester is an argument-supplied authorization.

**`status` is never an input.** No parameter here, and no branch, admits a claim that a
change is already approved. A repeated call with an APPROVED request's id in its arguments
opens a *new* PENDING request, because V23 answers "may this run?" from the row's own
`status` column and from nowhere else — not from an LLM claim, not from a tool argument.
Execution after approval is T37's endpoint and T38's executor, reached by a cookie POST from
a NOA-origin document (V22), which is a path that does not pass through here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import structlog

from core.approvals.errors import (
    ChangeEvidenceRequiredError,
    ChangeGateUnavailableError,
    ChangeReasonForbiddenError,
)
from core.db.lifecycle import ActionRequestStatus
from core.secrets.redaction import redact_sensitive_data
from noa_api.mcp_audit import read_conversation_ref
from noa_api.mcp_request_auth import current_mcp_identity
from noa_api.mcp_tools.context import McpToolContext, build_action_request_repository

# Argument names that would carry an LLM-authored reason (C8, V43). An explicit set, not a
# substring rule: `reason` has to be refused, while a legitimate argument such as
# `reason_code` on some future integration is not what C8 is about, and a rule that guessed
# would eventually refuse a change for the wrong cause. Compared case-insensitively and
# stripped, the way `core.secrets.redaction.is_sensitive_key` compares.
FORBIDDEN_REASON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "reason",
        "proposed_reason",
        "change_reason",
        "approval_reason",
        "justification",
    }
)

# One structured event per opened request, so an operator asking "why is there a card
# waiting" is answerable from the logs. Identifiers only — never the arguments, never the
# evidence (V8).
LOG_CHANGE_REQUEST_OPENED: Final = "mcp_change_request_opened"

# The write failed and the change was refused. NOA declined to run something it could not
# record as pending.
LOG_CHANGE_GATE_WRITE_FAILED: Final = "mcp_change_gate_write_failed"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PendingChangeRequest:
    """The request the gate just opened — what T32 turns into a tool result.

    `status` is carried rather than assumed so a caller reads the state that was written
    instead of hardcoding the word "pending" in a second place; it is `PENDING` for every
    row this module can produce (`core.approvals.repository` writes no other).

    No `reason` field, and there is nowhere for one to be added: the reason is typed by an
    operator on the approval card after this value has been rendered and forgotten (C8, V43).
    """

    action_request_id: UUID
    status: ActionRequestStatus
    expires_at: datetime


def assert_no_reason_argument(arguments: Mapping[str, Any]) -> None:
    """Refuse arguments that carry a reason under any of its spellings (C8, V15, V43).

    C8 puts the boundary on the *schema*: a CHANGE tool must not declare a reason parameter
    of any name, so the LLM is never asked to author one and never has one to relay. This is
    that boundary enforced at the door the schema leads to, which is what makes it hold for
    a tool built later by someone who read the tool next to it rather than the spec.

    Top level only, unlike redaction's recursive walk. A reason is a first-class parameter of
    a tool call or it is not a reason — `{"account": {"reason": ...}}` is an integration's
    own payload shape, and refusing on it would make the guard a source of false refusals
    for CHANGEs that have nothing to do with C8.
    """
    offending = sorted(
        key for key in arguments if str(key).strip().lower() in FORBIDDEN_REASON_KEYS
    )
    if offending:
        raise ChangeReasonForbiddenError(
            f"CHANGE arguments carried reason-shaped key(s) {offending}; the reason is "
            "operator-typed at approve time (C8, V43)"
        )


def build_approval_context(
    *,
    arguments: Mapping[str, Any],
    evidence: Mapping[str, Any],
    requester_email: str,
    librechat_user_id: str,
) -> dict[str, Any]:
    """The payload V33 persists on the row, built once, at gate time.

    **It holds only what no column holds.** `tool_name`, `conversation_ref`,
    `requested_by_user_id` and the created-at stamp are all columns on `action_requests`
    (T34), and copying any of them in here would be two records of one moment that can
    disagree — which is T34's own argument for dropping `args`, `risk`, `decided_by_user_id`
    and `updated_at` from that table. What is left is the three things the row cannot say:

    - `arguments` — what the model asked for, redacted through the same function the audit
      path uses (V8, V66). CHANGE arguments should carry no credential by construction
      (C15/V49 generate passwords server-side), so this is a net rather than a fix, and the
      net is where a future tool's argument name lands.
    - `requester` — the email and the LibreChat account behind the call. The user id is the
      FK column; these two are not, and they are what V35's card shows as provenance and
      origin. Persisted rather than joined at render time because the FK is `SET NULL` (T34)
      — a deleted operator would otherwise erase the identity from a decision that was made.
    - `evidence` — the in-process preflight (C9, V17). Never from the transcript, never from
      a tool argument: it is born inside the CHANGE call, milliseconds old, same user.

    Plain `dict`s and JSON-native values throughout: this lands in JSONB and comes back as
    ordinary Python, so a `Mapping` subclass or a `datetime` handed in here would round-trip
    into something a comparison against the original would not match.
    """
    redacted_arguments = redact_sensitive_data(dict(arguments))
    return {
        "arguments": redacted_arguments if isinstance(redacted_arguments, dict) else {},
        "requester": {
            "email": requester_email,
            "librechat_user_id": librechat_user_id,
        },
        "evidence": dict(evidence),
    }


async def open_change_request(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    evidence: Mapping[str, Any],
    context: McpToolContext,
) -> PendingChangeRequest:
    """Open the approval question for one CHANGE call; never answer it (T33, V16, V23).

    Order is load-bearing: both guards run before anything is written, so a refused call
    leaves no row an operator has to wonder about and no card that cannot be described.

    `expires_at` comes from `APPROVAL_PENDING_TTL_SECONDS` on the tool context. The column is
    NOT NULL by T34's design — a request without a deadline cannot expire, and "pending
    forever" is the state V32 removes — so this is the one field the gate computes rather
    than receives.

    Raises `ChangeReasonForbiddenError`, `ChangeEvidenceRequiredError` or
    `ChangeGateUnavailableError`; all three are `NoaError`s, so `sanitize_tool_errors` hands
    the model the code that names the cause rather than a generic failure (V19).
    """
    assert_no_reason_argument(arguments)
    if not evidence:
        raise ChangeEvidenceRequiredError(
            f"`{tool_name}` opened the gate with no preflight evidence (C9, V17, V35)"
        )

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


async def _write_pending(
    context: McpToolContext,
    *,
    tool_name: str,
    requested_by_user_id: UUID,
    conversation_ref: str | None,
    approval_context: dict[str, Any],
    expires_at: datetime,
) -> UUID:
    """Commit the PENDING row, or refuse the change (V23).

    Fail-closed, and `Exception` rather than a driver-specific class for the same reason the
    audit path catches broadly: every way this can fail ends in "no authorization row", and
    V23 has no truthful answer without one. The cause is logged and dropped rather than
    returned — a connection string or a constraint name in front of the model is exactly what
    V8 closes.

    Its own session, opened and closed here: the tool has none open by this point (a pooled
    connection held across the preflight's hop to someone else's host is how a slow remote
    becomes a database outage — T21's rule).
    """
    try:
        async with context.session_factory() as session:
            repository = build_action_request_repository(context, session)
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


__all__ = [
    "FORBIDDEN_REASON_KEYS",
    "LOG_CHANGE_GATE_WRITE_FAILED",
    "LOG_CHANGE_REQUEST_OPENED",
    "PendingChangeRequest",
    "assert_no_reason_argument",
    "build_approval_context",
    "open_change_request",
]
