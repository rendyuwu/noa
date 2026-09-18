"""What the WHM firewall release runner answers, and the vocabulary it answers in.

`whm_firewall_change.py` holds the two halves of `whm_firewall_release_and_allow` that talk to
the outside world: the tool an LLM can reach, which changes nothing, and the runner closure that
drives CSF and Imunify once an operator approved. This holds the step after those commands
return — reading a fresh dual-backend lookup into one of five answers, and stating the
before→after each of them carries.

**Split out for the file-size cap, and behaviour-neutral.** `whm_firewall_change.py` sat three
lines under the 900-line cap `apps/api/tests/test_config.py` enforces, which left no room to add
a word to any of the five branches. Same functions, same call order, same strings; the names went
public on the way across and nothing else moved.

**The tool's name lives here rather than in the tool module, and that is the split's one
surprise.** `release_outcome` tags its unverified-change warning with the tool, and
`whm_firewall_change.py` imports this module for the two functions below — so reading the name
back out of that module would be a cycle. It is re-exported there, so every caller that already
named it there is untouched.

A leaf, like `whm_firewall_change_common.py` beside it: it reaches the READ layer's verdict
vocabulary and the firewall pair's shared shapes, and nothing from either tool module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

import structlog

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
    FieldChange,
)
from core.approvals.execution import ChangeExecutionRequest
from core.remote_exec.types import SSHConnectionConfig
from noa_api.mcp_tools.results import ToolPayload, tool_failure, tool_ok
from noa_api.mcp_tools.whm_firewall import (
    VERDICT_ALLOWLISTED,
    VERDICT_BLOCKED,
    BackendLookup,
    combine_firewall_verdict,
)
from noa_api.mcp_tools.whm_firewall_change_common import (
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    BackendChange,
    backend_outcomes,
    backend_refusal_sentence,
    backend_write_failure,
    evidence_bound,
    evidence_verdict,
    firewall_verdict_sentence,
    name_sources,
    refused_backend_verdict,
    unanswered_backends,
)

TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW = "whm_firewall_release_and_allow"

# The postflight says the address is still blocked: the deny entry outlived the release.
ERROR_RELEASE_FAILED = "firewall_release_failed"
# It is no longer blocked and it is not allowed either: the release took and the allow did not.
ERROR_ALLOW_FAILED = "firewall_allow_failed"

_JAKARTA: Final = ZoneInfo("Asia/Jakarta")


def jakarta_stamp(at: datetime) -> str:
    """`12 Sep 2026, 8:26 PM (WIB)` — one instant, in one zone, with the zone on the value.

    **The zone is appended unconditionally and there is no parameter that can suppress it.** The
    approval card has no zone-naming heading the way the copied block does, so the zone rides on
    the value or it is not stated at all — and a bare wall-clock time that reaches a ticket is
    read by whoever opens it in whatever zone they sit in. Written this way the stamp has no bare
    form to escape in, which is the whole of what the rule protects.

    Jakarta and never the runner's own clock, for the same reason the embed pins one zone: two
    people reading one approval have to be reading one clock.

    This is the only operator-facing stamp any runner composes, and it composes it here rather
    than leaving the instant for a renderer to format, because the sentence around it is this
    family's own vocabulary and the branch that drops the sentence altogether is a Python branch.
    """
    local = at.astimezone(_JAKARTA)
    return f"{local:%-d %b %Y, %-I:%M %p} (WIB)"


# The change ran and the confirming read could not answer. Warning, because an operator may want
# to look at the box.
LOG_RELEASE_UNVERIFIED: Final = "whm_firewall_release_and_allow_unverified"

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ReleaseTarget:
    """The machine, address and window an approved release runs against, from the evidence.

    `FirewallChangeTarget` plus the window, which is this tool's alone: the removal one module
    over resolves the same machine and address and has no duration to carry.
    """

    config: SSHConnectionConfig
    server_name: str
    target: str
    duration_minutes: int


def release_delta(
    target: ReleaseTarget,
    *,
    request: ChangeExecutionRequest,
    expires_at: datetime,
    changes: Mapping[str, BackendChange],
    lookups: Mapping[str, BackendLookup],
    verification: str,
    verification_cause: str | None,
    measured_verdict: str | None,
) -> ChangeDelta:
    """This change's before→after, as the runner that ran it states it.

    One builder for all five branches, so the facets a branch fills are the only thing that
    differs and a branch cannot quietly grow a sixth shape. What each argument decides:

    - **a comparison needs both sides, so a missing side means no comparison happened.**
      `measured_verdict` is `None` where no postflight verdict was consulted — a backend that
      could not be driven, or one that stayed silent. `old_verdict` is `None` where the evidence
      carries no usable one. Either way `changed_fields` is **absent**, not empty: absent says
      NOA did not compare, and an empty tuple would say NOA compared and the reading did not
      move. Substituting the second for the first is the benign value in place of "unknown",
      which is folding a non-answer into the benign value.
    - both sides present and equal yields an empty `changed_fields`: the reading did not move,
      measured. That is the honest answer for a release of an address that was already allowed,
      where the window moved and the verdict did not — `new_values` is what carries that.
    - a missing `old_verdict` is unreachable from any row the gate wrote, and the branch is
      defensive rather than live: `firewall_state` always writes the key, and
      `combine_firewall_verdict` answers one of four non-empty words — `unknown` included, which
      is a real comparable value and renders as a genuine transition when the postflight then
      answers. What reaches here without one is an `approval_context` NOA did not write, because
      the resolution above validates the target and the server id out of this half and nothing
      else. Defensive or not, it has to say `None`: this is the branch the whole
      absent-versus-empty distinction was invented on, and a mis-stated one here would be a card
      reading "measured, nothing changed" about a before-state that was never readable.
    - `new_values` rides where the allow command took on every driven backend **and** the
      postflight did not refute the entry, which together are what the resolved expiry is a fact
      *about*: the window the backends were asked for, accepted, and did not then deny holding.
      A backend that could not be driven never received it, and a `not_found` postflight is
      every backend saying it holds no entry for this address at all — on either branch the
      expiry is a window nothing wrote, and a resolved timestamp for an entry no command created
      is the worst kind of precise. `blocked` is not that refutation: CSF resolves a
      conflict block-first, so an allow written under a surviving deny entry exists and is
      overridden, and whether an existing entry takes effect is the verdict's job rather than
      this facet's.

    `backends` and `unanswered` are present on every branch, including the failures: which
    backend refused and which one answered are facts about this run regardless of what the
    envelope beside it says.
    """
    old_verdict = evidence_verdict(request.evidence)
    changed: tuple[FieldChange, ...] | None = None
    if measured_verdict is not None and old_verdict is not None:
        changed = (
            ()
            if old_verdict == measured_verdict
            else (FieldChange(field="firewall_verdict", old=old_verdict, new=measured_verdict),)
        )

    # Two conditions rather than one, because the commands and the postflight can disagree: every
    # backend accepted the allow *and* no backend then answered that it holds nothing for this
    # address. The verdicts listed are the ones under which an allow entry can exist — `None` is
    # the branch where none was consulted, and `blocked` is an entry that exists and is overridden.
    allow_took = all(change.ok for change in changes.values()) and measured_verdict in (
        None,
        VERDICT_ALLOWLISTED,
        VERDICT_BLOCKED,
    )
    return ChangeDelta(
        identity={"server": target.server_name, "target": target.target},
        verification=verification,
        verification_cause=verification_cause,
        changed_fields=changed,
        backends=backend_outcomes(changes, lookups),
        unanswered=tuple(unanswered_backends(lookups)),
        new_values=(
            {
                "expires_at": expires_at.isoformat(),
                "duration_minutes": target.duration_minutes,
            }
            if allow_took
            else None
        ),
        bound=evidence_bound(request.evidence),
    )


def release_outcome(
    target: ReleaseTarget,
    *,
    request: ChangeExecutionRequest,
    expires_at: datetime,
    changes: Mapping[str, BackendChange],
    lookups: Mapping[str, BackendLookup],
) -> ChangeOutcome:
    """What the change did, read off a fresh dual-backend read (DECISIONS section 6.5, the
    password-reset verdict-on-verify rule, folding a non-answer into the benign value).

    Five answers, in the order they are decided, and the order is the argument:

    1. **a backend could not be driven** — its own code, because that is the remedy. Reported
       first because it is a fact about the commands rather than an inference from the state.
    2. **a usable backend did not answer the confirming read** — the change happened and is
       *unverified*, with the silent backend named. Reporting it as done would be the
       fabrication the dual-backend firewall read was written to stop, one side worse; reporting
       it as failed would send an
       operator to repeat a release that may already have taken (the password-reset
       verdict-on-verify rule).
    3. **still blocked** — the deny entry outlived the release, so the release failed.
    4. **neither blocked nor allowed** — the release took and the allow did not.
    5. **allowed** — both halves took, and both say so separately.

    `released` and `allowlisted` appear only where the postflight answered. Claiming either from
    a read that did not happen is exactly what the shape exists to prevent, and an absent field
    is more honest than a `false` nobody measured.

    Every one of the five carries a delta beside its envelope, and the two `false`s on answers 3
    and 4 are exactly why: they were *earned* by a postflight that answered, so the delta states
    a measured verdict rather than an absence. `release_delta` holds which facets each branch
    fills.
    """
    common: ToolPayload = {
        "server": target.server_name,
        "target": target.target,
        "duration_minutes": target.duration_minutes,
        # Duration required, never a server default: the after-state shows the resolved expiry, not
        # the window that was asked for.
        "expires_at": expires_at.isoformat(),
        "backends": {name: change.as_payload() for name, change in changes.items()},
    }
    unanswered = unanswered_backends(lookups)

    def delta(
        *,
        verification: str,
        verification_cause: str | None = None,
        measured_verdict: str | None = None,
    ) -> ChangeDelta:
        """`release_delta` with this branch's five shared arguments already bound."""
        return release_delta(
            target,
            request=request,
            expires_at=expires_at,
            changes=changes,
            lookups=lookups,
            verification=verification,
            verification_cause=verification_cause,
            measured_verdict=measured_verdict,
        )

    broken_name = next((name for name in sorted(changes) if not changes[name].ok), None)
    if broken_name is not None:
        # The verdict is consulted here now, and it used not to be — see
        # `refused_backend_verdict`, which holds why, and why `verified` stays unreachable on a
        # tool whose answer is per backend. The reading was already taken above.
        failure = backend_write_failure(changes[broken_name])
        measured = None if unanswered else combine_firewall_verdict(list(lookups.values()))
        verification, cause = refused_backend_verdict(
            failure=failure,
            contradicted=measured is not None and measured != VERDICT_ALLOWLISTED,
        )
        # Where the reading itself could not answer, the silent sources are **named** in place of
        # the second sentence — never a guess at what the firewall now holds.
        reading = (
            "NOA checked afterwards: "
            + firewall_verdict_sentence(
                target=target.target, server=target.server_name, verdict=measured
            )
            if measured is not None
            else (
                f"{name_sources(unanswered)} did not answer when NOA checked afterwards, so NOA "
                f"cannot say what the firewall holds for {target.target}."
            )
        )
        return ChangeOutcome(
            payload={
                **tool_failure(
                    failure.code,
                    backend_refusal_sentence(
                        name=broken_name,
                        server=target.server_name,
                        refused=failure.refused,
                    )
                    + f" {reading}",
                ),
                **common,
                "headline": f"Unblock failed — {target.target}",
                "unanswered_backends": unanswered,
            },
            delta=delta(
                verification=verification,
                verification_cause=cause,
                measured_verdict=measured,
            ),
        )

    if unanswered:
        logger.warning(
            LOG_RELEASE_UNVERIFIED,
            tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
            action_request_id=str(request.action_request_id),
            target=target.target,
            unanswered_backends=unanswered,
        )
        return ChangeOutcome(
            payload=tool_ok(
                **common,
                headline=f"IP unblocked — {target.target}",
                status=STATUS_CHANGED,
                verified=False,
                verification=VERIFICATION_UNAVAILABLE,
                unanswered_backends=unanswered,
                # **No expiry stamp on this branch.** The commands were accepted, so the window
                # is on the delta and in `/admin`; a card whose corner says NOA could not confirm
                # the change must not print a precise time for a window it cannot confirm is in
                # force. Owner-decided.
                message=(
                    f"The unblock ran on {target.server_name}. {name_sources(unanswered)} did "
                    "not answer when NOA checked afterwards, so NOA cannot say it is fully "
                    "unblocked."
                ),
            ),
            # The silent backends are named on `unanswered` rather than counted, and no cause is
            # attached: "which source said nothing" *is* the cause, and a code beside it would be
            # a second answer to the same question.
            delta=delta(verification=VERIFICATION_UNAVAILABLE),
        )

    verdict = combine_firewall_verdict(list(lookups.values()))
    if verdict == VERDICT_BLOCKED:
        return ChangeOutcome(
            payload={
                **tool_failure(
                    ERROR_RELEASE_FAILED,
                    # The expiry prints here and not on the branch above, because this reading
                    # answered: the allow entry exists and the surviving deny entry overrides it,
                    # which is how both backends resolve a conflict. Saying the entry was written
                    # without saying it is overridden would read as a change that half took.
                    f"NOA checked afterwards: {target.target} is still blocked on "
                    f"{target.server_name}.\nAn allow entry was written and expires "
                    f"{jakarta_stamp(expires_at)}, and the deny entry overrides it.",
                ),
                **common,
                "headline": f"IP still blocked — {target.target}",
                "released": False,
                "allowlisted": False,
            },
            # A measurement, not an absence: every backend answered and the verdict did not move,
            # which is what earns the two `false`s beside it.
            delta=delta(verification=VERIFICATION_MISMATCH, measured_verdict=verdict),
        )
    if verdict != VERDICT_ALLOWLISTED:
        # `not_found`: nothing blocks it and nothing allows it either. No expiry line, and that
        # absence is measured — `new_values` is absent on this branch because every backend
        # answered that it holds no entry at all, so a resolved timestamp here would name a
        # window nothing wrote.
        return ChangeOutcome(
            payload={
                **tool_failure(
                    ERROR_ALLOW_FAILED,
                    "NOA checked afterwards: "
                    + firewall_verdict_sentence(
                        target=target.target, server=target.server_name, verdict=verdict
                    ),
                ),
                **common,
                "headline": f"Nothing found for {target.target}",
                "released": True,
                "allowlisted": False,
            },
            # The verdict moved off `blocked` and stopped short of `allowlisted`, so the delta
            # renders one field change and the failure code says which half is missing.
            delta=delta(verification=VERIFICATION_MISMATCH, measured_verdict=verdict),
        )

    return ChangeOutcome(
        payload=tool_ok(
            **common,
            headline=f"IP unblocked — {target.target}",
            status=STATUS_CHANGED,
            released=True,
            allowlisted=True,
            verified=True,
            unanswered_backends=unanswered,
            message=(
                f"{target.target} is no longer blocked on {target.server_name}.\n"
                f"The allow entry expires {jakarta_stamp(expires_at)}."
            ),
        ),
        delta=delta(verification=VERIFICATION_VERIFIED, measured_verdict=verdict),
    )
