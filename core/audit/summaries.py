"""Turning a tool result into an audit row's `result_summary`.

Written for the READ path and hoisted here, because the post-approval executor
records the same field from the same envelope and two spellings of "bounded, redacted, compact
JSON" is one too many. `noa_api.mcp_audit` imports both functions and re-exports them,
so its own callers and tests keep naming one thing.

**The envelope is the contract.** Every exposed tool answers `{"ok": bool, ...}`
(`noa_api.mcp_tools.results`), and `sanitize_tool_errors` turns an exception into `ok: False`
rather than a raise — so `ok` is what a status is read off, and a payload that cannot be
read as a success is not evidence of one.

**Redaction happens here even though the callers redact too.** `tool_runs.result_summary`
outlives the call and is read by the admin audit surface and, for an approved change,
by the card and by `noa_get_action_result`. A per-caller exemption is how one of them
eventually writes a credential into the audit trail.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from core.db.lifecycle import ToolRunStatus
from core.secrets.redaction import redact_sensitive_data

# `tool_runs.result_summary` is `String(2000)`. Truncation happens here rather than at the
# column so the stored value is a marked ellipsis instead of a silently clipped one.
MAX_RESULT_SUMMARY_LENGTH: Final = 2000

TRUNCATION_SUFFIX: Final = "..."


def result_summary(payload: Mapping[str, Any] | None) -> str | None:
    """A bounded, redacted rendering of what a tool answered.

    Compact JSON so the 2000 characters hold as much of the result as possible, `default=str`
    so a stray non-serializable value degrades to its repr instead of raising inside the
    audit path.
    """
    if payload is None:
        return None

    rendered = json.dumps(redact_sensitive_data(dict(payload)), separators=(",", ":"), default=str)
    if len(rendered) <= MAX_RESULT_SUMMARY_LENGTH:
        return rendered
    return rendered[: MAX_RESULT_SUMMARY_LENGTH - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX


def status_for_payload(payload: Mapping[str, Any] | None) -> ToolRunStatus:
    """COMPLETED or FAILED, read off the result envelope.

    `ok` is the one field every tool result carries, and `sanitize_tool_errors` turns an
    exception into `ok: False` rather than a raise — so without this branch every failed call
    would be recorded as a success. An ambiguity result is `ok: False` too, and is
    recorded FAILED on purpose: the call did not do what was asked, and `result_summary`
    carries the `choices` that say why.

    A missing or non-boolean `ok` counts as failure. A result that cannot be read as a
    success is not evidence of one.
    """
    if not payload or payload.get("ok") is not True:
        return ToolRunStatus.FAILED
    return ToolRunStatus.COMPLETED
