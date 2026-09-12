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

Two things the same seven-tool harness answers, and the second is here because building it twice
is the only alternative:

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

`test_every_change_tool_is_covered_here` reads the case list against `build_change_runners`, so
the eighth CHANGE tool fails here until somebody states its halves rather than quietly not being
covered.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from core.approvals.context import arguments_from_context, evidence_from_context
from core.approvals.delta import ChangeDelta
from core.approvals.execution import (
    RECEIPT_AFTER_KEY,
    RECEIPT_BEFORE_KEY,
    RECEIPT_DELTA_KEY,
    ChangeExecutionRequest,
    ChangeRunner,
    build_receipt,
)
from core.audit.summaries import MAX_RESULT_SUMMARY_LENGTH, result_summary
from noa_api.mcp_tools.change_gate import EVIDENCE_HEADLINE
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.pmg_whitelist import TOOL_PMG_WHITELIST
from noa_api.mcp_tools.pmg_whitelist_runner import build_pmg_whitelist_runner
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_nic_runner import build_proxmox_vm_nic_runner
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.proxmox_password_runner import build_proxmox_reset_vm_password_runner
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
    build_whm_unsuspend_runner,
)
from noa_api.mcp_tools.whm_firewall_allowlist import (
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    build_whm_firewall_allowlist_remove_runner,
)
from noa_api.mcp_tools.whm_firewall_change import (
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    build_whm_firewall_release_runner,
)
from support.action_decisions import REASON
from support.change_delta import outcome_of
from support.mcp_identity import authenticated_caller, http_request_context
from support.pmg import call_whitelist, whitelist_change_context
from support.proxmox_nic import call_nic, nic_context, no_polling_delay
from support.proxmox_password import no_polling_delay as no_password_polling_delay
from support.proxmox_password import reset, reset_context
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    UNSUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_success_body,
)
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    IMUNIFY_CLEAN,
    IMUNIFY_DROP,
    IMUNIFY_WHITE,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    imunify_answer,
    imunify_backend,
)
from support.whm_firewall_change import (
    allowlist_remove,
    execution_request,
    release,
    release_context,
    released_box,
)

# The WHM account the two account lanes run against, and the reseller WHM reports as its owner.
# `whm_server`'s credential is `root`, and the runner refuses unless the two agree.
ACCOUNT = "acmeco"
OWNER = "root"


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


@dataclass(frozen=True)
class Halves:
    """One approved change end to end, as its receipt is built from it.

    `evidence` is what the gate wrote and the operator was asked against; `payload` is what the
    runner answered. Both are read back off a receipt `build_receipt` actually built, rather than
    from the values handed to it: `after` is the payload AFTER `redact_mapping`, and a harness
    holding the raw envelope instead would assert equality against bytes no receipt contains.
    That gap is empty today only because no CHANGE payload currently carries a key on the
    redactor's list, which is a coincidence rather than a property — `proxmox_reset_vm_password`
    already carries a credential and already shares a key, so it is one field name away from
    `password` and the harness would have gone on agreeing in the weaker direction.
    """

    tool: str
    evidence: dict[str, Any]
    payload: dict[str, Any]
    delta: ChangeDelta | None

    @property
    def shared_keys(self) -> set[str]:
        """The top-level keys both halves spell the same way."""
        return set(self.evidence) & set(self.payload)

    @property
    def moved_fields(self) -> set[str]:
        """Every field the delta reports as having changed."""
        rendered = {} if self.delta is None else self.delta.as_payload()
        return {change["field"] for change in rendered.get("changed_fields") or []}


async def approved_run(fixture: ToolFixture, runner: ChangeRunner, *, tool: str) -> Halves:
    """Run `runner` against the request the gate just wrote, and return both halves.

    The request is built the way `core.approvals.execution` builds one — arguments and evidence
    lifted off `approval_context` by the production readers — so the evidence the runner sees is
    the row's, not a fixture's approximation of it. Both halves then come off `build_receipt`
    itself rather than off the values passed to it, which is what makes this harness's claim about
    receipts true by construction instead of true by coincidence.
    """
    recorded = fixture.action_requests.requests[0]
    request = ChangeExecutionRequest(
        action_request_id=recorded.action_request_id,
        tool_run_id=uuid4(),
        tool_name=tool,
        arguments=arguments_from_context(recorded.approval_context),
        evidence=evidence_from_context(recorded.approval_context),
        reason=REASON,
    )
    outcome = await outcome_of(runner, request)
    receipt = build_receipt(
        evidence=request.evidence,
        payload=outcome.payload,
        delta=outcome.delta,
    )
    return Halves(
        tool=tool,
        evidence=dict(receipt[RECEIPT_BEFORE_KEY]),
        payload=dict(receipt[RECEIPT_AFTER_KEY]),
        delta=outcome.delta,
    )


# --------------------------------------------------------------------------------------
# One lane per CHANGE tool: call the gate, then run what it authorised
# --------------------------------------------------------------------------------------


