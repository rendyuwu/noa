"""The before→after delta a runner publishes beside its envelope.

**Why the runner states it, and not a reader.** A receipt's two halves are written by different
code at different moments about the same fact: `before` is the gate's in-process preflight and
`after` is the runner's own tool envelope. Where their keys meet they meet on **identity**, and
those keys carry the same value in both halves by construction, because a runner resolves the
machine and the thing on it out of the evidence rather than re-deriving them. Measured across
the seven CHANGE tools the overlap runs from one key (`server`, on the account pair) to five
(`proxmox_vm_nic`: `server`, `node`, `vmid`, `net`, `action`) — and on none of them is the field
that *moved* addressable in both halves: `evidence["nic"]["link_state"]` against
`payload["link_state"]`, `evidence["account"]["suspended"]` against `payload["suspended"]`. So no
reader can diff them: the keys they share cannot yield a change, and the change is not under a
shared key. The only party holding both vocabularies is the runner that produced one of them and
read the other. Held by `apps/api/tests/test_change_receipt_halves.py`, which drives each tool's
gate and then its runner and compares the two halves a receipt is actually built from. It was
prose alone until then, and the prose had the count wrong — the shape of a control asserted by
prose and held by nothing.

The runner also knows more than either half says — which backend answered the confirming read,
whether the write landed but the apply step did not — and that knowledge has no key in `after`
to survive in.

**It does not ride in the payload.** `ChangeOutcome` carries the delta *beside* the envelope,
and `core.approvals.execution.build_receipt` takes it as its own parameter. Four things that
buys, each of which the tempting arrangement — lift it off the payload the way `ok` and
`error_code` are lifted — gives up:

- `ok` and `error_code` are read from the **raw** payload, so "lifted like those" means lifted
  unredacted. The delta is redacted on its own line instead.
- `after` **is** the redacted payload. A delta in the payload puts a key in `after`, and a
  byte-identity guarantee on `after` stops being expressible.
- `tool_runs.result_summary` is that same payload rendered as compact JSON and cut at 2000
  characters (`core.audit.summaries`). A delta in the payload pushes existing content past the
  cut — worst for the tool whose payload is already largest — and the result is not an error but
  an audit field quietly losing its tail, in front of the admin surface, the card and
  `noa_get_action_result`.
- the receipt dict gains exactly one key, as one named edit, rather than as a side effect.

**Identity plus optional facets, not a discriminated union.** A discriminated union means one
value selects exactly one member, and two of the seven CHANGE tools need two members at once:
`whm_firewall_release_and_allow` emits the per-backend map, the resolved expiry and its two
outcome booleans in a single answer. So the shape is `identity` and `verification`, which every
delta has, plus facets a family fills or leaves out.

**Absence is structural.** Every facet is `Optional` in the type and omitted from
`as_payload` when it is `None`, so a renderer that fabricates a measurement has to work at it.
A missing outcome is absent, never `false` — the partial-verdict rule states this for a CHANGE
whose confirming
read came back partial, applied to every facet: an absent field beats a `false` nobody measured.

`changed_fields` carries that distinction twice over, and the difference is load-bearing:

- `None` — no field diff is being reported. Nothing was measured (the write was accepted and the
  postflight could not answer), or the evidence carried no before-value to compare anything
  against, or the value that changed is one that may not be rendered at all
  (`proxmox_reset_vm_password` changes a password).
- `()` — nothing moved, and the runner has grounds for saying so. Ordinarily a diff was taken
  and the two sides matched, which is what a no-op publishes — and it is why a no-op is an
  explicit "nothing changed" rather than an empty delta: a naive before→after diff of a no-op's
  payload renders the identity fields as new values, which would describe a change to a machine
  nothing touched. It is equally what a branch that failed *before writing anything* publishes:
  no comparison happened there and none is owed, because no command went out. The facet states
  what moved, not how the runner came to know it.

  Grounds is the whole of the distinction, and it is narrow: a reading was taken, or the write
  is known not to have been issued. A write that was accepted and then lost — a timeout, a
  dropped connection, a task whose outcome was never read — is `None`, because the change may
  have landed.

**Verification is one field with four states, not a shape**, because that is what it is in the
code already:

- `verified` — the postflight answered and agrees.
- `unavailable` — NOA holds no measurement. The confirming read did not answer, or the change
  itself was refused before one could be taken. `verification_cause` names which.
- `mismatch` — the postflight answered and disagrees. That is a measurement, and it is a
  failure; `pmg_whitelist`'s `postflight_failed` and `proxmox_vm_nic`'s carry it.
- `not_in_force` — the write landed and the step that puts it into effect did not.
  `pmg_whitelist`'s `pmg_sync_failed` is the instance: the config moved and Postfix did not,
  which is neither a change nor a refusal.

**The delta carries no reason.** The one reason field is operator-typed on the card, and the LLM
never authors, relays or sees it; a runner must not echo it back in its payload, because
`result_summary` is derived from that payload and `noa_get_action_result` hands the summary to a
model. A receipt key is the same door one step over, so the fence is checked here
rather than left to seven authors: a reason-bearing key anywhere in a delta is refused at
construction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

# The four states `verification` may hold. Constants because a receipt outlives the call and is
# read by the approval card, the admin audit surface and a renderer that switches on the value —
# a misspelt state reads as an unknown one, which is the direction that must not fail open.
VERIFICATION_VERIFIED: Final = "verified"
VERIFICATION_UNAVAILABLE: Final = "unavailable"
VERIFICATION_MISMATCH: Final = "mismatch"
VERIFICATION_NOT_IN_FORCE: Final = "not_in_force"

VERIFICATION_STATES: Final[frozenset[str]] = frozenset(
    {
        VERIFICATION_VERIFIED,
        VERIFICATION_UNAVAILABLE,
        VERIFICATION_MISMATCH,
        VERIFICATION_NOT_IN_FORCE,
    }
)

# Key names that carry the operator's reason, or a target system's echo of it. Refused anywhere
# in a delta: WHM stores a suspension note and returns it as `suspendreason` on
# every later `listaccts`, and a firewall allow entry carries the reason behind NOA's marker, so
# the words can arrive at a runner from the target system as well as from the request.
# Case-insensitive and whitespace-stripped, the comparison `core.secrets.redaction` makes for the
# same class of mistake one column over.
REASON_BEARING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "reason",
        "proposed_reason",
        "suspendreason",
        "suspend_reason",
        "suspension_note",
        "note",
        "comment",
    }
)


def is_reason_bearing_key(key: str) -> bool:
    """True when a delta must not carry a value under `key`."""
    return key.strip().lower() in REASON_BEARING_KEYS


def reason_bearing_key_paths(value: object, *, _prefix: str = "") -> list[str]:
    """Every location inside `value` that a delta may not carry.

    The same recursive walk `core.secrets.redaction.sensitive_key_paths` makes for credentials,
    asking a different question: a flat top-level scan would pass
    `{"account": {"suspendreason": ...}}` straight into a receipt, and the account summary a WHM
    preflight reads is exactly that shape.

    Paths are dotted, with list positions as `[i]`, so a refusal names where the value was
    without carrying it.
    """
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{_prefix}.{key}" if _prefix else str(key)
            if is_reason_bearing_key(str(key)):
                found.append(path)
            else:
                found.extend(reason_bearing_key_paths(item, _prefix=path))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            found.extend(reason_bearing_key_paths(item, _prefix=f"{_prefix}[{index}]"))
    return found


@dataclass(frozen=True)
class FieldChange:
    """One field that moved, with the value on each side.

    `old` comes from the before-state the operator authorised against and `new` from what the
    runner measured afterwards. Constructing one where they are equal is refused by `ChangeDelta`
    — "changed only" is the rendering rule, and a row whose two sides match is a row a card would
    print as a change that did not happen.
    """

    field: str
    old: Any
    new: Any

    def as_payload(self) -> dict[str, Any]:
        """JSON-native, for the receipt's JSONB."""
        return {"field": self.field, "old": self.old, "new": self.new}


