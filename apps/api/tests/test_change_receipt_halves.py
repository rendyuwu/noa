"""The two halves of a receipt, measured against each other.

`core/approvals/delta.py` argues that the runner has to state the before→after itself because
nothing downstream can compute it: `before` is the gate's in-process preflight and `after` is the
runner's own envelope, and where their keys meet they meet on identity — same key, same value —
while the field that actually moved is never addressable in both. That was prose in four modules
and a doc, and the prose was wrong: it claimed the two halves overlap in at most one key, when
`proxmox_vm_nic` shares five.

So the property is measured here, for every CHANGE tool, end to end: the tool is called through
its gate, the evidence it wrote is read back off `approval_context`, and the runner is handed
that evidence the way `core.approvals.execution` hands it over. Both halves are then the ones a
receipt is actually built from, rather than a fixture's idea of them — which matters, because a
fixture asserting a property of itself is exactly what failed here. A tool that adds an evidence
key its runner also answers with does not need the fixture updated to keep passing, and that is
the drift this has to catch.

Three things the same seven-tool harness answers, and the second and third are here because
building the lane twice is the only alternative:

- **the halves cannot be diffed** — the keys they share carry equal values, so no comparison of
  them yields a change, and no field the delta reports as moved is among those keys.
- **how large a payload is, and which one the pin belongs on.** The `result_summary` pin moved
  here from `test_change_delta_runners.py`, and not for room: the size question had ended up
  split across two files, one pinning a single payload and the other measuring all seven, which
  is one claim in two places. Which tool renders longest is a **measurement and moves with the
  wording**, and it has now moved twice: `proxmox_reset_vm_password` led while the runners
  answered in their old strings, `whm_firewall_release_and_allow` took it when each branch gained
  a heading and its sentence named the expiry in words, and the password tool leads again now
  that its branches carry the owner's restart sentence and say how long the delivered link keeps
  working. What does not move is which payload the pin belongs on — the release tool is the
  largest *once a delta is folded in*, because it fills four facets in one answer, and that is
  what the pin is actually guarding. Both orderings are asserted below, and they no longer name
  the same tool, which is the whole reason both are asserted.
- **four rules about the words on the card, each of which was read by nothing.** Both halves name
  the change in the operator's words; a runner's before-clause starts its own line instead of
  running into the sentence ahead of it; the four spellings of that clause stay four; and the
  optional heading over a target system's own text is carried by exactly the tools that ship a
  block to head. A docstring is not a check — the before-clause's newline rule was written down in
  `proxmox_nic_runner` *because* that family had just shipped a space on every site it had, with
  nothing to catch the next family spelling it the same way. Three of the four are claims across
  families rather than about one, so they land on the harness that already drives every family
  rather than in seven per-tool files.

`test_every_change_tool_is_covered_here` reads the case list against `build_change_runners`, so
the eighth CHANGE tool fails here until somebody states its halves rather than quietly not being
covered — and the two rules parametrised over `CASES` arrive with it for the same reason.

**The harness itself lives in `support/change_halves.py`.** It moved when the four card-text rules
above took this file past the 900-line cap `test_config.py` enforces over `git ls-files`. What
moved is the apparatus — `Halves`, the lane per tool, and `CASES` — and no claim moved with it: a
cap is met by splitting, never by trimming an assertion to make a number go down. Adding an eighth
lane is an edit there; stating what has to be true of it is an edit here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast

import pytest

from core.approvals.context import evidence_from_context
from core.approvals.delta import FieldChange
from core.approvals.execution import (
    RECEIPT_DELTA_KEY,
)
from core.audit.summaries import MAX_RESULT_SUMMARY_LENGTH, result_summary
from core.integrations.whm.accounts import account_suspension_state
from noa_api.mcp_tools.change_gate import (
    EVIDENCE_HEADING,
    EVIDENCE_HEADLINE,
    REQUIRED_EVIDENCE_KEYS,
)
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.pmg_whitelist import (
    ACTION_REMOVE,
    EVIDENCE_MATCHES,
)
from noa_api.mcp_tools.proxmox_nic import (
    ACTION_DISABLE,
    ACTION_ENABLE,
    EVIDENCE_NIC,
    TOOL_PROXMOX_VM_NIC,
    link_state_for,
)
from noa_api.mcp_tools.proxmox_nic_runner import _before_clause as nic_before_clause
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.whm_account_change import (
    EVIDENCE_ACCOUNT,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    _SUSPEND,
    _UNSUSPEND,
    DELTA_FIELD_SUSPENDED,
    _AccountChangeDirection,
    _ChangeTarget,
)
from noa_api.mcp_tools.whm_account_change_runner import _before_clause as account_before_clause
from noa_api.mcp_tools.whm_firewall_allowlist import (
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
)
from noa_api.mcp_tools.whm_firewall_change import (
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    build_whm_firewall_release_runner,
)
from support.change_delta import outcome_of
from support.change_halves import (
    CASES,
    Halves,
)
from support.pmg import (
    BYSTANDER,
    TARGET,
    FakePMGWhitelist,
    call_whitelist,
    whitelist_change_context,
)
from support.servers import build_tool_context
from support.whm_firewall_change import (
    execution_request,
    release_context,
    released_box,
)

# Every clock-stamped instant in a rendered summary, replaced by one of fixed width so a length
# can be pinned at all. `isoformat()` omits `.ffffff` entirely when the microsecond is
# zero, so the raw bytes are seven characters shorter roughly once in a million runs — a pin on
# them would be a test that fails on a schedule nobody can reproduce.
_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00")
FIXED_INSTANT = "2026-09-09T12:00:00.000000+00:00"

# The same instant again, in the words the operator reads it in. Its rendered width *moves*: the
# day and the hour drop their leading zero, so `3 Sep 2026, 4:41 AM (WIB)` is three characters
# shorter than `13 Sep 2026, 11:41 AM (WIB)`. A length pinned over those bytes would fail on a
# schedule nobody can reproduce, which is the same reason the ISO form above is normalised.
_FRIENDLY_STAMP = re.compile(r"\d{1,2} [A-Z][a-z]{2} \d{4}, \d{1,2}:\d{2} [AP]M \(WIB\)")
FIXED_FRIENDLY_STAMP = "12 Sep 2026, 8:26 PM (WIB)"

# `whm_firewall_release_and_allow`'s summary, with those instants normalised. 426 of the 2000
# characters `tool_runs.result_summary` holds, so the headroom is 1574 — 79% of the column still
# free. It was 391 before each branch gained a heading and the expiry moved into the sentence in
# words, so the whole wording pass cost 35 characters of a column with 1600 to spare.
#
# **This is no longer the longest payload bare, and it is still the one to pin.** The password
# tool's branches now carry the owner's restart sentence and the life of the delivered link, which
# takes them past this one unfolded — and leaves them short of it folded, which is the measure the
# cut is about. `test_which_payload_the_summary_pin_belongs_on` below asserts both orderings, so
# neither is assumed and a wording pass that reverses either fails there rather than here.
#
# Pinned rather than bounded, because what it is guarding is *growth*: the delta doubles this
# figure the moment it enters the payload (857 characters, asserted below), and a `<= 2000`
# assertion would sit green through that and through the next four fields after it. The cut this
# is really about is silent — `result_summary` replaces the tail with `...` rather than failing —
# and it lands on the audit trail, the card's execution line and `noa_get_action_result` at once.
PINNED_SUMMARY_LENGTH = 426
SUMMARY_HEADROOM = MAX_RESULT_SUMMARY_LENGTH - PINNED_SUMMARY_LENGTH

# The card's own heading, written by the gate for a PENDING card and again by the runner for a
# completed one. One name, two facts, two moments — `Unblock an IP — 1.2.3.4` against `IP still
# blocked — 1.2.3.4` — so it is the one shared key the halves are allowed to disagree on, and it
# is named here rather than left to a reader to infer from a green test.
CARD_TEXT_KEYS = frozenset({EVIDENCE_HEADLINE})

# The runner's own sentence — the paragraph under the heading on both card surfaces and in the
# block copied off them. A literal rather than an imported constant because `tool_ok(**payload)`
# is a free-form kwargs passthrough with no builder to hang one off; the embed names it once
# (`RUNNER_MESSAGE_KEY` in `apps/web-embed/src/lib/approvals/verdict.ts`) and this is the other
# end of that same string.
RUNNER_MESSAGE_KEY = "message"


def pinned(summary: str | None) -> str:
    """One rendered summary with its clock-stamped bytes normalised.

    Both spellings of an instant, because the release runner now composes the operator-facing
    one into its sentence and the ISO one still rides on `expires_at`.
    """
    return _FRIENDLY_STAMP.sub(FIXED_FRIENDLY_STAMP, _INSTANT.sub(FIXED_INSTANT, summary or ""))


def test_the_normaliser_still_separates_what_it_is_not_hiding() -> None:
    """The clock bytes are dropped from the compare; nothing else is.

    A normaliser wide enough to swallow the sentence around a stamp would make every length pin
    in this file green against any wording at all. So: two stamps of different widths collapse to
    one, and a sentence that differs anywhere else still differs afterwards.
    """
    early = "The allow entry expires 3 Sep 2026, 4:41 AM (WIB)."
    late = "The allow entry expires 13 Sep 2026, 11:41 PM (WIB)."

    assert pinned(early) == pinned(late)
    assert pinned(early) != pinned(early.replace("allow", "deny"))
    assert len(pinned(early)) == len(pinned(late))


def test_every_change_tool_is_covered_here() -> None:
    """The eighth CHANGE tool fails here rather than quietly not being covered.

    Read off `build_change_runners`, which is the same mapping `registry.py` asserts coverage
    against — so "all seven" is a fact about the registered surface rather than a count somebody
    kept up to date by hand.
    """
    assert set(CASES) == set(build_change_runners(context=build_tool_context().context))


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_the_two_halves_of_a_receipt_cannot_be_diffed(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the whole design rests on, measured per tool.

    Two claims, and together they say a reader holding both halves cannot compute what changed:

    - every key the halves share carries the **same value** in both, because a runner resolves
      its identity out of the evidence rather than re-deriving it. A shared key can therefore
      never yield a difference. `CARD_TEXT_KEYS` is the one exemption and it is named rather
      than implied — the card's heading is written by both halves under one name and the two
      deliberately disagree, because the gate states what is being asked and the runner states
      what happened. Neither is an identity, and the second claim below still covers them.
    - no field the delta reports as moved is one of those shared keys. The change is real and it
      is simply not addressable in both vocabularies: `evidence["nic"]["link_state"]` against
      `payload["link_state"]`, `evidence["account"]["suspended"]` against `payload["suspended"]`.

    The overlap itself is pinned per tool in `CASES`, because the sentence this replaces got the
    count wrong and a property with no number attached is one nobody can check.
    """
    build, expected_shared = CASES[tool]

    halves = await build(monkeypatch)

    assert halves.shared_keys == expected_shared | (CARD_TEXT_KEYS & set(halves.payload))
    for key in sorted(halves.shared_keys - CARD_TEXT_KEYS):
        assert halves.evidence[key] == halves.payload[key], key
    assert halves.moved_fields & halves.shared_keys == set()


