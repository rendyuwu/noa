"""What each CHANGE runner publishes as its before→after delta (T38 — V85, V86).

One lane per tool, including the failure branches, because the failure branches are where the
shape earns its keep: an executor refusal and a runner failure are both `ok: False`, so the only
thing that separates them is whether a delta was stated at all.

The record's own rules, the receipt key and the executor's seam are `test_change_delta.py`. How
large these payloads are — the `result_summary` pin, and what folding a delta into one would cost
— is `test_change_receipt_halves.py`, which measures all seven rather than pinning one of them
here and asserting the ordering there. The two WHM account runners are asserted in
`test_whm_tools_suspend_account.py` and `test_whm_tools_unsuspend_account.py`, where their WHM
endpoint fixtures live — moving them here would mean a second copy of that wiring (V66).

Every branch that measured nothing is asserted on a facet **being absent**, and every branch that
measured "nothing moved" on the same facet being **present and empty**. Those are two different
claims about one change, they render identically to a reader who cannot tell them apart, and only
one of them is a measurement.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.approvals.delta import (
    VERIFICATION_MISMATCH,
    VERIFICATION_NOT_IN_FORCE,
    VERIFICATION_UNAVAILABLE,
    VERIFICATION_VERIFIED,
)
from core.approvals.execution import RECEIPT_DELTA_KEY, build_receipt
from noa_api.mcp_tools.pmg_whitelist_runner import build_pmg_whitelist_runner
from noa_api.mcp_tools.proxmox_nic import EVIDENCE_NIC
from noa_api.mcp_tools.proxmox_nic_runner import build_proxmox_vm_nic_runner
from noa_api.mcp_tools.proxmox_password_runner import (
    build_proxmox_reset_vm_password_runner,
)
from noa_api.mcp_tools.whm_firewall_allowlist import (
    build_whm_firewall_allowlist_remove_runner,
)
from noa_api.mcp_tools.whm_firewall_change import build_whm_firewall_release_runner
from noa_api.mcp_tools.whm_firewall_change_common import EVIDENCE_FIREWALL
from support.change_delta import delta_of, outcome_of
from support.pmg import (
    BYSTANDER,
    TARGET_NORMALIZED,
    FakePMGWhitelist,
    whitelist_change_context,
)
from support.pmg import execution_request as whitelist_request
from support.proxmox_nic import (
    NET0,
    NET0_UP,
    FakeProxmoxNICVM,
    nic_context,
    no_polling_delay,
)
from support.proxmox_nic import execution_request as nic_request
from support.proxmox_password import FakeProxmoxVM, reset_context
from support.proxmox_password import execution_request as reset_request
from support.proxmox_password import no_polling_delay as no_password_polling_delay
from support.remote_exec import SUDO_DENIED_STDERR, command_result
from support.servers import YOPASS_URL
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    CSF_TEMP_ALLOW,
    IMUNIFY_CLEAN,
    SERVER_NAME,
    TARGET,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    imunify_answer,
    imunify_backend,
)
from support.whm_firewall_change import (
    DURATION_MINUTES,
    execution_request,
    release_context,
    released_box,
    removal_request,
    removed_box,
)


def firewall_evidence(request: Any, **keys: Any) -> None:
    """Overwrite the firewall half of an approved request's evidence, in place.

    `support.whm_firewall_change`'s request builders write a minimal before-state, which is the
    right default: most branches do not read it, and one that carried every key would hide a
    runner reading a key the gate does not always write. A test about a key passes it here.
    """
    request.evidence[EVIDENCE_FIREWALL] = {
        **request.evidence[EVIDENCE_FIREWALL],
        **keys,
    }


# --------------------------------------------------------------------------------------
# `whm_firewall_release_and_allow` (T25)
# --------------------------------------------------------------------------------------


async def test_the_release_delta_names_the_verdict_it_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One field, both sides measured: the `old` off the evidence, the `new` off the postflight.

    The `old` side is the reading the operator authorised against and not a second reading taken
    later (V33) — re-deriving it here would be a delta about a decision nobody made.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["identity"] == {"server": SERVER_NAME, "target": TARGET}
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == [
        {"field": "firewall_verdict", "old": "blocked", "new": "allowlisted"}
    ]
    assert payload["new_values"] == {
        "expires_at": payload["new_values"]["expires_at"],
        "duration_minutes": DURATION_MINUTES,
    }
    assert payload["unanswered"] == []


async def test_the_release_delta_reports_every_backend_it_drove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Driven and answered are two facts, joined by name and kept apart in the row (V86).

    A backend that ran the commands and then went silent on the confirming read is the case the
    pair exists for, and one boolean could not say it.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    assert delta.as_payload()["backends"] == [
        {"name": "csf", "driven": True, "answered": True, "verdict": "allowlisted"},
        {"name": "imunify", "driven": True, "answered": True, "verdict": "whitelisted"},
    ]


async def test_a_release_that_did_not_take_publishes_a_measured_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner-failure path, and the `false`s beside it are *earned* (V86).

    The address is still blocked after the release ran. Every backend was driven and every one
    answered the confirming read, so the verdict is a measurement and the delta says so:
    `mismatch`, with an empty diff meaning the reading did not move. That empty diff is what the
    payload's `released: false, allowlisted: false` is, stated as a comparison rather than as two
    booleans.

    `new_values` is still here, and that is the facet meaning what it says: the window is what
    the backends were *asked* for and accepted, which they did — CSF resolves a conflict
    block-first, so an allow entry written under a surviving deny entry exists and is overridden
    rather than refused. Whether it takes effect is the verdict's job, and the verdict is on the
    row above saying it did not.
    """
    fixture, _ = release_context(monkeypatch, box=released_box(csf_after=CSF_DENY_LINE))
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_MISMATCH
    assert payload["changed_fields"] == []
    assert "verification_cause" not in payload
    assert payload["new_values"]["duration_minutes"] == DURATION_MINUTES
    assert all(entry["answered"] for entry in payload["backends"])


