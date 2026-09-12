"""What both WHM firewall CHANGE tools share.

`change_target.py` holds what *every* post-approval runner shares, across systems. This holds
what the two firewall ones share with each other and with nothing else: the before-state their
cards are built from, the shape a backend's answer takes, the two release commands whose refusal
is an ordinary answer, and the machine-and-address a runner resolves out of the evidence.

**Born with the release-and-allow tool inside `whm_firewall_change.py`, hoisted with the
allowlist-remove tool when the allowlist removal became
the second caller.** The same move `change_target.py` was made by, for the same reason: a second
caller is when shared code stops being one module's internals. It is a leaf on purpose —
it imports the READ layer's vocabulary and nothing from either tool module — because the
alternative, letting the allowlist-remove tool import its machinery from the release-and-allow
tool's module, makes the second tool a
dependent of the first and the aggregate registrar a cycle.

**Its own file rather than `whm_firewall.py`.** The dual-backend firewall read's module is the
READ tool, and half of what is here drives mutations. The file-size cap would have allowed the
merge; the cookie/CSRF boundary is the reason not to take it, and it is the same reason the
release-and-allow tool did not append itself to that file either.

**The evidence keys are shared, and that is a narrower claim than it looks.** They are shared
between the two *firewall* tools, whose evidence genuinely is the same shape — a server, an
address, and one dual-backend reading of that address. They are deliberately not shared with the
account tools, which spell two of them the same way by coincidence: a key that means two things
in two contracts is how a JSONB read silently returns the wrong field (the release-and-allow
tool's own note, kept).
`whm_firewall_release_and_allow` adds `duration_minutes` to this set and owns that one alone.

The AST fan-out guard covers this module through `whm_firewall*.py`, so nothing here can
hand-roll an `asyncio.gather` past the zero-backend error's door without failing
`test_whm_firewall_gate.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import structlog

from core.approvals.delta import VERIFICATION_MISMATCH, BackendOutcome, Bound
from core.errors import NoaError
from core.integrations.whm.availability import (
    BACKEND_CSF,
    BACKEND_IMUNIFY,
    FirewallAvailability,
)
from core.integrations.whm.csf_cli import require_csf_success, run_csf_command
from core.integrations.whm.imunify_cli import parse_imunify_json_output, run_imunify_command
from core.integrations.whm.ssh import resolve_whm_ssh_config
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE
from core.remote_exec.types import SSHConnectionConfig
from noa_api.mcp_tools.change_target import (
    # Re-exported below rather than imported twice: a firewall CHANGE module reaches for all of
    # these through this one module, instead of half through `change_target` and half from here
    # . What `change_target` owns is what every runner shares across systems; this is the
    # firewall pair's own layer on top of it.
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_EVIDENCE_UNUSABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    WriteFailure,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import ERROR_UNKNOWN, ToolPayload, tool_failure
from noa_api.mcp_tools.whm_firewall import (
    VERDICT_ALLOWLISTED,
    VERDICT_BLOCKED,
    BackendLookup,
    combine_firewall_verdict,
    without_noa_comment_text,
)

# The evidence keys the firewall tools write and their runners read back. Constants because they
# cross a boundary in time as well as in code — a tool writes them into `approval_context` JSONB
# and a runner reads them minutes later — and a misspelt key in JSONB reads as an absent one.
EVIDENCE_SERVER_ID = "server_id"
EVIDENCE_SERVER_NAME = "server"
EVIDENCE_TARGET = "target"
EVIDENCE_FIREWALL = "firewall"

# Only ever the fallback text of an exception this module raises and then swallows, when csf
# exits non-zero and prints nothing. It exists so `require_csf_success` can classify the failure
# — which is what the tolerated steps below actually want from it.
MESSAGE_CSF_STEP_FAILED = "CSF did not complete this step."

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class BackendChange:
    """What one backend did when a firewall CHANGE drove it.

    `ok` is about the commands, not about the outcome — whether the entries are actually gone or
    present is the postflight's answer, taken separately and from a fresh read. A backend that
    could not be driven at all keeps the code that names the remedy (`ssh_sudo_required` and
    `csf_command_failed` send an operator to different places).
    """

    ok: bool
    error_code: str | None = None
    message: str | None = None

    def as_payload(self) -> ToolPayload:
        """This backend's entry in a runner's answer."""
        if self.ok:
            return {"ok": True}
        return {"ok": False, "error_code": self.error_code, "message": self.message}


def backend_change_failure(error_code: str, message: str) -> BackendChange:
    """A backend failure with NOA's own comment text cut out of it.

    A backend that refuses a command frequently quotes the command — or the entry — back, and a
    firewall entry NOA created carries the operator's reason behind its marker. This message
    reaches `result_summary`, which `noa_get_action_result` returns to a model, so it is cut on
    the way in rather than trusted to be harmless.

    It applies to a removal as much as to a write, and that is the part worth stating: the
    allowlist-remove tool writes
    no comment of its own, but the entry it deletes was written by the release-and-allow tool
    and still carries one, so
    a refusal that quotes it is the same leak through the same door.
    """
    return BackendChange(ok=False, error_code=error_code, message=without_noa_comment_text(message))


async def tolerated_csf_step(
    config: SSHConnectionConfig, *, args: list[str], target: str, tool: str, event: str
) -> None:
    """One csf command whose non-zero exit is an ordinary answer, not a failure.

    "Not in that list" is how csf reports the common case — an address held by a temporary ban
    has no `csf.deny` line, one that was never allowed has no `csf.allow` line — and treating
    that as a failure would refuse the ordinary release or removal these tools exist for.

    Logged when it happens, so "the entry was already gone" stays distinguishable from "the
    command could not run" after the fact. An SSH-level failure still raises through
    `run_csf_command` — that one is not an answer about the list, it is the absence of one.

    **A sudo-rights refusal is not tolerated either** (two causes, two remedies, `noa-old` GH #82).
    sudoers can permit `csf -v` — which is what the availability probe runs — and refuse `csf -ar`,
    and in that arrangement every step here would report "not in that list" and the change would
    read as having found nothing to do. `ssh_sudo_required` names a remedy; silence names none.

    `event` is the caller's, not this function's: a log event names the tool a reader is looking
    for, and it is the first thing they filter on. `tool` rides beside it as a field.
    """
    result = await run_csf_command(config, args=args)
    if result.exit_code == 0:
        return
    # Raises `ssh_sudo_required` for a rights refusal and `csf_command_failed` otherwise; only
    # the first is re-raised, because only the first is never an answer about the list.
    try:
        require_csf_success(result, default_message=MESSAGE_CSF_STEP_FAILED)
    except NoaError as exc:
        if exc.error_code == SSH_SUDO_REQUIRED_CODE:
            raise
    logger.info(
        event,
        tool=tool,
        backend="csf",
        step=args[0],
        target=target,
        exit_code=result.exit_code,
    )


async def tolerated_imunify_step(
    config: SSHConnectionConfig, *, args: list[str], target: str, tool: str, event: str, step: str
) -> None:
    """One Imunify command whose own refusal is an ordinary answer (see above).

    Imunify refuses a delete for an entry it does not hold, which is the common shape of both
    firewall changes: most addresses are held by one backend rather than by every list.

    A sudo-rights refusal is re-raised for the reason it is one backend over: sudoers can permit
    the probe and refuse the write, and tolerating that turns a change that could not run into a
    change that found nothing to do.
    """
    result = await run_imunify_command(config, args=args)
    try:
        parse_imunify_json_output(result)
    except NoaError as exc:
        if exc.error_code == SSH_SUDO_REQUIRED_CODE:
            raise
        logger.info(
            event,
            tool=tool,
            backend="imunify",
            step=step,
            target=target,
            error_code=exc.error_code,
        )


def firewall_state(
    lookups: Mapping[str, BackendLookup], *, availability: FirewallAvailability
) -> ToolPayload:
    """One dual-backend read, shaped as the before-state a card and a receipt show.

    The same fields the dual-backend firewall read's tool answers with, and deliberately so: an
    operator authorising a
    firewall change is looking at the reading they would have got from the preflight, which is
    what makes the card honest about what it is approving (DECISIONS section 6.5).

    **Uncut**, unlike the dual-backend firewall read's own `matches`. These lines go onto
    `action_requests.approval_context`
    and from there to the approval card and the receipt, both of which are the operator's own
    surfaces behind their cookie — and `noa_get_action_result` reaches neither: `ActionResultView`
    has no evidence field, and the only thing `core.approvals.results` takes off `action_receipts`
    is the delta's two verification scalars, lifted out of the JSONB **in SQL**
    (`core.approvals.reads.select_requester_matched_with_change_verification`), so no receipt row
    enters that process at all. The
    no-path-back rule withholds from the surface that answers a *model*; withholding
    here would take the reason off the two places that exist to show it.
    """
    matches = [line for lookup in lookups.values() for line in lookup.matches]
    total_matches = sum(lookup.total_matches for lookup in lookups.values())
    state: ToolPayload = {
        "available_backends": availability.as_tools_dict(),
        # Present-but-denied ≠ absent: the operator is told to fix sudoers, not to install csf.
        "sudo_required": availability.sudo_required,
        "combined_verdict": combine_firewall_verdict(list(lookups.values())),
        # Folding a non-answer into the benign value is refused: a verdict read from a subset
        # says so, on the card as much as in a tool result.
        "unanswered_backends": unanswered_backends(lookups),
        "matches": matches,
        # The cap's own bound: the cut is csf's (`max_matches`), so the bound travels with the rows.
        "total_matches": total_matches,
        "truncated": total_matches > len(matches),
    }
    for name, lookup in lookups.items():
        state[name] = lookup.as_payload()
    return state


def unanswered_backends(lookups: Mapping[str, BackendLookup]) -> list[str]:
    """The usable backends that produced no verdict a decision can rest on.

    One spelling of the predicate, because it is asserted on twice per change — once on the
    before-state the operator reads and once on the after-state the runner answers with — and two
    copies is how the second one stops matching the first.
    """
    return [name for name, lookup in lookups.items() if not lookup.answered]


def holds_allow_entry(lookups: Mapping[str, BackendLookup]) -> bool:
    """Does any backend that answered still hold an allow entry for this address?

    Read from `allow_entry` rather than from the combined verdict, because the verdict resolves
    block-over-allow and therefore loses exactly this fact for an address that is on both lists
    (`core.integrations.whm.csf`). Backends that did not answer are skipped rather than counted
    as clean, which is why every caller has to check `unanswered_backends` first: silence is not
    evidence of absence, and here the absence is the whole claim.
    """
    return any(lookup.allow_entry for lookup in lookups.values() if lookup.answered)


def backend_outcomes(
    changes: Mapping[str, BackendChange], lookups: Mapping[str, BackendLookup]
) -> tuple[BackendOutcome, ...]:
    """Each backend's row in the delta both firewall runners publish.

    Two independent facts joined by name, and they are joined here rather than twice because the
    join is where they could disagree: `changes` says whether the backend could be *driven*
    and with what refusal, `lookups` says whether it *answered* the confirming read and with what
    verdict. Keeping them apart is what makes "the command failed" and "the check said nothing"
    two rows a reader can act on differently — the first names a remedy, the second names a
    server to go and look at.

    The union of both maps, so a backend that was driven and then went silent, and one that was
    never driven but still answered, both appear. Sorted by name, so a receipt written twice for
    one address reads the same both times.
    """
    names = sorted(set(changes) | set(lookups))
    outcomes: list[BackendOutcome] = []
    for name in names:
        change = changes.get(name)
        lookup = lookups.get(name)
        outcomes.append(
            BackendOutcome(
                name=name,
                # Absent from `changes` means `run_on_usable_backends` never reached it, which
                # is not the same as reaching it and being refused — but from the delta's side
                # both are "this backend did not run the change", and the refusal that *was*
                # measured carries its own code below.
                driven=change is not None and change.ok,
                answered=lookup is not None and lookup.answered,
                verdict=None if lookup is None else lookup.verdict,
                error_code=None if change is None else change.error_code,
            )
        )
    return tuple(outcomes)


def evidence_bound(evidence: Mapping[str, object]) -> Bound | None:
    """The bound of the before-state reading this change was decided against.

    `firewall_state` writes the lines it read plus `total_matches` and `truncated`, because the
    cut is csf's own (`max_matches`) and a bound travels with the rows it bounds. A delta stating
    "this address was blocked and is now allowed" rests on that reading, so the bound rides into
    the delta as well — without it the sentence reads as a statement about every line the
    firewall holds for the address, when the evidence behind it stopped at twenty.

    `None` when the evidence carries no usable pair, which is a row opened before those keys
    existed. Absent rather than a zero: a bound nobody recorded is not a bound of nothing.
    """
    firewall = evidence.get(EVIDENCE_FIREWALL)
    if not isinstance(firewall, Mapping):
        return None
    total = firewall.get("total_matches")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    return Bound(total=total, truncated=firewall.get("truncated") is True)


def evidence_verdict(evidence: Mapping[str, object]) -> str | None:
    """The combined verdict the operator saw, as the `old` side of a delta's field change.

    Off the evidence rather than re-derived: it is the reading the decision rests on, and a
    second computation here could disagree with the one that was authorised.

    `None` when the evidence has no usable verdict, and a caller treats that as "no field change
    can be stated" rather than substituting a benign word — a delta whose `old` side was invented
    is exactly the fabrication folding a non-answer into the benign value refuses one surface over.
    """
    firewall = evidence.get(EVIDENCE_FIREWALL)
    if not isinstance(firewall, Mapping):
        return None
    verdict = firewall.get("combined_verdict")
    return verdict if isinstance(verdict, str) and verdict else None


@dataclass(frozen=True)
class FirewallChangeTarget:
    """The machine and address an approved firewall change runs against, from the evidence."""

    config: SSHConnectionConfig
    server_name: str
    target: str


async def resolve_firewall_change_target(
    evidence: Mapping[str, object], *, context: McpToolContext
) -> FirewallChangeTarget | ToolPayload:
    """The connection and address an approved firewall change runs against.

    **From the evidence, never from the arguments.** `server_ref` and `target` are strings a
    model supplied, inventory can be edited between a request and its approval, and the evidence
    is the state the operator actually saw on the card. Re-resolving here would be a second
    resolution that can disagree with the one the decision rests on.

    Two refusals, both before anything is changed and each naming what an administrator should
    do: the address did not survive its JSONB round trip as a usable value, or the server row is
    gone. A caller with a further evidence field of its own checks it before calling this and
    answers `change_evidence_unusable` the same way (the release-and-allow tool's
    `duration_minutes`).

    The database session closes before the SSH hops, the account search's rule, and here it matters
    twice over: the executor's own session is open for the whole of the call.
    """
    target = evidence.get(EVIDENCE_TARGET)
    if not isinstance(target, str) or not target.strip():
        return tool_failure(ERROR_EVIDENCE_UNUSABLE, MESSAGE_EVIDENCE_UNUSABLE)

    server_id = uuid_or_none(evidence.get(EVIDENCE_SERVER_ID))
    if server_id is None:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        server = await repository.get_by_id(server_id)
        if server is None:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
        server_name = server.name
        config = resolve_whm_ssh_config(
            server, cipher=context.secret_cipher, require_host_key_fingerprint=True
        )

    return FirewallChangeTarget(config=config, server_name=server_name, target=target.strip())


# The name each backend goes by on an operator's card, against the key it goes by in code. The
# operator-facing surfaces name a source that could not answer rather than counting it, and
# `imunify` is a package name while `Imunify` is what the product calls itself — a sentence
# reading "imunify did not answer" looks like a typo for a word the reader half-recognises.
#
# A hand-kept map is a claim only where something reads it against the code, so
# `test_whm_firewall_release_runner.py` asserts it covers exactly the backends
# `FirewallAvailability` can name. An unmapped name still renders, as itself: an operator who
# sees a raw key can go and look it up, and a backend dropped from the sentence names nothing.
BACKEND_DISPLAY_NAMES: Final[Mapping[str, str]] = {
    BACKEND_CSF: "CSF",
    BACKEND_IMUNIFY: "Imunify",
}


def name_sources(names: Sequence[str]) -> str:
    """`CSF and Imunify` — the sources, named, for a sentence an operator reads.

    Named and never counted: "one source did not answer" tells an operator that something is
    wrong and not which server to go and look at. That is the same rule `delta.unanswered`
    carries as a list rather than a number, applied to the sentence beside it.
    """
    return " and ".join(BACKEND_DISPLAY_NAMES.get(name, name) for name in names)


def firewall_verdict_sentence(*, target: str, server: str, verdict: str) -> str:
    """One combined verdict, as the sentence an operator reads.

    `blocked` and `allowlisted` stay in the firewall's own words — they name states a reader can
    act on, and translating them would put a second vocabulary between the operator and the box
    they are about to look at. `not_found` is the exception and it is not a softening: the token
    names nothing to a reader, so it renders as what this repo documents it to mean, which is
    that neither list holds an entry for the address at all.
    """
    if verdict == VERDICT_BLOCKED:
        return f"{target} is still blocked on {server}."
    if verdict == VERDICT_ALLOWLISTED:
        return f"{target} is allowed on {server}."
    return f"{server} has no deny entry and no allow entry for {target}."


# What a backend failure says when it carried no sentence of its own. Two of them, because the
# blank case still has to answer the one question this whole path turns on: a backend that
# refused said what it did, and a backend that never answered said nothing at all. Shared so the
# two tools do not answer one blank message two ways.
MESSAGE_BACKEND_REFUSED = "The firewall command did not run."
MESSAGE_BACKEND_UNANSWERED = "The firewall command did not answer."


def backend_refusal_sentence(*, name: str, server: str, refused: bool) -> str:
    """Which source could not be driven, and which of the two ways it could not.

    The distinction is the whole value of the line: a backend that **refused** answered, and what
    it answered is that it did not act — which makes a disagreeing read afterwards conclusive. A
    backend that never answered said nothing, and nothing is known. `backend_failure_sentence`
    keeps the same split for the backend's own words; this is that split in words an operator
    reads, with the source named rather than left to the error code that no longer renders here.
    """
    if refused:
        return f"{BACKEND_DISPLAY_NAMES.get(name, name)} refused the command on {server}."
    return (
        f"{BACKEND_DISPLAY_NAMES.get(name, name)} did not answer when NOA ran the command "
        f"on {server}."
    )


def backend_failure_sentence(failure: WriteFailure) -> str:
    """A backend failure as a sentence, with the right fallback behind a blank message.

    **A fixed "did not run" is a claim, and on half this branch it is the one claim this path
    refuses to make.** A backend whose message came back blank with `ssh_timeout` never told NOA
    what it did, so an envelope saying the command did not run would contradict the delta beside
    it, which says `unavailable` precisely because nothing is known. The four single-target
    runners word their openers off `WriteFailure.verb` for the same reason; this is that one
    fact, in the two words the firewall pair's sentence is built from.
    """
    return failure.sentence(
        MESSAGE_BACKEND_REFUSED if failure.refused else MESSAGE_BACKEND_UNANSWERED
    )


def backend_write_failure(broken: BackendChange) -> WriteFailure:
    """One backend's refusal as the failure a confirming read is reported beside.

    The code is already the remedy-naming one and the message is already cut of NOA's own
    comment text (`backend_change_failure`), so nothing is re-derived here — what this adds is
    the one question both tools ask of it: did the backend answer that it did not act, or did
    it never answer at all? Only the first makes a disagreeing read conclusive.
    """
    return WriteFailure(code=broken.error_code or ERROR_UNKNOWN, message=broken.message)


def refused_backend_verdict(*, failure: WriteFailure, contradicted: bool) -> tuple[str, str | None]:
    """The verification state and cause a backend's refusal earns once the read is consulted.

    **The read is consulted on this branch now, and it used not to be.** Both tools take their
    confirming read before deciding anything, so by the time a refusal is reported the reading
    already exists — and a refusal reported with nothing behind it is strictly less than the
    same refusal with a measurement beside it. An operator told only that csf refused cannot
    tell a release that did not happen from one that happened anyway. The backend's own row
    still names the remedy, which is what the earlier decision was protecting, and nothing is
    taken off it.

    **`verified` is not reachable here, and that is these two tools being vectors rather than
    scalars.** The change asked for every usable backend; one of them named a refusal; and a
    combined verdict cannot say "and the other one did it too". So a positive reading does not
    make the change whole the way it does on a tool with a single target, and the honest state
    stays `unavailable` — NOA holds no measurement that its **own** change took.

    What the reading does earn is the line between:

    - `mismatch` — every usable backend answered, the address is not where the change asked for
      it, and the backend told NOA it did not act. All three, which is what makes it a
      measurement rather than an absence.
    - `unavailable` — every other case, including a command that never answered at all, because
      that command may still land and a read taken now cannot say it did not.

    `contradicted` is each tool's own question, because the fact differs: the release asks
    whether the combined verdict is `allowlisted`, and the removal asks whether any backend that
    answered still holds an allow entry — a combined verdict would lose the second, since both
    backends resolve a conflict block-first.
    """
    if failure.refused and contradicted:
        # A contradicted reading needs no cause: the backend's code is on the envelope and on
        # its own row, and a cause answers why there is no measurement.
        return VERIFICATION_MISMATCH, None
    return VERIFICATION_UNAVAILABLE, failure.code


def confirming_read_sentence(*, target: str, answer: str | None, unanswered: Sequence[str]) -> str:
    """What the confirming read said, as a sentence to put beside a backend's refusal.

    `answer` is `None` where the read itself could not answer, and that branch is the shared
    part: the silent backends are **named** rather than counted, because a source that cannot
    answer gets named beside the verdict and "one backend was silent" does not say which server
    to go and look at.

    Each tool supplies its own `answer`, because the fact each one confirms is different — a
    release asks what the combined verdict is, and a removal asks whether any backend that
    answered still holds an allow entry, which the combined verdict cannot say.
    """
    if answer is None:
        return (
            f"NOA could not confirm what the firewall holds for `{target}` either: "
            f"{' and '.join(unanswered)} did not answer the confirming read."
        )
    return f"A fresh read of the firewall says {answer}."


__all__ = [
    "BACKEND_DISPLAY_NAMES",
    "ERROR_EVIDENCE_UNUSABLE",
    "ERROR_SERVER_UNAVAILABLE",
    "EVIDENCE_FIREWALL",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_TARGET",
    "MESSAGE_BACKEND_REFUSED",
    "MESSAGE_BACKEND_UNANSWERED",
    "MESSAGE_EVIDENCE_UNUSABLE",
    "MESSAGE_SERVER_UNAVAILABLE",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "VERIFICATION_UNAVAILABLE",
    "BackendChange",
    "FirewallChangeTarget",
    "WriteFailure",
    "backend_change_failure",
    "backend_failure_sentence",
    "backend_outcomes",
    "backend_refusal_sentence",
    "backend_write_failure",
    "confirming_read_sentence",
    "evidence_bound",
    "evidence_verdict",
    "firewall_state",
    "firewall_verdict_sentence",
    "holds_allow_entry",
    "name_sources",
    "refused_backend_verdict",
    "resolve_firewall_change_target",
    "tolerated_csf_step",
    "tolerated_imunify_step",
    "unanswered_backends",
]
