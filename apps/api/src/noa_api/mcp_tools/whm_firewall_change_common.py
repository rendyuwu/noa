"""What both WHM firewall CHANGE tools share (T25, T26 — V22, V33, V66).

`change_target.py` holds what *every* post-approval runner shares, across systems. This holds
what the two firewall ones share with each other and with nothing else: the before-state their
cards are built from, the shape a backend's answer takes, the two release commands whose refusal
is an ordinary answer, and the machine-and-address a runner resolves out of the evidence.

**Born at T25 inside `whm_firewall_change.py`, hoisted at T26 when the allowlist removal became
the second caller.** The same move `change_target.py` was made by, for the same reason: a second
caller is when shared code stops being one module's internals (V66). It is a leaf on purpose —
it imports the READ layer's vocabulary and nothing from either tool module — because the
alternative, letting T26 import its machinery from T25's module, makes the second tool a
dependent of the first and the aggregate registrar a cycle.

**Its own file rather than `whm_firewall.py`.** T24's module is the READ tool, and half of what
is here drives mutations. C14 would have allowed the merge; V22's boundary is the reason not to
take it, and it is the same reason T25 did not append itself to that file either
(`docs/AS-BUILT.md` §T25(a)).

**The evidence keys are shared, and that is a narrower claim than it looks.** They are shared
between the two *firewall* tools, whose evidence genuinely is the same shape — a server, an
address, and one dual-backend reading of that address. They are deliberately not shared with the
account tools, which spell two of them the same way by coincidence: a key that means two things
in two contracts is how a JSONB read silently returns the wrong field (T25's own note, kept).
`whm_firewall_release_and_allow` adds `duration_minutes` to this set and owns that one alone.

The AST fan-out guard covers this module through `whm_firewall*.py`, so nothing here can
hand-roll an `asyncio.gather` past V57's door without failing `test_whm_firewall_gate.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from core.approvals.delta import BackendOutcome, Bound
from core.errors import NoaError
from core.integrations.whm.availability import FirewallAvailability
from core.integrations.whm.csf_cli import require_csf_success, run_csf_command
from core.integrations.whm.imunify_cli import parse_imunify_json_output, run_imunify_command
from core.integrations.whm.ssh import resolve_whm_ssh_config
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE
from core.remote_exec.types import SSHConnectionConfig
from noa_api.mcp_tools.change_target import (
    # Re-exported below rather than imported twice: a firewall CHANGE module reaches for all of
    # these through this one module, instead of half through `change_target` and half from here
    # (V66). What `change_target` owns is what every runner shares across systems; this is the
    # firewall pair's own layer on top of it.
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_SERVER_UNAVAILABLE,
    MESSAGE_EVIDENCE_UNUSABLE,
    MESSAGE_SERVER_UNAVAILABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import ToolPayload, tool_failure
from noa_api.mcp_tools.whm_firewall import (
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
    """A backend failure with NOA's own comment text cut out of it (V96).

    A backend that refuses a command frequently quotes the command — or the entry — back, and a
    firewall entry NOA created carries the operator's reason behind its marker. This message
    reaches `result_summary`, which `noa_get_action_result` returns to a model, so it is cut on
    the way in rather than trusted to be harmless.

    It applies to a removal as much as to a write, and that is the part worth stating: T26 writes
    no comment of its own, but the entry it deletes was written by T25 and still carries one, so
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

    **A sudo-rights refusal is not tolerated either** (V55, `noa-old` GH #82). sudoers can permit
    `csf -v` — which is what the availability probe runs — and refuse `csf -ar`, and in that
    arrangement every step here would report "not in that list" and the change would read as
    having found nothing to do. `ssh_sudo_required` names a remedy; silence names none.

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
    change that found nothing to do (V55).
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

    The same fields T24's tool answers with, and deliberately so: an operator authorising a
    firewall change is looking at the reading they would have got from the preflight, which is
    what makes the card honest about what it is approving (DECISIONS §6.5).

    **Uncut**, unlike T24's own `matches`. These lines go onto `action_requests.approval_context`
    and from there to the approval card and the receipt, both of which are the operator's own
    surfaces behind their cookie (V27) — and `noa_get_action_result` reaches neither
    (`ActionResultView` has no evidence field, and `core.approvals.results` never joins
    `action_receipts`, V76). V96 withholds from the surface that answers a *model*; withholding
    here would take the reason off the two places that exist to show it.
    """
    matches = [line for lookup in lookups.values() for line in lookup.matches]
    total_matches = sum(lookup.total_matches for lookup in lookups.values())
    state: ToolPayload = {
        "available_backends": availability.as_tools_dict(),
        # Present-but-denied ≠ absent: the operator is told to fix sudoers, not to install csf.
        "sudo_required": availability.sudo_required,
        "combined_verdict": combine_firewall_verdict(list(lookups.values())),
        # V86: a verdict read from a subset says so, on the card as much as in a tool result.
        "unanswered_backends": unanswered_backends(lookups),
        "matches": matches,
        # V85: the cut is csf's (`max_matches`), so the bound travels with the rows.
        "total_matches": total_matches,
        "truncated": total_matches > len(matches),
    }
    for name, lookup in lookups.items():
        state[name] = lookup.as_payload()
    return state


def unanswered_backends(lookups: Mapping[str, BackendLookup]) -> list[str]:
    """The usable backends that produced no verdict a decision can rest on (V86).

    One spelling of the predicate, because it is asserted on twice per change — once on the
    before-state the operator reads and once on the after-state the runner answers with — and two
    copies is how the second one stops matching the first.
    """
    return [name for name, lookup in lookups.items() if not lookup.answered]


def holds_allow_entry(lookups: Mapping[str, BackendLookup]) -> bool:
    """Does any backend that answered still hold an allow entry for this address? (T26)

    Read from `allow_entry` rather than from the combined verdict, because the verdict resolves
    block-over-allow and therefore loses exactly this fact for an address that is on both lists
    (`core.integrations.whm.csf`). Backends that did not answer are skipped rather than counted
    as clean, which is why every caller has to check `unanswered_backends` first: silence is not
    evidence of absence (V86), and here the absence is the whole claim.
    """
    return any(lookup.allow_entry for lookup in lookups.values() if lookup.answered)


def backend_outcomes(
    changes: Mapping[str, BackendChange], lookups: Mapping[str, BackendLookup]
) -> tuple[BackendOutcome, ...]:
    """Each backend's row in the delta both firewall runners publish (V86).

    Two independent facts joined by name, and they are joined here rather than twice because the
    join is where they could disagree (V66): `changes` says whether the backend could be *driven*
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
    """The bound of the before-state reading this change was decided against (V85).

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
    second computation here could disagree with the one that was authorised (V33).

    `None` when the evidence has no usable verdict, and a caller treats that as "no field change
    can be stated" rather than substituting a benign word — a delta whose `old` side was invented
    is exactly the fabrication V86 refuses one surface over.
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
    """The connection and address an approved firewall change runs against (V33).

    **From the evidence, never from the arguments.** `server_ref` and `target` are strings a
    model supplied, inventory can be edited between a request and its approval, and the evidence
    is the state the operator actually saw on the card. Re-resolving here would be a second
    resolution that can disagree with the one the decision rests on.

    Two refusals, both before anything is changed and each naming what an administrator should
    do: the address did not survive its JSONB round trip as a usable value, or the server row is
    gone. A caller with a further evidence field of its own checks it before calling this and
    answers `change_evidence_unusable` the same way (T25's `duration_minutes`).

    The database session closes before the SSH hops, T21's rule, and here it matters twice over:
    the executor's own session is open for the whole of the call.
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


__all__ = [
    "ERROR_EVIDENCE_UNUSABLE",
    "ERROR_SERVER_UNAVAILABLE",
    "EVIDENCE_FIREWALL",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_TARGET",
    "MESSAGE_EVIDENCE_UNUSABLE",
    "MESSAGE_SERVER_UNAVAILABLE",
    "STATUS_CHANGED",
    "STATUS_NO_OP",
    "VERIFICATION_UNAVAILABLE",
    "BackendChange",
    "FirewallChangeTarget",
    "backend_change_failure",
    "backend_outcomes",
    "evidence_bound",
    "evidence_verdict",
    "firewall_state",
    "holds_allow_entry",
    "resolve_firewall_change_target",
    "tolerated_csf_step",
    "tolerated_imunify_step",
    "unanswered_backends",
]