async def test_an_allow_that_did_not_take_renders_the_half_that_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other failure, and it is a *different* delta rather than the same one twice.

    Nothing blocks the address and nothing allows it: the release took and the allow did not. The
    verdict moved off `blocked` and stopped short of `allowlisted`, which is exactly the payload's
    `released: true, allowlisted: false` — and it is one field change, not an empty diff.

    **And no resolved expiry rides beside it.** Every backend accepted the allow command, so a
    facet keyed off the commands alone publishes an absolute `expires_at` here — a precise window
    for an entry the same delta's verdict says does not exist. `not_found` is the one reading that
    refutes the entry; `blocked` above is not, and keeps its expiry (V86).
    """
    fixture, _ = release_context(
        monkeypatch, box=released_box(csf_after=CSF_CLEAN_OUTPUT, imunify_after=IMUNIFY_CLEAN)
    )
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_MISMATCH
    assert payload["changed_fields"] == [
        {"field": "firewall_verdict", "old": "blocked", "new": "not_found"}
    ]
    assert "new_values" not in payload


async def test_a_backend_that_could_not_be_driven_states_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No verdict was consulted on this branch, so none is claimed (V86).

    What *was* measured rides in the backend's own row — it was not driven, and the code that
    names the remedy is beside it. `changed_fields` is absent rather than empty: NOA did not
    compare, and an empty diff here would read as "we checked and nothing moved".
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_DENY_LINE),
                mutations={CSF_TEMP_ALLOW: command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)},
            ),
        ),
        ssh_username="operator",
    )
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == "ssh_sudo_required"
    assert "changed_fields" not in payload
    assert "new_values" not in payload
    assert payload["backends"] == [
        {
            "name": "csf",
            "driven": False,
            "answered": True,
            "verdict": "blocked",
            "error_code": "ssh_sudo_required",
        }
    ]


async def test_a_silent_backend_is_named_in_the_delta_and_not_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V86's own sentence: the source that could not answer gets named beside the verdict.

    Names and not a count, because "one backend was silent" does not say which server to look
    at. No cause rides with it either — which source said nothing *is* the cause, and a code
    beside it would be a second answer to the same question.

    **The rows are asserted here and nowhere else**, because this is the only lane in the suite
    where one of them is `answered: false`. `backend_outcomes` hardcoding `answered=True` would
    otherwise leave the whole delta lane green while claiming every backend confirmed every
    change — the per-backend half of V86, and the pair `driven` and `answered` exist to keep
    apart: csf ran the commands and answered, imunify ran them and then said nothing.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_ALLOW_LINE)),
            imunify=imunify_backend(imunify_answer("not json at all")),
        ),
    )
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["unanswered"] == ["imunify"]
    assert payload["backends"] == [
        {"name": "csf", "driven": True, "answered": True, "verdict": "allowlisted"},
        {"name": "imunify", "driven": True, "answered": False},
    ]
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert "verification_cause" not in payload
    assert "changed_fields" not in payload
    # The allow command took on every driven backend, so the resolved window is a real value.
    assert payload["new_values"]["duration_minutes"] == DURATION_MINUTES


async def test_a_capped_before_state_ships_its_bound_into_the_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V85 one surface over: the delta rests on a capped reading, so it carries the cap.

    Without it, "this address was blocked and is now allowed" reads as a statement about every
    line the firewall holds for the address, when the evidence behind it stopped at twenty.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)
    firewall_evidence(request, total_matches=34, truncated=True)

    delta = await delta_of(runner, request)

    assert delta is not None
    assert delta.as_payload()["bound"] == {"total": 34, "truncated": True}


async def test_an_evidence_row_with_no_bound_states_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control (V87), and V86's rule: a bound nobody recorded is not a bound of
    nothing. A row opened before those keys existed answers an absent facet, not `0`."""
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)

    delta = await delta_of(runner, execution_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    assert "bound" not in delta.as_payload()


async def test_a_verdict_that_did_not_move_renders_a_measured_empty_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sides present and equal: NOA compared, and the reading did not move.

    The reachable case is a re-run to extend a window that is about to close — the address was
    already allowed when the operator was asked and it is allowed now. The verdict genuinely did
    not move and `new_values` is what carries what did, so an empty `changed_fields` is the
    truthful answer rather than a gap.

    Paired with the test below, which is the same runner on the same box with one side of the
    comparison taken away. Without the pair, a builder that emitted `()` for *both* would pass
    whichever of the two was written alone.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)
    firewall_evidence(request, combined_verdict="allowlisted")

    delta = await delta_of(runner, request)

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == []
    assert payload["new_values"]["duration_minutes"] == DURATION_MINUTES


