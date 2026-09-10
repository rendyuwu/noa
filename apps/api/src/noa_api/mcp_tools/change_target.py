"""What every post-approval runner shares.

Three things: the values a runner re-derives from the evidence, the refusals it makes when it
cannot, and the words it answers with.

A CHANGE runner acts on the **evidence** the operator approved against, never on the arguments
the model supplied: `server_ref` is a string an LLM passed and inventory can be edited
between a request and its approval, while `evidence["server_id"]` is the machine the preflight
actually read and the operator actually saw on the card. That rule produces the same two
refusals in every runner — the evidence no longer carries a usable value, or the row it names is
gone — so the codes and the sentences live here rather than once per system.

Born at T22 inside `whm_account_change`, hoisted at T25 when the firewall runner became the
second caller. `whm_account_change` re-exports both names, so the tools and tests that already
reach for them there keep one import path.

**Why the evidence is refused rather than repaired.** It round-tripped through JSONB, and a
value that no longer parses is a request NOA declines instead of guessing at: by the time a
runner reads it, an operator has already typed a reason and pressed Approve, so a guess here is
a guess with an authorisation attached to it.

Two codes, because two remedies. `whm_server_unavailable` sends an administrator to inventory —
the server row was deleted after the request was opened. `change_evidence_unusable` says the
approved request itself is no longer runnable, which is a NOA bug rather than anything an
operator can fix on a server, and the sentence says so without naming the field (V8: the detail
goes to the log, not to the card).

**Only one of those two is universal, and T27 is where that showed.** The first Proxmox runner
shares `change_evidence_unusable` exactly — same remedy, same sentence, no system in it — and
does *not* share the server pair: `whm_server_unavailable` names the table an administrator has
to go and look at, and `proxmox_servers` is a different one. So the code stays per-system
(`noa_api.mcp_tools.proxmox_password.ERROR_SERVER_UNAVAILABLE`) and only the noun differs
between the two sentences. Duplicating a constant whose *value* is the system's name is not what
V66 is about; collapsing them would be, because it would make one code answer for two
inventories.

**The status words are here for the same reason the codes are.** `changed` and `no_op` land in
`tool_runs.result_summary` and in a receipt an operator reads, so two runners spelling one of
them differently is two vocabularies in one audit trail. They were T22's; T25 is the
second speaker.

`unavailable` was here too and moved to `core.approvals.delta`, which now owns the four states
verification can hold. Same value, same name, re-exported from here — the move is that a
runner's payload and the delta it publishes beside that payload read one definition of "the
postflight could not answer" instead of two.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from core.approvals.delta import (
    # Hoisted to `core.approvals.delta` when the receipt's delta became the second speaker of
    # this word. Its value is unchanged and it is re-exported below, so every runner and
    # test that reads it from here keeps one import path — but there is now exactly one
    # definition of "the postflight could not answer", shared by the payload a runner returns
    # and the delta it states beside it, and the two cannot drift into two spellings.
    VERIFICATION_UNAVAILABLE,
)

# The approved change names a server that is no longer resolvable — deleted, or the evidence no
# longer parses. Distinct from the tool-time resolution failures, which the model can fix by
# asking again: by the time this fires an operator has already approved something.
ERROR_SERVER_UNAVAILABLE = "whm_server_unavailable"

MESSAGE_SERVER_UNAVAILABLE = (
    "The WHM server this change was approved for is no longer available. Contact an administrator."
)

# The evidence carries a value the runner cannot act on — a target or a duration that did not
# survive its JSONB round trip as the type the gate wrote. Separate from the code above because
# the remedy is: ask for the change again, and tell an administrator if it recurs.
ERROR_EVIDENCE_UNUSABLE = "change_evidence_unusable"

MESSAGE_EVIDENCE_UNUSABLE = (
    "NOA cannot run this change: what was approved was not recorded in a runnable form. Ask for "
    "the change again; contact an administrator if this continues."
)

# The target was already in the state the change would produce when the preflight looked:
# nothing was asked of an operator and nothing was changed.
STATUS_NO_OP = "no_op"
STATUS_CHANGED = "changed"


def uuid_or_none(value: Any) -> UUID | None:
    """One evidence value as a `UUID`, or `None` when it is not one.

    It round-tripped through JSONB as a string, and a value that no longer parses is a request
    NOA refuses rather than guesses at.
    """
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


__all__ = [
    "ERROR_EVIDENCE_UNUSABLE",
    "ERROR_SERVER_UNAVAILABLE",
    "MESSAGE_EVIDENCE_UNUSABLE",
    "MESSAGE_SERVER_UNAVAILABLE",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "VERIFICATION_UNAVAILABLE",
    "uuid_or_none",
]