async def test_which_payload_the_summary_pin_belongs_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two superlatives, and they are not the same tool. Measured, because one was assumed.

    Which tool renders longest is a measurement, and it moves with the wording — it was
    `proxmox_reset_vm_password` while the runners answered in their old strings, then
    `whm_firewall_release_and_allow` once each branch carried a heading and named its expiry in
    words, and it is the password tool again now that its branches carry the owner's restart
    sentence and how long the delivered link keeps working. So it is asserted rather than
    assumed, and re-measured whenever a runner is reworded.

    What the pin is actually guarding does not move with the wording, and this is the assertion
    that says so. The cut it exists for is the one a **delta folded into the payload** would push
    content past, and by that measure the release tool is the worst case by a wide margin — it
    fills four facets in one answer, so its delta is the biggest thing that could ever be folded
    in. Both orderings are asserted, because keeping only the first would move the pin to the
    payload where the risk is smaller.

    Orderings rather than lengths: the exact figures belong where the cut is argued, and these
    summaries carry timestamps whose rendered width moves.
    """
    bare: dict[str, int] = {}
    folded: dict[str, int] = {}
    for tool, (build, _) in sorted(CASES.items()):
        halves = await build(monkeypatch)
        delta = {} if halves.delta is None else halves.delta.as_payload()
        bare[tool] = len(result_summary(halves.payload) or "")
        folded[tool] = len(result_summary({**halves.payload, RECEIPT_DELTA_KEY: delta}) or "")

    assert max(bare, key=lambda tool: bare[tool]) == TOOL_PROXMOX_RESET_VM_PASSWORD
    assert max(folded, key=lambda tool: folded[tool]) == TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW
    # Not a tie on either: a `max` over equal values answers whichever came first.
    assert sorted(bare.values())[-1] > sorted(bare.values())[-2]
    assert sorted(folded.values())[-1] > sorted(folded.values())[-2]


async def test_the_payload_the_pin_guards_stays_where_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `result_summary` pin, and the reason the delta rides beside the payload.

    Two claims. The payload carries exactly the thirteen keys asserted below — the twelve it
    carried before a delta existed, plus the heading the wording pass added — asserted on the key
    set, which is the clock-safe form of byte identity here, since two of the values are
    timestamps. And its rendered summary is 426 characters of the 2000 the column holds, leaving
    1574 free.

    The negative control is the whole argument: folding the delta into the payload takes the same
    summary to 857 characters. Nothing would fail — `result_summary` cuts the tail and marks it
    with an ellipsis — so the loss would land silently on the audit trail, on the card's
    execution-result line and on `noa_get_action_result`, and a byte-identity test on `before`
    and `after` would never see it.

    **Every figure above is the one the assertions under it read**, which is the correction this
    docstring is carrying: it went on stating 391, 1609 and 822 after the numbers below had moved
    to 426, 1574 and 857, and a docstring disagreeing with the assertion three lines under it is
    how a reader takes the wrong measurement out of the file kept to hold the right one. This is
    no longer the longest payload rendered bare — `proxmox_reset_vm_password` is — and it is
    still the largest once a delta is folded in, which is the measure the 2000-character cut is
    about and the reason the pin sits here.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)

    outcome = await outcome_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert set(outcome.payload) == {
        "ok",
        "server",
        "target",
        "duration_minutes",
        "expires_at",
        "backends",
        "status",
        "released",
        "allowlisted",
        "verified",
        "unanswered_backends",
        "headline",
        "message",
    }
    summary = pinned(result_summary(outcome.payload))
    assert len(summary) == PINNED_SUMMARY_LENGTH
    assert SUMMARY_HEADROOM == 1574

    assert outcome.delta is not None
    folded_in = pinned(
        result_summary({**outcome.payload, RECEIPT_DELTA_KEY: outcome.delta.as_payload()})
    )
    assert len(folded_in) == 857


# --------------------------------------------------------------------------------------
# The words on the card: what both halves must name, and how a second fact is joined on
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_both_halves_name_the_change_in_the_operators_words(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card has a heading at both moments, and the gate also restates what was asked.

    Three strings, and this is `CARD_TEXT_KEYS` above turned into a check rather than a comment:
    the gate writes `headline` and `asked` for the PENDING card, the runner writes `headline`
    again for the completed one, and a surface that finds any of the three missing falls back to
    a humanised tool name — a heading that reads like a heading and tells an operator nothing
    about what they are approving or what just ran.

    **Blank is missing**, the same rule `assert_evidence_usable` applies in `change_gate.py`: a
    key present with a whitespace-only value renders as an empty heading, which is the same card
    with a less findable cause.

    The gate half is not a second copy of that guard. `assert_evidence_usable` runs in-process on
    the mapping a tool hands it, while what is read here came back out of `approval_context`
    JSONB through `evidence_from_context` — which is where a key that did not survive the round
    trip reads as an absent one, silently, minutes after the guard passed.

    **The runner half has no production guard at all, and it is the half this exists for.**
    Nothing anywhere requires a runner to answer with a `headline`; a family added later can
    simply not write one, and only the completed card would ever notice.

    **Both runner strings, because the surfaces lean on both and only one was ever guarded.** The
    heading names the change and the `message` is the paragraph under it, and a family that wrote
    a heading and no sentence would render a completed card whose one paragraph is the *request*
    restated — the card's fallback where the runner said nothing — under a corner reading
    `Approved`, with nothing marking that the runner never spoke. The sentence also carries three
    things no other key does: the name of any source that could not answer, the before-clause's
    four spellings, and the owner-stated consequence. Guarding the heading alone left the string
    that holds all three unheld.

    **Stated gap, and it is the same one as the heading's: one branch per tool.** The gate half is
    also shadowed — `assert_evidence_usable` refuses the call before a request row exists, so a
    missing evidence key fails this parametrisation earlier and elsewhere than the loop below.
    The round-trip case argued above is real and is not what reddens. The runner half is the half
    these assertions actually measure.

    **Stated gap: the harness drives one branch per tool, the confirmed one.** Every runner also
    writes a heading on its no-op, mismatch and refusal branches, and none of those are reached
    from here — the per-family files drive them, one assertion at a time. What is bound here is
    that each family writes one at all, across the whole registered CHANGE surface.

    The tool set is `CASES`, which `test_every_change_tool_is_covered_here` holds against
    `build_change_runners`, so an eighth CHANGE tool arrives in this parametrisation rather than
    being a name somebody has to remember to add to a list.
    """
    build, _ = CASES[tool]

    halves = await build(monkeypatch)

    for key in REQUIRED_EVIDENCE_KEYS:
        asked_for = halves.evidence.get(key)
        assert isinstance(asked_for, str) and asked_for.strip(), key
    for key in (EVIDENCE_HEADLINE, RUNNER_MESSAGE_KEY):
        answered = halves.payload.get(key)
        assert isinstance(answered, str) and answered.strip(), key