async def test_a_before_state_with_no_usable_verdict_states_no_field_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One side of the comparison is missing, so no comparison is stated (V86).

    `evidence_verdict` answers `None` when the evidence has no usable verdict, and its docstring
    says a caller treats that as "no field change can be stated" rather than substituting a
    benign word. This is that caller, and the facet is **absent** — not the empty tuple, which
    would tell an operator the before-state was read and matched when it was never readable at
    all.

    Unreachable from a row the gate wrote: `firewall_state` always writes the key and
    `combine_firewall_verdict` answers one of four non-empty words. What reaches it is an
    `approval_context` NOA did not write, and the runner validates the target and the server id
    out of this half and nothing else — so the branch is defensive, and it still has to be
    stated correctly, because it is the branch the absent-versus-empty distinction exists for.

    The change itself is unaffected: it took, it verified, and the resolved window is real. Only
    the diff is withheld.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)
    # Replaced rather than merged: what is being arranged is the *absence* of the key.
    request.evidence[EVIDENCE_FIREWALL] = {"matches": [CSF_DENY_LINE]}

    delta = await delta_of(runner, request)

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert "changed_fields" not in payload
    assert payload["new_values"]["duration_minutes"] == DURATION_MINUTES


async def test_the_release_delta_never_carries_the_operators_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C8, V15, V43 on the delta, asserted on the bytes of the whole receipt.

    This runner writes the reason onto the csf allow entry, so it is the one of the seven where
    the words genuinely leave NOA — and the entry it wrote carries them behind NOA's marker,
    which the postflight then reads back. Asserted on the rendered receipt rather than on the
    delta's keys, because what has to be true is that the string is nowhere in the row.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = build_whm_firewall_release_runner(context=fixture.context)
    request = execution_request(server_id=fixture.servers.servers[0].id)

    outcome = await outcome_of(runner, request)
    receipt = build_receipt(
        evidence={"firewall": {"combined_verdict": "blocked"}},
        payload=outcome.payload,
        delta=outcome.delta,
    )

    assert request.reason not in json.dumps(receipt[RECEIPT_DELTA_KEY])


# --------------------------------------------------------------------------------------
# `whm_firewall_allowlist_remove` (T26)
# --------------------------------------------------------------------------------------


