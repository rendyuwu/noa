"""The shape of `action_requests.approval_context`, in one place (T33, T63 — V33, V66).

V33 says the approval payload is built once, at gate time, and persisted as one object rather
than rebuilt when the card renders. That makes this JSONB column a small contract between one
writer and several readers, and the keys are the whole of it:

- `noa_api.mcp_tools.change_gate.build_approval_context` writes it (T33);
- `core.approvals.decisions.LockedActionRequest` reads the arguments into an approved change's
  audit row (T37, V47);
- `core.approvals.results` reads them again for `noa_get_action_result` (T63);
- T41's card reads all three;
- `core.approvals.execution` reads the arguments *and* the evidence, because an approved
  change is executed from what the gate recorded and its receipt's before-state is that same
  preflight (T38, V46).

Four spellings of `"arguments"` across four modules is three chances for one of them to be
wrong in a way nothing catches — the payload is JSONB, so a misspelt key reads as an absent
one and answers `{}` (V66's argument, and the same one that moved `ERROR_UNKNOWN` at T31).

**`arguments_from_context` is the extraction rule, not a `.get()`.** `{}` when the key is
missing *or* is not an object, so a context written by something other than the gate cannot
put a string or a list where a mapping is expected. It matches `tool_runs.args`' own server
default for the reason T35 gives: "took no arguments" and "arguments not recorded" must not
become the same row, and both of them are still an empty mapping to a reader.

Nothing here reads `reason`. It is not part of this payload — it is a column of its own, NULL
until an operator types one into the approval card (C8, V15, V43) — and a helper here would be
the first place someone reached for it from a path that must never see it (T63's tool is
exactly such a path).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# What the model asked for, redacted at the gate (V8). Read by the decision path and by T63.
CONTEXT_ARGUMENTS_KEY: Final = "arguments"

# Who called: email plus LibreChat account. Persisted rather than joined at render time — the
# requester FK is `SET NULL` (T34), so a deleted operator would otherwise erase the identity
# from a decision that was made (V35).
CONTEXT_REQUESTER_KEY: Final = "requester"

# The in-process preflight the CHANGE tool ran (C9, V17). For the approval card and for the
# receipt's before-state — never for a model: V17 says this evidence is born in-process and
# stays out of the transcript.
CONTEXT_EVIDENCE_KEY: Final = "evidence"


def arguments_from_context(approval_context: Mapping[str, Any]) -> dict[str, Any]:
    """The tool arguments as the gate redacted them, or `{}` (V8, V33).

    Redacted once, at the gate, and carried — never re-derived by a reader, which would be a
    second redaction rule to keep in step with the first.
    """
    arguments = approval_context.get(CONTEXT_ARGUMENTS_KEY)
    return dict(arguments) if isinstance(arguments, dict) else {}


def evidence_from_context(approval_context: Mapping[str, Any]) -> dict[str, Any]:
    """The in-process preflight the CHANGE tool ran (C9, V17, V35).

    Written for T41's card and hoisted here at T38, when the executor became its second reader
    — the receipt's before-state is this payload, so the operator reads the same evidence they
    authorised against (V46). `core.approvals.card` re-exports it.

    `{}` when absent or not an object — the gate refuses to open a request with no evidence at
    all (`ChangeEvidenceRequiredError`), so an empty payload here means the row predates that
    guard or was written by something else, and either way the honest answer is "nothing
    recorded" rather than an exception in front of an operator.
    """
    evidence = approval_context.get(CONTEXT_EVIDENCE_KEY)
    return dict(evidence) if isinstance(evidence, dict) else {}


__all__ = [
    "CONTEXT_ARGUMENTS_KEY",
    "CONTEXT_EVIDENCE_KEY",
    "CONTEXT_REQUESTER_KEY",
    "arguments_from_context",
    "evidence_from_context",
]