@dataclass(frozen=True)
class ListDelta:
    """What entered and left a list the change is about.

    The facet for a family whose change *is* list membership rather than a field's value. Empty
    `added` and `removed` on a present `ListDelta` is the explicit "the list did not move", the
    same distinction `changed_fields` draws between `()` and `None`.

    `total_entries` is the size of the list the delta is measured against, and it is `None` when
    the runner did not read the whole list — which is the ordinary case for a runner that reads
    only the lines matching its target.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    total_entries: int | None = None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native. `total_entries` is omitted when nothing measured it."""
        payload: dict[str, Any] = {"added": list(self.added), "removed": list(self.removed)}
        if self.total_entries is not None:
            payload["total_entries"] = self.total_entries
        return payload


@dataclass(frozen=True)
class BackendOutcome:
    """One source the change was driven through, and what it answered afterwards.

    Four facts kept apart because they fail apart. `driven` is about the commands — whether this
    backend could be made to run them at all. `answered` is about the confirming read, taken
    separately and from a fresh look, and it is the fact the partial-verdict rule turns on: a
    backend that said nothing has not said the change took. `verdict` is that backend's own
    word, `None` when it
    did not produce one. `error_code` names the remedy when the commands were refused.

    These render regardless of whether the envelope beside them said `ok`, because a refused
    change still measured which backend refused it and which ones answered.
    """

    name: str
    driven: bool
    answered: bool
    verdict: str | None = None
    error_code: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """JSON-native. `verdict` and `error_code` are omitted when there is none."""
        payload: dict[str, Any] = {
            "name": self.name,
            "driven": self.driven,
            "answered": self.answered,
        }
        if self.verdict is not None:
            payload["verdict"] = self.verdict
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload


@dataclass(frozen=True)
class Bound:
    """The bound of a capped list the delta's claim rests on.

    A read that caps rows ships its own bound, and a delta stated against a capped reading owes
    the same thing one surface over: without it, "this address was blocked and is now allowed"
    reads as a statement about every line the firewall holds for that address, when the evidence
    behind it stopped at twenty.
    """

    total: int
    truncated: bool

    def as_payload(self) -> dict[str, Any]:
        """JSON-native, for the receipt's JSONB."""
        return {"total": self.total, "truncated": self.truncated}


@dataclass(frozen=True)
class ChangeDelta:
    """What one approved change moved, as the runner that moved it states it.

    `identity` and `verification` are always present: a delta that cannot say what it is about
    describes nothing, and one that cannot say whether it was confirmed is the claim the
    partial-verdict rule refuses.
    Everything else is a facet, `Optional` in the type, and omitted from `as_payload` when
    absent — see the module docstring for what each `None` means, and for why `changed_fields`
    distinguishes `None` from `()`.
    """

    # What the change was about — the machine, and whatever names the thing on it. Per family,
    # because "which VM's which interface" and "which account on which server" are not one shape.
    identity: Mapping[str, Any]
    # One of `VERIFICATION_STATES`.
    verification: str
    # Why there is no measurement, when there is none. A named code, never a sentence about the
    # operator's request.
    verification_cause: str | None = None
    changed_fields: tuple[FieldChange, ...] | None = None
    list_delta: ListDelta | None = None
    backends: tuple[BackendOutcome, ...] | None = None
    # The sources that produced no answer a decision can rest on. Names, never a count: "one
    # backend was silent" does not say which server to go and look at.
    unanswered: tuple[str, ...] | None = None
    # The slot for a change whose new value has no before twin and cannot be shown either — a
    # one-open delivery URL for a credential NOA generated. Its presence is the claim that the
    # credential may be live; its absence is the claim that it is not.
    delivered_credential: str | None = None
    # New values with no before twin: a resolved expiry, the window that was asked for.
    new_values: Mapping[str, Any] | None = None
    bound: Bound | None = None

    def __post_init__(self) -> None:
        """Refuse a delta that cannot be true, at construction rather than at render time.

        Six checks, in the order they run, and each one is a claim a renderer would otherwise
        print:

        - an unknown `verification`, which would reach a card as a state nothing handles;
        - an empty `identity` — a delta about nothing;
        - a `verification_cause` on a `verified` delta, which is a reason for a non-answer
          attached to an answer;
        - a reason-bearing key anywhere inside it (see the module docstring);
        - a `changed_fields` row whose two sides are equal, which is "changed only" broken;
        - a negative `bound.total`, which is not a bound and would render as one.

        **The reason fence scans `as_payload()` — the bytes that will be stored** — rather than a
        second view of the same record. A hand-maintained mirror of the serialiser is one edit
        away from disagreeing with it, and the direction it would fail in is the silent one: a
        facet added to the payload and forgotten in the mirror rides into the receipt outside the
        fence, with nothing to fail. The four keys such a mirror left out cost nothing to walk:
        the two verification fields and the delivery URL are NOA's own vocabulary holding
        strings, and `bound` — which is *not* NOA's own, since `evidence_bound` lifts it off the
        gate's `approval_context` — holds an integer and a boolean. None of the four names is
        reason-bearing and the walk bottoms out on every one of the values.

        `ValueError` rather than a payload: this is a NOA bug in the runner that built it, not
        anything an operator or a model can act on, and the executor's own catch turns it into a
        named terminal failure with a receipt.
        """
        if self.verification not in VERIFICATION_STATES:
            raise ValueError(f"unknown verification state {self.verification!r}")
        if not self.identity:
            raise ValueError("a change delta must say what it is about")
        if self.verification == VERIFICATION_VERIFIED and self.verification_cause is not None:
            raise ValueError("a verified delta carries no verification cause")

        carried = reason_bearing_key_paths(self.as_payload())
        if carried:
            raise ValueError(f"a change delta must not carry reason-bearing key(s) {carried}")

        for change in self.changed_fields or ():
            if change.old == change.new:
                raise ValueError(f"{change.field!r} did not change; a delta renders changes only")

        if self.bound is not None and self.bound.total < 0:
            raise ValueError("a bound's total cannot be negative")

    def as_payload(self) -> dict[str, Any]:
        """JSON-native fields for the receipt's `delta` key.

        Absent facets are **omitted**, not sent as `null`. The receipt is the record of what was
        measured, and a key holding `null` is a measurement slot a later reader has to know to
        distrust; the card's own body takes the opposite rule for the same reason one surface
        over (a renderer that switches on a field reads a missing key as one it forgot), and the
        card is what turns this absence into `null` where a renderer needs it.
        """
        payload: dict[str, Any] = {
            "identity": dict(self.identity),
            "verification": self.verification,
        }
        if self.verification_cause is not None:
            payload["verification_cause"] = self.verification_cause
        if self.changed_fields is not None:
            payload["changed_fields"] = [change.as_payload() for change in self.changed_fields]
        if self.list_delta is not None:
            payload["list_delta"] = self.list_delta.as_payload()
        if self.backends is not None:
            payload["backends"] = [backend.as_payload() for backend in self.backends]
        if self.unanswered is not None:
            payload["unanswered"] = list(self.unanswered)
        if self.delivered_credential is not None:
            payload["delivered_credential"] = self.delivered_credential
        if self.new_values is not None:
            payload["new_values"] = dict(self.new_values)
        if self.bound is not None:
            payload["bound"] = self.bound.as_payload()
        return payload


@dataclass(frozen=True)
class ChangeOutcome:
    """A runner's answer: the tool envelope, and the delta beside it.

    Two fields rather than one dict, because the seam is the point. `payload` is what
    `core.audit.summaries` bounds and what becomes the receipt's `after` half, byte for byte as
    it was before this type existed; `delta` is what the runner measured, redacted separately and
    stored under its own key.

    `delta is None` is a claim and not a gap: nothing was measured, so nothing is stated. That is
    also the discriminator between the two failure paths, and it has to be structural — an
    executor refusal and a runner failure are both `ok: False`, so the envelope cannot tell them
    apart. Whether the runner emitted a delta at all can.
    """

    payload: dict[str, Any]
    delta: ChangeDelta | None = None


__all__ = [
    "REASON_BEARING_KEYS",
    "VERIFICATION_MISMATCH",
    "VERIFICATION_NOT_IN_FORCE",
    "VERIFICATION_STATES",
    "VERIFICATION_UNAVAILABLE",
    "VERIFICATION_VERIFIED",
    "BackendOutcome",
    "Bound",
    "ChangeDelta",
    "ChangeOutcome",
    "FieldChange",
    "ListDelta",
    "is_reason_bearing_key",
    "reason_bearing_key_paths",
]