async def release_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The firewall-release tool, on a box that reads blocked before the change and allowlisted
    after it.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_DENY_LINE), csf_answer(CSF_ALLOW_LINE)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_DROP), imunify_answer(IMUNIFY_WHITE)),
        ),
    )
    await release(fixture)
    return await approved_run(
        fixture,
        build_whm_firewall_release_runner(context=fixture.context),
        tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    )


async def allowlist_remove_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The allowlist-remove tool, on a box holding an allow entry that the removal then clears."""
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_ALLOW_LINE), csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_WHITE), imunify_answer(IMUNIFY_CLEAN)),
        ),
    )
    await allowlist_remove(fixture)
    return await approved_run(
        fixture,
        build_whm_firewall_allowlist_remove_runner(context=fixture.context),
        tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    )


async def nic_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The VM NIC tool. The fake VM holds its own config, so the runner reads what the gate saw."""
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context()
    await call_nic(fixture)
    return await approved_run(
        fixture,
        build_proxmox_vm_nic_runner(context=fixture.context),
        tool=TOOL_PROXMOX_VM_NIC,
    )


async def reset_password_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The password-reset tool, the one whose changed value may not be rendered at all."""
    no_password_polling_delay(monkeypatch)
    fixture, _ = reset_context()
    await reset(fixture)
    return await approved_run(
        fixture,
        build_proxmox_reset_vm_password_runner(context=fixture.context),
        tool=TOOL_PROXMOX_RESET_VM_PASSWORD,
    )


async def whitelist_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The whitelist tool. The fake gateway holds `mynetworks`, so the add the gate proposed is the
    one run.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    await call_whitelist(fixture)
    return await approved_run(
        fixture,
        build_pmg_whitelist_runner(context=fixture.context),
        tool=TOOL_PMG_WHITELIST,
    )


def account_context(*, listings: list[list[dict[str, Any]]], path: str) -> ToolFixture:
    """A WHM endpoint answering `listaccts` twice — once for the gate, once for the postflight.

    Two bodies rather than one, because a CHANGE workflow reads that endpoint on both sides of the
    change and a single answer would let the postflight pass against a read that never happened.
    """
    api = FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: [listaccts_body(rows) for rows in listings],
            path: [whm_api_success_body()],
        },
    )
    cipher = build_tool_context().cipher
    row = whm_server("alpha")
    row.api_token = cipher.encrypt_text("whm-api-token-plaintext")
    return build_tool_context(servers=[row], cipher=cipher, whm_transport=api.transport)


def account_row(*, suspended: int) -> dict[str, Any]:
    """One `listaccts` row for the account both lanes act on."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=suspended, owner=OWNER)


async def suspend_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The suspend tool: live when the operator was asked, suspended when the runner checked."""
    fixture = account_context(
        listings=[[account_row(suspended=0)], [account_row(suspended=1)]],
        path=SUSPENDACCT_PATH,
    )
    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        await whm_suspend_account(server_ref="alpha", username=ACCOUNT, context=fixture.context)
    return await approved_run(
        fixture,
        build_whm_suspend_runner(context=fixture.context),
        tool=TOOL_WHM_SUSPEND_ACCOUNT,
    )


async def unsuspend_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The unsuspend tool, the mirror: suspended at gate time and live afterwards."""
    fixture = account_context(
        listings=[[account_row(suspended=1)], [account_row(suspended=0)]],
        path=UNSUSPENDACCT_PATH,
    )
    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        await whm_unsuspend_account(server_ref="alpha", username=ACCOUNT, context=fixture.context)
    return await approved_run(
        fixture,
        build_whm_unsuspend_runner(context=fixture.context),
        tool=TOOL_WHM_UNSUSPEND_ACCOUNT,
    )


HalvesBuilder = Callable[[pytest.MonkeyPatch], Awaitable[Halves]]

# Every CHANGE tool, its lane, and the keys its two halves were measured to share. The key sets
# are pinned rather than merely checked for the property below, so a tool that grows an overlap
# fails here and its author has to look at whether the halves are still undiffable — which is the
# claim four modules and `docs/change-delta.md` make about this table.
CASES: dict[str, tuple[HalvesBuilder, set[str]]] = {
    TOOL_WHM_SUSPEND_ACCOUNT: (suspend_halves, {"server"}),
    TOOL_WHM_UNSUSPEND_ACCOUNT: (unsuspend_halves, {"server"}),
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW: (
        release_halves,
        {"server", "target", "duration_minutes"},
    ),
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE: (allowlist_remove_halves, {"server", "target"}),
    TOOL_PROXMOX_RESET_VM_PASSWORD: (
        reset_password_halves,
        {"server", "node", "vmid", "username"},
    ),
    TOOL_PROXMOX_VM_NIC: (nic_halves, {"server", "node", "vmid", "net", "action"}),
    TOOL_PMG_WHITELIST: (
        whitelist_halves,
        {"server", "action", "target", "normalized_target"},
    ),
}


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