def account_clause(
    changed_fields: tuple[FieldChange, ...] | None,
    *,
    suspended_before: bool | None,
    direction: _AccountChangeDirection = _SUSPEND,
) -> str:
    """One before-clause, composed by the account runner's own function.

    `suspended_before` is the only field `_before_clause` reads off the target it is handed, so
    `client`, `username` and `server_name` are passed empty rather than rebuilt here: a value
    nothing reads is a value the next reader goes looking for a use of. The direction defaults to
    suspend because the pair are mirror images by construction (`_AccountChangeDirection`), and
    `_UNSUSPEND` is driven below.
    """
    target = _ChangeTarget(
        client=cast(Any, None),
        username="",
        server_name="",
        suspended_before=suspended_before,
    )
    return account_before_clause(changed_fields, target=target, direction=direction)


def account_clause_of(halves: Halves, direction: _AccountChangeDirection) -> str:
    """The clause the account runner appended to *this* run, recomposed from the same material.

    `changed_fields` off the delta and `suspended_before` off the evidence through
    `account_suspension_state`, which are the two the runner itself computed the clause from —
    so what this returns is the runner's own sentence rather than a fixture's idea of it, and a
    reworded clause needs no edit here.
    """
    account = halves.evidence.get(EVIDENCE_ACCOUNT)
    return account_clause(
        None if halves.delta is None else halves.delta.changed_fields,
        suspended_before=account_suspension_state(account) if isinstance(account, dict) else None,
        direction=direction,
    )


