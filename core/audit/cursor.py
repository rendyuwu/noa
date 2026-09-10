"""Keyset continuation tokens for the audit list (T55 — §I.admin-api).

Ported from `noa-old` branch `MCP` (`apps/api/src/noa_api/api/pagination.py`) per C13: the
encode/decode/predicate trio and the UTC normalisation are upstream's, and both are the parts
that are easy to get subtly wrong. Two deliberate deviations, both recorded here rather than
left for a reader to notice:

- **A malformed cursor raises `InvalidAuditCursorError`, not `RequestValidationError`.** Upstream
  reused FastAPI's own validation exception so a bad cursor and an out-of-range `limit` answered
  the same 422 with a validation-error list. NOA's error envelope is one shape for every refusal
  (V8, V73): `error_code` + `message` + `request_id`, with diagnostics in `detail` and out of the
  body. `InvalidRoleNameError` already sets that precedent for a malformed *path* param, so this
  follows it rather than introducing a second envelope on the admin surface.
- **The JSON field names are fixed, and `ID_FIELD` is `toolRunId` for every surface.** Upstream
  parameterised them per list (`actionRequestId`, `toolRunId`, …) because it paged four. Two lists
  page through this codec now — `/admin/audit/tool-runs` and `/admin/action-requests` — so the
  name is no longer accurate for one of them: a token minted on the action-request list carries an
  `action_requests.id` under the key `toolRunId`. That is a naming defect and not a functional one.
  The token is opaque base64, both sides read the same constant, and the `id` is only ever compared
  against the column the same statement orders by. Parameterising the key would be a wire-format
  change — every token already handed out still has to decode, so a reader would have to accept
  either spelling and emit the new one — which is more machinery than the defect earns. Two
  consequences a reader should expect: decoding a token by hand shows `toolRunId` for an
  action-request position, and a cursor minted on one list decodes cleanly on the other and pages
  from a meaningless position. Both surfaces are admin-only (V13), so neither is a disclosure.

**Why keyset and not `OFFSET`.** The audit trail is written while it is read: every MCP call
appends a row, so an offset page re-reads rows that shifted under it — an operator paging back
through a busy hour would see the same run twice and miss another. A cursor names the row the
last page ended on, so the next page continues from a position rather than from a count.

The token is **opaque, not signed**. It carries a timestamp and a UUID that the caller already
saw in the page it came from, and it authorises nothing: `require_admin` is the access control
on both audit routes (V13). A signature here would protect a value that is not a secret.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import ColumnElement, and_, or_

from core.audit.errors import InvalidAuditCursorError

# The two keys inside the decoded token. Spelled once: an encoder and a decoder that disagreed
# about a name would make every second page look like the first. `ID_FIELD` names `tool_runs`
# because that was the only list when it was written and it is unparameterised by choice — the
# action-request list pages with the same key over its own ids. See the module docstring.
TIMESTAMP_FIELD: Final = "createdAt"
ID_FIELD: Final = "toolRunId"


@dataclass(frozen=True)
class KeysetCursor:
    """Where the previous page stopped: its last row's ordering timestamp and id.

    Both halves are needed because `created_at` is not unique — two runs started in the same
    millisecond are ordinary, and a cursor holding only the timestamp would either skip the rest
    of a tied group or serve it twice (V92(c), one surface over).
    """

    timestamp: datetime
    entity_id: UUID


def encode_cursor(cursor: KeysetCursor) -> str:
    """The token a client sends back to ask for the next page.

    base64url of compact JSON, padding stripped: it rides in a query string, so `+`, `/` and `=`
    would each need escaping somewhere along the way.
    """
    payload = {
        TIMESTAMP_FIELD: cursor.timestamp.isoformat(),
        ID_FIELD: str(cursor.entity_id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> KeysetCursor:
    """Parse a client-supplied token, or refuse it (400 `invalid_audit_cursor`).

    Every malformed shape lands on one refusal: empty, not base64, not JSON, not an object,
    missing either key, a key of the wrong type, an unparseable timestamp, a non-UUID id. One
    code because there is one remedy — drop the cursor and re-read from the first page — and
    because a cursor is a value NOA handed out, so its contents are not news to the caller.

    A naive timestamp is read as UTC rather than rejected. `tool_runs.created_at` is
    `timestamptz`, so comparing a naive value against it would either raise inside the driver or
    silently shift the page boundary by the server's offset; upstream's normalisation is kept for
    exactly that reason.
    """
    raw_cursor = cursor.strip()
    if not raw_cursor:
        raise InvalidAuditCursorError("empty cursor")

    padded = raw_cursor + "=" * (-len(raw_cursor) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        parsed: Any = json.loads(decoded)
    except Exception as exc:
        raise InvalidAuditCursorError(f"cursor is not base64url JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InvalidAuditCursorError(f"cursor decoded to {type(parsed).__name__}, not an object")

    timestamp_raw = parsed.get(TIMESTAMP_FIELD)
    id_raw = parsed.get(ID_FIELD)
    if not isinstance(timestamp_raw, str) or not isinstance(id_raw, str):
        raise InvalidAuditCursorError(
            f"cursor needs string `{TIMESTAMP_FIELD}` and `{ID_FIELD}` fields"
        )

    try:
        timestamp = datetime.fromisoformat(timestamp_raw)
        entity_id = UUID(id_raw)
    except Exception as exc:
        raise InvalidAuditCursorError(f"cursor fields do not parse: {exc}") from exc

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    else:
        timestamp = timestamp.astimezone(UTC)

    return KeysetCursor(timestamp=timestamp, entity_id=entity_id)


def keyset_predicate(
    *,
    timestamp_column: ColumnElement[datetime],
    id_column: ColumnElement[UUID],
    cursor: KeysetCursor,
) -> ColumnElement[bool]:
    """Strictly-after-the-cursor, for a page ordered `(timestamp DESC, id DESC)`.

    Two branches rather than a row-value comparison: the tie-break has to fire only *within* a
    tied timestamp group, and `(ts, id) < (:ts, :id)` on a nullable-free pair compiles to the
    same thing while reading less clearly against a mixed index.
    """
    return or_(
        timestamp_column < cursor.timestamp,
        and_(
            timestamp_column == cursor.timestamp,
            id_column < cursor.entity_id,
        ),
    )


__all__ = [
    "ID_FIELD",
    "TIMESTAMP_FIELD",
    "KeysetCursor",
    "decode_cursor",
    "encode_cursor",
    "keyset_predicate",
]
