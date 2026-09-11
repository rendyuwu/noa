"""What every post-approval runner shares.

Three things: the values a runner re-derives from the evidence, the refusals it makes when it
cannot, and the words it answers with.

A CHANGE runner acts on the **evidence** the operator approved against, never on the arguments
the model supplied: `server_ref` is a string an LLM passed and inventory can be edited
between a request and its approval, while `evidence["server_id"]` is the machine the preflight
actually read and the operator actually saw on the card. That rule produces the same two
refusals in every runner — the evidence no longer carries a usable value, or the row it names is
gone — so the codes and the sentences live here rather than once per system.

Born inside `whm_account_change` with the suspend runner, hoisted when the firewall runner became
the second caller. `whm_account_change` re-exports both names, so the tools and tests that already
reach for them there keep one import path.

**Why the evidence is refused rather than repaired.** It round-tripped through JSONB, and a
value that no longer parses is a request NOA declines instead of guessing at: by the time a
runner reads it, an operator has already typed a reason and pressed Approve, so a guess here is
a guess with an authorisation attached to it.

Two codes, because two remedies. `whm_server_unavailable` sends an administrator to inventory —
the server row was deleted after the request was opened. `change_evidence_unusable` says the
approved request itself is no longer runnable, which is a NOA bug rather than anything an
operator can fix on a server, and the sentence says so without naming the field (the detail
goes to the log, not to the card).

**Only one of those two is universal, and the password-reset runner is where that showed.** The
first Proxmox runner shares `change_evidence_unusable` exactly — same remedy, same sentence, no
system in it — and does *not* share the server pair: `whm_server_unavailable` names the table an
administrator has to go and look at, and `proxmox_servers` is a different one. So the code stays
per-system (`noa_api.mcp_tools.proxmox_password.ERROR_SERVER_UNAVAILABLE`) and only the noun differs
between the two sentences. Duplicating a constant whose *value* is the system's name is not what the
shared-helpers rule is about; collapsing them would be, because it would make one code answer for
two inventories.

**The status words are here for the same reason the codes are.** `changed` and `no_op` land in
`tool_runs.result_summary` and in a receipt an operator reads, so two runners spelling one of
them differently is two vocabularies in one audit trail. They were the suspend runner's; the
firewall runner is the second speaker.

`unavailable` was here too and moved to `core.approvals.delta`, which now owns the four states
verification can hold. Same value, same name, re-exported from here — the move is that a
runner's payload and the delta it publishes beside that payload read one definition of "the
postflight could not answer" instead of two.

**A failed write is not an answer yet.** Every runner here re-reads its target after it writes.
When the write itself fails, that reading is still available and is the better witness of what
the remote holds, so a runner consults it once and then reports — which is why three things here
are shared: one predicate that says whether a failure was the remote refusing or the remote never
answering, one resolver that turns that plus the reading into a verification state, and one
sentence builder that says the same thing to an operator. What stays per runner is the *payload*
— its keys, its facets, the noun it calls its target by — and a helper taking a confirm callable
for all seven would be abstracting four call sites with three different confirm semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from uuid import UUID

from core.approvals.delta import (
    # Hoisted to `core.approvals.delta` when the receipt's delta became the second speaker of
    # this word. Its value is unchanged and it is re-exported below, so every runner and
    # test that reads it from here keeps one import path — but there is now exactly one
    # definition of "the postflight could not answer", shared by the payload a runner returns
    # and the delta it states beside it, and the two cannot drift into two spellings.
    VERIFICATION_MISMATCH,
    VERIFICATION_UNAVAILABLE,
    VERIFICATION_VERIFIED,
)
from noa_api.mcp_tools.results import ERROR_TIMEOUT, ERROR_UNKNOWN

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


# The error codes that mean NOA never learned what the remote did, as against the remote
# answering that it did nothing.
#
# Closed and small on purpose: these four are the whole vocabulary a transport has for a
# non-answer — the deadline passed, the connection never carried a reply, the reply did not
# parse, or a task the write handed off never reached a terminal state. Every other code a
# runner can see was minted by an integration to name something a remote *said*, so a code
# absent from this set reads as an explicit refusal.
#
# **Four of the five are bound to a producer, one is not, and the difference is stated rather
# than left to read as coverage.** `timeout` and `request_failed` are driven out of the
# real `WHMClient` by `test_confirm_after_failed_write.py`, `ssh_timeout` out of the real
# `ssh_exec` by `test_remote_exec_ssh.py`, and both `task_timeout` constants are asserted into
# this set by their owners. Nothing binds `invalid_response` to any of the three places that mint
# it, and nothing binds Proxmox's own `timeout`/`request_failed`/`invalid_response` — that client
# spells its codes in its own module (`core.integrations.proxmox.client`), so a rename there
# reclassifies a non-answer as a refusal and no test in this repo goes red.
NON_ANSWER_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        ERROR_TIMEOUT,
        # `httpx.RequestError` — the connection broke before an answer arrived. Both HTTP clients
        # mint this literal (`core.integrations.whm.client`, `core.integrations.proxmox.client`);
        # only WHM's is exercised against this set.
        "request_failed",
        # The remote answered and NOA could not read the answer, which says nothing about what
        # the remote did. Three producers, none of them bound here: both HTTP clients, and the
        # VM-interface runner for a config that came back with no digest.
        "invalid_response",
        # `core.remote_exec.ssh` raises this for a **command** that timed out as well as for a
        # connection that did — the command it belongs to may have run to completion on the far
        # side, which is precisely a non-answer. The connect-phase failures keep their own codes
        # (`ssh_connection_failed`, `ssh_auth_failed`, the host-key three) and are refusals in
        # the sense that matters here: nothing was sent.
        "ssh_timeout",
        # A write the remote accepted, whose task NOA stopped waiting on. Spelled here rather
        # than imported, because the two runners that own the constant import this module and
        # the import cannot run both ways; a test asserts both of their constants are in this
        # set, so the literal is bound rather than hand-kept.
        "task_timeout",
    }
)


@dataclass(frozen=True)
class WriteFailure:
    """A CHANGE's write step after it failed, in the two facts a confirming read needs beside it.

    Carried into a runner's postflight rather than returned instead of one. A `_verify_*` that
    runs after a failed write and knows nothing about it emits a clean success and erases the
    failure entirely — no cause, no trace anything went wrong — so the failure travels with the
    reading and shapes the sentence the reading is reported in.

    `code` is the write's own error code, kept because it names the remedy: WHM's on a locked
    suspension, csf's on a refused command. `message` is the remote's sentence where there is
    one, already cut of anything that must not travel back (the firewall pair's is cut at its
    marker before it ever reaches here).
    """

    code: str
    message: str | None = None

    @property
    def refused(self) -> bool:
        """Did the remote answer that it did not act, or did it never answer at all?

        The one decision every CHANGE tool shares here, and the asymmetry below rests entirely
        on it. A remote that refused has told NOA what it did, so a confirming read that agrees
        with the refusal is conclusive. A remote that never answered has told NOA nothing: a
        change that lands a second after NOA stopped waiting reads exactly like one that never
        landed, and reporting the second as "confirmed did not happen" replaces an honest
        unknown with a confident falsehood.

        An unrecognised code reads as a refusal, because that is what an unrecognised code
        almost always is. The non-answers are the transport's, enumerated above and stable,
        while a new code is minted whenever an integration gains a new thing a remote can say.
        """
        return self.code not in NON_ANSWER_ERROR_CODES

    @property
    def verb(self) -> str:
        """How a sentence names this failure, so six runners do not spell one fact six ways."""
        return "refused" if self.refused else "did not answer"

    def sentence(self, fallback: str) -> str:
        """The remote's own words as a sentence, or `fallback` where it gave none.

        Punctuated here because a remote's message frequently is not a sentence: WHM answers
        `Account suspension is locked` and csf answers bare clauses, and a runner splicing one
        into a longer sentence would run two claims together with a space between them.

        **Whitespace is "gave none".** A message of `"  "` is truthy, so choosing the fallback on
        truthiness and stripping afterwards yields a bare `"."` — a sentence with no claim in it,
        spliced in front of a reading that does carry one. `write_failure_or_none` already blanks
        such a message, and the constructions that do not go through it are hand-built — the
        firewall pair's `backend_write_failure`, the VM interface runner's two task failures, and
        the password runner's — so the guard sits at the one point a spoken sentence passes
        through rather than at each constructor. The first two reach here; the password runner's
        reads `verb` and `code` and never calls this, and the guard covers it for the day it does.
        """
        spoken = (self.message or "").strip() or fallback.strip()
        return spoken if spoken.endswith((".", "!", "?")) else f"{spoken}."


def write_failure_or_none(result: Mapping[str, Any]) -> WriteFailure | None:
    """One integration client's answer as a write failure, or `None` when the write took.

    The envelope is the same in every client (`ok`, `error_code`, `message`), so the `unknown`
    default and the refusal of a blank message live here instead of at each mutation site. Named
    apart from the `write_failure` keyword every postflight takes, so a call site reads as a
    question rather than as the answer it is about to pass on.
    """
    if result.get("ok") is True:
        return None
    message = result.get("message")
    return WriteFailure(
        code=str(result.get("error_code") or ERROR_UNKNOWN),
        message=message if isinstance(message, str) and message.strip() else None,
    )


def confirmed_verification(*, matched: bool, failure: WriteFailure) -> tuple[str, str | None]:
    """The verification state and cause a confirming read earns after a write that failed.

    **A positive reading is conclusive; a negative one after a non-answer is not.** That
    asymmetry is the whole of this function, and it is not symmetric by accident:

    - **the state matches and the write merely went unanswered** — `verified`, and no cause at
      all. NOA sent the write, the remote took the connection, and the state is what was asked
      for; hedging every timeout with "NOA cannot prove it caused this" teaches an operator to
      skip the qualifier, and a hedge nobody reads degrades the surface it sits on. A cause
      here would also be refused at construction, since a verified delta carries none.
    - **the state matches and the remote refused** — `unavailable`, cause the refusal code. Not
      `mismatch`, because the postflight *agrees* with the target; not `verified`, because the
      remote said it did nothing, so something other than this change put the state there and
      claiming it would be false. `unavailable` is the honest one: NOA holds no measurement that
      its **own** change took, and the reading itself is named in the payload instead.
    - **the state does not match and the remote refused** — `mismatch`. The refusal now has a
      reading behind it, which is strictly more than a refusal carried before.
    - **the state does not match and the write went unanswered** — `unavailable`, cause the
      write's code. Never "did not happen": the read may simply be earlier than the change.

    The pair is returned together so a `verified` state and a cause cannot be assembled apart
    and then fail at construction.
    """
    if matched:
        if failure.refused:
            return VERIFICATION_UNAVAILABLE, failure.code
        return VERIFICATION_VERIFIED, None
    if failure.refused:
        return VERIFICATION_MISMATCH, None
    return VERIFICATION_UNAVAILABLE, failure.code


def confirmed_verification_sentence(
    *, opener: str, reading: str, matched: bool, failure: WriteFailure, fallback: str
) -> str:
    """What a failed write plus a confirming read say to an operator, as one sentence.

    Three runners spoke these three sentences in identical words — the account pair, the VM
    interface and the PMG whitelist — because the fact being reported is the same fact and only
    the noun differs. `reading` is that noun already worded by the caller ("`acme` is suspended",
    "`net0` on VM 108 reads as up"), which is the only thing a runner still owns here.

    **Same two inputs as `confirmed_verification`, deliberately.** The sentence and the verdict
    are two functions of one `(matched, failure.refused)` pair rather than two derivations a
    caller could get out of step, so a payload cannot say "a fresh read agrees" over a delta
    stating `unavailable`. The branches line up one for one with that function's:

    - **the write went unanswered** — no hedge about what the remote said, because it said
      nothing; the read may simply be earlier than the change, so the sentence refuses to report
      it as one that did not happen. `unavailable` there.
    - **the remote refused and the reading matches anyway** — the refusal is quoted and the
      reading is named as something this change did not cause. `unavailable` there.
    - **the remote refused and the reading agrees** — the refusal is quoted and the reading is
      what turns it into a measurement. `mismatch` there.

    The `verified` case has no sentence here: it is the one branch where each runner has
    something of its own to say, and all three say it above this call.
    """
    if not failure.refused:
        return (
            f"{opener}, and a fresh read says {reading}. The change may still land, so NOA "
            "cannot report it as one that did not happen."
        )

    spoken = failure.sentence(fallback)
    if matched:
        return (
            f"{opener}. {spoken} A fresh read says {reading}, so something other than this "
            "change left it that way."
        )
    return f"{opener}. {spoken} A fresh read agrees: {reading}."


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
    "NON_ANSWER_ERROR_CODES",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "VERIFICATION_UNAVAILABLE",
    "WriteFailure",
    "confirmed_verification",
    "confirmed_verification_sentence",
    "uuid_or_none",
    "write_failure_or_none",
]