async def test_the_removal_delta_states_a_list_move_and_not_a_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V97 is why this facet is `list_delta` and not a field change.

    Both backends resolve a conflict block-first, so an address on a deny list *and* an allow
    list reads `blocked` before the removal and `blocked` after it — a delta computed from the
    combined verdict would render "nothing changed" for a removal that worked, on exactly the
    address that most plainly had an entry to delete. What this change touches is list
    membership, so that is what the delta states.
    """
    fixture, _ = release_context(monkeypatch, box=removed_box())
    runner = build_whm_firewall_allowlist_remove_runner(context=fixture.context)

    delta = await delta_of(runner, removal_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["identity"] == {"server": SERVER_NAME, "target": TARGET}
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["list_delta"] == {"added": [], "removed": [TARGET]}
    assert "changed_fields" not in payload


async def test_a_removal_that_did_not_take_claims_no_list_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured and disagreeing, and still silent about what left the list.

    `holds_allow_entry` is an aggregate over the backends that answered: where it still reads
    true NOA knows an entry survived somewhere and does *not* know whether another backend's
    entry went. `removed: []` beside the per-backend rows would read as "nothing left any list",
    which is more than the aggregate said (V86).
    """
    fixture, _ = release_context(monkeypatch, box=removed_box(csf_after=CSF_ALLOW_LINE))
    runner = build_whm_firewall_allowlist_remove_runner(context=fixture.context)

    delta = await delta_of(runner, removal_request(server_id=fixture.servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_MISMATCH
    assert "list_delta" not in payload
    assert payload["backends"] == [
        {"name": "csf", "driven": True, "answered": True, "verdict": "allowlisted"},
        {"name": "imunify", "driven": True, "answered": True, "verdict": "not_found"},
    ]


# --------------------------------------------------------------------------------------
# `proxmox_vm_nic` (T28)
# --------------------------------------------------------------------------------------


async def test_a_no_op_publishes_an_explicit_nothing_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case a naive before→after diff gets wrong, and gets wrong in the worst direction.

    This branch answers `verified: true` having written nothing at all. Diffed from its payload,
    the identity fields would render as new values and describe a change to an interface nothing
    touched. So the delta is explicit: the comparison happened (`changed_fields` present) and
    nothing moved (empty) — which is a different claim from the absent facet an unmeasured branch
    publishes.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(nets={NET0: f"{NET0_UP},link_down=1"}))
    runner = build_proxmox_vm_nic_runner(context=fixture.context)

    delta = await delta_of(
        runner, nic_request(server_id=fixture.proxmox_servers.servers[0].id, link_state="down")
    )

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == []
    assert payload["identity"]["net"] == NET0


async def test_a_confirmed_flip_renders_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The link state moved, and both sides of the row were measured somewhere real.

    The `old` side is off the evidence — the card the operator read — rather than off the
    runner's own pre-write read, which is a second reading taken later and is deliberately the
    one the write's digest comes from instead.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context()
    runner = build_proxmox_vm_nic_runner(context=fixture.context)

    delta = await delta_of(runner, nic_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == [{"field": "link_state", "old": "up", "new": "down"}]


async def test_evidence_with_no_link_state_states_no_field_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One side of the comparison is missing, so no comparison is stated (V86).

    `_evidence_link_state` answers `None` when the evidence's `nic` half carries no usable value,
    and its docstring says a caller treats that as "no field change can be stated", never as a
    default. This is that caller. The flip took and was confirmed; only the diff is withheld,
    because an empty one would read as a comparison, about an interface that verifiably moved.

    Paired with the control below (V87): a builder answering `()` for both a missing side and two
    matching sides would pass whichever of the two was written alone.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context()
    runner = build_proxmox_vm_nic_runner(context=fixture.context)
    request = nic_request(server_id=fixture.proxmox_servers.servers[0].id)
    # Replaced rather than merged: what is being arranged is the *absence* of the key.
    request.evidence[EVIDENCE_NIC] = {"net": NET0, "bridge": "vmbr0"}

    delta = await delta_of(runner, request)

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert "changed_fields" not in payload


async def test_a_link_state_that_already_matched_renders_a_measured_empty_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both sides present and equal: NOA compared, and the reading did not move.

    The card described an interface already down, the runner's pre-write read found it up, so the
    write went out and the postflight confirms it down again — edited away and back while the
    request sat pending. The `old` side is the card's, not the pre-write read's (V33).
    """
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context()
    runner = build_proxmox_vm_nic_runner(context=fixture.context)

    delta = await delta_of(
        runner, nic_request(server_id=fixture.proxmox_servers.servers[0].id, link_state="down")
    )

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["changed_fields"] == []


async def test_a_task_that_never_finished_claims_no_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxmox accepted the write and its outcome was never read, so nothing is claimed.

    The one branch on this tool where `changed_fields` is absent rather than empty: the link may
    already have moved, and an empty diff would read as a measurement that it had not.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(task_never_finishes=True))
    runner = build_proxmox_vm_nic_runner(context=fixture.context)

    delta = await delta_of(runner, nic_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == "task_timeout"
    assert "changed_fields" not in payload


NIC_REFUSAL = {"ok": False, "error_code": "digest_mismatch"}

# Every branch of `proxmox_vm_nic` publishing an empty diff rather than an absent one, and the
# verification each earns. The no-op is the fifth, asserted above where its identity claim is.
NIC_MEASURED_EMPTY: dict[str, tuple[FakeProxmoxNICVM, str]] = {
    "config_unreadable": (FakeProxmoxNICVM(config_error=NIC_REFUSAL), VERIFICATION_UNAVAILABLE),
    "write_refused": (FakeProxmoxNICVM(write_error=NIC_REFUSAL), VERIFICATION_UNAVAILABLE),
    "task_failed": (FakeProxmoxNICVM(task_exit_status="failed"), VERIFICATION_UNAVAILABLE),
    "postflight_disagrees": (FakeProxmoxNICVM(ignore_write=True), VERIFICATION_MISMATCH),
}


@pytest.mark.parametrize("case", sorted(NIC_MEASURED_EMPTY))
async def test_every_nic_branch_that_knows_nothing_moved_says_so(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the pair above (V87), and the reason the two spellings both exist.

    A refused write is Proxmox saying it did not apply, a terminal non-`OK` task is Proxmox saying
    it one step later, a postflight reading the old line is the interface saying it, and a config
    that could not be read comes *before* any write — nothing moved because nothing was sent. All
    four answer an empty diff where the timeout above answers an absent one.

    Enumerated in one test because the grounds differ — three having taken a reading and one
    knowing no command went out — and `core.approvals.delta` treats them as one spelling on
    purpose.

    What holds each branch is the case beside it, not the completeness of this dict: nothing reads
    this set against the runner's branches, so dropping a case here deletes a test and fails
    nothing. Unlike the tool table in `test_change_receipt_halves.py`, which is bound against
    `build_change_runners`, a branch list is not something a test can read off the code. Stated
    rather than papered over, because a docstring claiming a mechanism it does not have is the
    defect this file's own subject is about (V84c).
    """
    vm, verification = NIC_MEASURED_EMPTY[case]
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context(vm=vm)
    runner = build_proxmox_vm_nic_runner(context=fixture.context)

    delta = await delta_of(runner, nic_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == verification
    assert payload["changed_fields"] == []


# --------------------------------------------------------------------------------------
# `proxmox_reset_vm_password` (T27)
# --------------------------------------------------------------------------------------


async def test_a_confirmed_reset_states_a_credential_and_no_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The facet for a change whose new value must not be rendered at all.

    What moved is a password: the old one NOA never held, the new one it must not record, and a
    crypt hash of either is what stays in the runner's frame rather than reaching a payload a
    model can read. So there is no `changed_fields` row on any branch of this tool, and the delta
    says instead that a credential was delivered and that the change was confirmed.
    """
    no_password_polling_delay(monkeypatch)
    fixture, _ = reset_context()
    runner = build_proxmox_reset_vm_password_runner(context=fixture.context)

    delta = await delta_of(runner, reset_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["delivered_credential"] == YOPASS_URL
    assert "changed_fields" not in payload


async def test_a_reset_proxmox_refused_delivers_no_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The absence is the claim: NOA is saying the VM never got this password (V62).

    Same rule the payload's `yopass_url` follows and read off the same field, so the two cannot
    disagree about whether a live credential exists. Handing an operator a link to a password the
    VM never had is how a "reset" gets relayed to a customer who then cannot log in.
    """
    no_password_polling_delay(monkeypatch)
    fixture, _ = reset_context(
        vm=FakeProxmoxVM(set_error={"ok": False, "error_code": "proxmox_api_error"})
    )
    runner = build_proxmox_reset_vm_password_runner(context=fixture.context)

    delta = await delta_of(runner, reset_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == "proxmox_api_error"
    assert "delivered_credential" not in payload


async def test_a_reset_that_may_be_live_delivers_the_credential_with_its_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the case above (V87), and V62's own decision.

    Proxmox accepted the write and the task never finished, so the password may be on the VM —
    and withholding the only copy of a live credential is a lockout NOA created. Without this
    case, "no link on a failure" would pass as a blanket rule and take the link off the branches
    that need it most.
    """
    no_password_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(task_never_finishes=True))
    runner = build_proxmox_reset_vm_password_runner(context=fixture.context)

    delta = await delta_of(runner, reset_request(server_id=fixture.proxmox_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification_cause"] == "task_timeout"
    assert payload["delivered_credential"] == YOPASS_URL


# --------------------------------------------------------------------------------------
# `pmg_whitelist` (T29)
# --------------------------------------------------------------------------------------


async def test_a_confirmed_add_states_the_line_it_wrote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """List membership again, and both spellings of the address on the identity (V59).

    `total_entries` comes off the evidence, because it is the only place the whole list was
    counted: this runner reads the lines that *match* its target and never the rest, so a total
    taken in the runner would be a total of four entries on a gateway that holds forty.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    runner = build_pmg_whitelist_runner(context=fixture.context)

    delta = await delta_of(runner, whitelist_request(server_id=fixture.pmg_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["list_delta"] == {
        "added": [TARGET_NORMALIZED],
        "removed": [],
        "total_entries": 1,
    }
    assert payload["identity"]["normalized_target"] == TARGET_NORMALIZED


async def test_a_write_that_landed_out_of_force_is_its_own_verification_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V99's outcome, and the fourth verification state exists for it.

    `pmgsh` accepted the line and `pmgconfig sync` did not run: the config moved and Postfix did
    not, which is neither a change nor a refusal. The list move rides **beside** it rather than
    instead of it — the line really is in the config — so dropping it would report a half-done
    change as one that did nothing.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(sync_error=command_result(exit_code=1, stderr="sync refused")),
    )
    runner = build_pmg_whitelist_runner(context=fixture.context)

    delta = await delta_of(runner, whitelist_request(server_id=fixture.pmg_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_NOT_IN_FORCE
    assert payload["verification_cause"] == "pmg_sync_failed"
    assert payload["list_delta"]["added"] == [TARGET_NORMALIZED]


async def test_a_refused_write_states_the_empty_list_move_it_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`applied: false` reaches beside the list move, not instead of it.

    A refused `create` moved nothing, and that is a measurement — the write was attempted and
    PMG said no — so the facet is present and empty rather than absent.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(create_error=command_result(exit_code=1, stderr="create refused")),
    )
    runner = build_pmg_whitelist_runner(context=fixture.context)

    delta = await delta_of(runner, whitelist_request(server_id=fixture.pmg_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["list_delta"] == {"added": [], "removed": [], "total_entries": 1}


async def test_a_list_that_could_not_be_read_states_no_list_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other spelling (V87): the list was never seen, so nothing is said about it.

    This is the branch before any write. `mynetworks` did not answer, so "nothing moved" is not
    a measurement NOA has — it is the absence of one, and the facet is absent to match (V86).
    """
    fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(list_error=command_result(exit_code=1, stderr="ls refused")),
    )
    runner = build_pmg_whitelist_runner(context=fixture.context)

    delta = await delta_of(runner, whitelist_request(server_id=fixture.pmg_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert "list_delta" not in payload


async def test_a_no_op_whitelist_change_publishes_an_empty_list_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The list was read, it already held what was asked for, and nothing was written.

    Present and empty, for `proxmox_vm_nic`'s reason one system over: a delta that simply omitted
    the facet here would be indistinguishable from one whose read never answered.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET_NORMALIZED])
    )
    runner = build_pmg_whitelist_runner(context=fixture.context)

    delta = await delta_of(runner, whitelist_request(server_id=fixture.pmg_servers.servers[0].id))

    assert delta is not None
    payload = delta.as_payload()
    assert payload["verification"] == VERIFICATION_VERIFIED
    assert payload["list_delta"] == {"added": [], "removed": [], "total_entries": 1}