# The families that compose a before-clause, and the production call that reproduces the one this
# harness's run produced. Both composers arrive private and are imported rather than re-derived,
# for the reason `approved_run` reads both halves off a real receipt: a second copy of a wording
# rule is a copy that can agree with a test and disagree with a card.
#
# The four tools not listed answer in one measured fact and compose no before-clause at all.
# **This list is hand-kept and nothing reads it against the code** — unlike `CASES`, which
# `test_every_change_tool_is_covered_here` holds against `build_change_runners`. There is no
# registry of before-clause composers to read: each family's is a private function in its own
# runner. So a family added later that composes one and joins it with a space is not caught here,
# and the rule it would break is stated in `_before_clause`'s docstring in both families that
# have one.
BEFORE_CLAUSE_TOOLS: dict[str, Callable[[Halves], str]] = {
    TOOL_PROXMOX_VM_NIC: lambda halves: nic_before_clause(
        None if halves.delta is None else halves.delta.changed_fields,
        evidence=halves.evidence,
    ),
    TOOL_WHM_SUSPEND_ACCOUNT: lambda halves: account_clause_of(halves, _SUSPEND),
    TOOL_WHM_UNSUSPEND_ACCOUNT: lambda halves: account_clause_of(halves, _UNSUSPEND),
}


@pytest.mark.parametrize("tool", sorted(BEFORE_CLAUSE_TOOLS))
async def test_a_before_clause_is_joined_to_its_sentence_with_a_newline(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The before-clause starts its own line. A space there runs two facts into one paragraph.

    What the clause says and what the sentence above it says are **two separately measured
    facts** — what the target reads now, and what NOA held for it before — and both card surfaces
    render a runner's bytes with no transformation at all. So the join is the whole difference
    between two lines an operator reads apart and one run-on paragraph they read as a single
    claim.

    This was prose in `_before_clause`'s docstring in `proxmox_nic_runner.py` and read by
    nothing, which is exactly how it came to be written: that family was the one spelling the
    join with a space, on every site it had, while the account pair and the firewall release tool
    spelled it `\\n`. A rule stated only in the family that got it wrong is a rule the next family
    added can get wrong the same way.

    The clause is **recomposed by the production function the runner called**, off the delta and
    the evidence the runner used, so nothing is retyped and a reworded clause changes this test's
    expectation with it. What is asserted is only where the clause sits.

    **Stated gap: one branch per tool, the confirmed one.** Each family appends the clause from
    several branches, and this reaches one of them per family. The join is spelled the same way on
    the others, but that is a fact about the code a reader can check and not one this measures.
    """
    build, _ = CASES[tool]

    halves = await build(monkeypatch)

    clause = BEFORE_CLAUSE_TOOLS[tool](halves)
    assert str(halves.payload["message"]).endswith(f"\n{clause}")


def _shared_ending(one: str, other: str) -> str:
    """The longest ending two clauses have in common.

    Derived rather than typed, so the grammar assertions below hold the *code's* words: a
    deliberate rewording of a spelling moves this with it, while a fold of two grammars into one
    makes a reading-only clause end the way a comparison does and reddens.
    """
    shared = 0
    while shared < min(len(one), len(other)) and one[-1 - shared] == other[-1 - shared]:
        shared += 1
    return one[len(one) - shared :]


def test_the_four_before_clause_spellings_stay_four() -> None:
    """Four answers to "what was it before", and folding any two of them loses a distinction.

    Both families compose the same four, and the **grammar carries the distinction**: "before
    this ran" claims a *comparison* — NOA holds both sides and is naming the one it started from
    — while "when NOA last read it" claims only a *reading*. The two middle ones are the pair a
    later simplification folds back together, and folding them restores a card that tells an
    operator NOA holds no reading of the target while that reading sits on `approval_context`,
    on the one branch where they have to go and check the machine by hand.

    Distinctness rather than presence, because each sentence asserted alone passes against a
    composer that prints that one sentence always. Composed by the production functions and never
    retyped here: what has to hold is that the four *differ*, not what any one of them says.

    **The two middle spellings are driven at one value on purpose, in both families.** A fold of
    their grammars is what this is for, and two clauses naming different values stay distinct
    through a fold — the set would still be four and the check would sit green through exactly
    the regression it exists to catch. So the account pair is driven where its `()` case is
    reachable, with the gate-time reading already equal to the state the change asked for, which
    is also the one shape where the composer's two sources name the same word; and the NIC's
    first three all quote one reading, so nothing but the grammar separates them.

    **Limit, in the account family only: the moved spelling cannot join them at that value.** Its
    `old` side is a boolean and `ChangeDelta` refuses an equal-sided row, so it necessarily names
    the other state and is held apart by value as well as by grammar. The NIC set has no such
    limit and separates all three by grammar alone.

    **That limit is why counting the set is not enough on its own, and the second assertion below
    is the one that holds the account family.** A fold of the two middle grammars leaves the
    account set at four, because the moved spelling names the *other* boolean and stays distinct
    by value through it — measured, not reasoned: folding `_before_clause`'s could-not-confirm
    branch into the comparison wording leaves `len(set(...)) == 4` green. So the grammars are
    asserted directly. The comparison suffix is **derived from the two comparison spellings
    themselves** rather than typed here — the longest ending the two share — and the rule is that
    neither clause claiming only a reading may end with it. Nothing in this file names the words
    of any spelling, so a deliberate rewording still moves the expectation with the code, and a
    fold of the grammars reddens in both families rather than in one.

    **Stated gap: this drives the composers, not their call sites.** Which branch hands which
    `changed_fields` is bound in the per-family files (`test_proxmox_nic_runner.py`,
    `test_confirm_after_failed_write.py`, the suspend and unsuspend runner files), and the
    unsuspend direction is driven by the join test above rather than here — the pair are mirror
    images through `_state_words`, so a fourth set would measure that helper twice.
    """
    reading = link_state_for(ACTION_ENABLE)
    on_the_card = {EVIDENCE_NIC: {"link_state": reading}}
    nic = {
        "compared, and it moved": nic_before_clause(
            (FieldChange(field="link_state", old=reading, new=link_state_for(ACTION_DISABLE)),),
            evidence=on_the_card,
        ),
        "compared, and it matched": nic_before_clause((), evidence=on_the_card),
        "read, never compared": nic_before_clause(None, evidence=on_the_card),
        "nothing was read": nic_before_clause(None, evidence={}),
    }
    account = {
        "compared, and it moved": account_clause(
            (FieldChange(field=DELTA_FIELD_SUSPENDED, old=False, new=True),),
            suspended_before=False,
        ),
        "compared, and it matched": account_clause((), suspended_before=True),
        "read, never compared": account_clause(None, suspended_before=True),
        "nothing was read": account_clause(None, suspended_before=None),
    }

    assert len(set(nic.values())) == 4, nic
    assert len(set(account.values())) == 4, account
    # The grammars, which the count above cannot reach in the account family. A clause that claims
    # only a reading must not end the way the two comparison clauses end.
    for family in (nic, account):
        claims_a_comparison = _shared_ending(
            family["compared, and it moved"], family["compared, and it matched"]
        )
        # A degenerate suffix would make the two assertions under it pass against anything.
        assert len(claims_a_comparison) > len(" ran."), claims_a_comparison
        assert not family["read, never compared"].endswith(claims_a_comparison), family
        assert not family["nothing was read"].endswith(claims_a_comparison), family
    # The one spelling both families share, because it names no value from either vocabulary.
    # Asserted rather than left to the reader: it is what the other three are held apart from.
    assert nic["nothing was read"] == account["nothing was read"]


# --------------------------------------------------------------------------------------
# The optional third evidence key: its presence is the decision, so which tools carry it
# is a property rather than a comment
# --------------------------------------------------------------------------------------


# The lanes whose gate evidence heads a raw block of the target system's own text. Both firewall
# tools ship one on every call; the other five ship none, because they read structured fields and
# have no vendor free-form text to head.
#
# **Keyed on the lane, not on the tool**, and `pmg_whitelist` is why: its heading turns on its own
# argument, so the tool belongs on the carrying side for a `remove` and on the absent side for an
# `add`. `CASES` drives the add, so PMG sits below rather than here, and the remove direction is
# measured by the test after this one rather than left as a gap.
EVIDENCE_HEADING_LANES = frozenset(
    {
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    }
)


@pytest.mark.parametrize("tool", sorted(CASES))
async def test_only_the_tools_with_a_block_to_head_carry_a_heading_for_one(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`evidence_heading` is present where there is text to head and absent where there is none.

    The card draws the block where the key is there and draws nothing where it is not, so **the
    key's presence is the decision** rather than a label on a decision made elsewhere. That makes
    which tools carry it a property, and one with no other reader: it deliberately cannot join
    `REQUIRED_EVIDENCE_KEYS`, since making it unconditional would refuse four gates that are
    correct, and an optional key with no check is one a later tool forgets while the card simply
    draws nothing and nobody notices.

    Absent means **absent**, not `None`: `assert_evidence_usable` never looks at this key, so a
    tool that wrote it empty would reach the card and put a heading over nothing. The two sides
    are therefore asserted with `not in` and with the same blank-is-missing rule the required keys
    get.

    Parametrised over `CASES`, which `test_every_change_tool_is_covered_here` holds against
    `build_change_runners` — so an eighth CHANGE tool does not merely land in this file, it lands
    on one side of this partition and fails until somebody decides which. That is the difference
    between this list and `BEFORE_CLAUSE_TOOLS` above, which nothing reads against the code.
    """
    build, _ = CASES[tool]

    halves = await build(monkeypatch)

    heading = halves.evidence.get(EVIDENCE_HEADING)
    if tool in EVIDENCE_HEADING_LANES:
        assert isinstance(heading, str) and heading.strip()
    else:
        assert EVIDENCE_HEADING not in halves.evidence


async def test_a_pmg_remove_heads_the_lines_it_is_going_to_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PMG is the one tool whose heading turns on its argument, and this is its other direction.

    `CASES` drives the `add`, which lands on the absent side above: nothing matched, because the
    address is not on the list, and that emptiness *is* the before-state — so a heading there
    would sit over nothing. A `remove` is the same tool with the matching lines present, and those
    lines are exactly what the change will delete.

    Both halves of that are asserted here rather than only the heading, because the heading alone
    would pass against a tool that wrote one unconditionally — which is the version this key was
    deliberately not built as. The gate is driven, not the runner: this key is written at gate
    time and read by the PENDING card, and it never reaches a runner at all.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET])
    )

    await call_whitelist(fixture, action=ACTION_REMOVE, target=TARGET)

    evidence = evidence_from_context(fixture.action_requests.requests[0].approval_context)
    heading = evidence.get(EVIDENCE_HEADING)
    assert isinstance(heading, str) and heading.strip()
    assert evidence[EVIDENCE_MATCHES]
