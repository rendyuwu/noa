"""`pmg_whitelist`'s runner — the half that edits `mynetworks`.

Reachable only after an operator approved (the far side of the cookie/CSRF boundary), so nothing
here goes through the tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`,
and that is what these tests build.

**Four properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **The runner re-reads before it decides.** PMG has no compare-and-set token, so the
   CAS-token-rule's first half has no instance — but its second does: what the approval window is
   checked against is the *fact* the operator approved, re-measured now. An address whitelisted by
   somebody else while the card sat pending is a `no_op`, not a second add and not a failure.
2. **A removal takes every matching line, by PMG's own spelling** — the pmg-whitelist tool's
   departure from `noa-old`, which deleted the normalised form once. Asserted on the **bytes
   written**: a runner sending `/config/mynetworks/203.0.113.10/32` for a line PMG printed as
   `203.0.113.10` answers identically and removes nothing, so the return value cannot catch it.
3. **The postflight asks the change's own question**. It re-reads the list, not the
   `200 OK` the mutation printed. `ignore_writes` is exactly that case: `pmgsh` says yes and the
   whitelist says no.
4. **Unavailable is not refuted** (one system over, and a non-answer never becomes a verdict). A
   postflight read that could not answer is `changed` + `verified: false` +
   `verification: unavailable`, never a bare `false` an operator reads as a measurement. A failed
   `pmgconfig sync` is its own third thing: the config moved and mail flow did not.

The fifth thread is **no reason value reaching this path**, which is asserted rather than assumed: a
`mynetworks` entry is a CIDR and nothing else, so a sentinel reason driven through the approval
must not appear in the serialized payload, the derived summary, the built receipt — or in any
command.

The seams: the real `pmgsh` command composition, the real `mynetworks` parser, a real
`SecretCipher` decrypting real ciphertext on the row, the real resolver. Only the SSH socket is
doubled, and the node behind it holds its whitelist as state.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from core.approvals.execution import build_receipt
from core.audit.summaries import result_summary, status_for_payload
from core.db.lifecycle import ToolRunStatus
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH
from noa_api.mcp_tools.change_target import (
    ERROR_EVIDENCE_UNUSABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
)
from noa_api.mcp_tools.pmg_whitelist import (
    ACTION_ADD,
    ACTION_REMOVE,
    ERROR_SERVER_UNAVAILABLE,
)
from noa_api.mcp_tools.pmg_whitelist_runner import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SYNC_FAILED,
    build_pmg_whitelist_runner,
)
from support.change_delta import PayloadRunner, payload_runner
from support.pmg import (
    BYSTANDER,
    SERVER_NAME,
    TARGET,
    TARGET_NORMALIZED,
    FakePMGWhitelist,
    command_step,
    execution_request,
    payload_text,
    pmg_argv,
    whitelist_change_context,
)
from support.remote_exec import SUDO_DENIED_STDERR, command_result

# What the gate would have put on the evidence for a removal of the bare-host line.
BARE_HOST_MATCH = [{"cidr": TARGET, "normalized": TARGET_NORMALIZED}]

# Both spellings of one address, which `mynetworks` really can hold at once.
BOTH_SPELLINGS = [
    {"cidr": TARGET, "normalized": TARGET_NORMALIZED},
    {"cidr": TARGET_NORMALIZED, "normalized": TARGET_NORMALIZED},
]


def build_runner(fixture: Any) -> PayloadRunner:
    """The runner over this fixture's context, answering its envelope.

    The delta it publishes beside that envelope is asserted in
    `test_change_delta.py`; every claim in this file is about the envelope.
    """
    return payload_runner(build_pmg_whitelist_runner(context=fixture.context))


def server_id(fixture: Any):  # type: ignore[no-untyped-def]
    return fixture.pmg_servers.servers[0].id


def delete_path(cidr: str) -> str:
    """The path `pmgsh delete` is given for one entry, as one argv token."""
    return f"{MYNETWORKS_PATH}/{cidr}"


# --- The happy path, both directions ---


async def test_an_approved_add_writes_the_entry_syncs_and_confirms_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole flow, with the node's own list deciding the verdict.

    The fake keeps what was created and the postflight reads it back through the production
    parser, so `verified: true` here means the address really is on the list — not that a canned
    fixture agreed with itself.
    """
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER]))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    assert payload["exists"] is True
    # The card's heading, written here rather than derived from the tool name: `Pmg Whitelist`
    # names the machinery, and this names what happened to the address.
    assert payload["headline"] == f"Email relay allowed — {TARGET}"
    # Both spellings, because an operator can grep the box for either — the typed address in the
    # first sentence, the line PMG now holds in the second.
    assert payload["message"] == (
        f"{TARGET} may now relay email through {SERVER_NAME}. Added as {TARGET_NORMALIZED}."
    )
    assert box.entries == [BYSTANDER, TARGET_NORMALIZED]


async def test_an_approved_removal_deletes_the_entry_syncs_and_confirms_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, and the bystander is what says the removal was surgical."""
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET])
    )

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BARE_HOST_MATCH
        )
    )

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    assert payload["exists"] is False
    assert payload["removed"] == [TARGET]
    assert payload["headline"] == f"Email relay stopped — {TARGET}"
    # The second sentence names the lines the removal actually took, in PMG's own spelling, for
    # the reason `removed` above carries them: two spellings of one address are two lines.
    assert payload["message"] == (
        f"{TARGET} may no longer relay email through {SERVER_NAME}. Removed {TARGET}."
    )
    assert box.entries == [BYSTANDER]


# --- Add and remove: the commands, in order, and the sync that applies them ---


async def test_an_add_reads_creates_syncs_then_reads_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pmgsh create` then `pmgconfig sync --restart 1`, and the order is the property.

    A mutation that skipped the sync looks applied and is not: `pmgsh` writes PMG's config and
    Postfix does not pick it up until the sync runs. The trailing read is the postflight,
    and asserting on the sequence is what separates "it synced" from "it synced *after* writing".
    """
    fixture, box = whitelist_change_context(monkeypatch)

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert box.created == [TARGET_NORMALIZED]
    assert box.synced == 1
    assert [command_step(command) for command in box.commands] == ["ls", "create", "sync", "ls"]


async def test_a_removal_reads_deletes_syncs_then_reads_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same shape one direction over."""
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET])
    )

    await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BARE_HOST_MATCH
        )
    )

    assert box.deleted_paths == [delete_path(TARGET)]
    assert box.synced == 1
    assert [command_step(command) for command in box.commands] == ["ls", "delete", "sync", "ls"]


# --- The pmg-whitelist tool's departure from `noa-old`: which spelling a removal names ---


async def test_a_removal_deletes_by_pmgs_own_spelling_and_not_the_normalised_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes, not the verdict (exact membership, and `noa-old`'s defect).

    `mynetworks` here holds `203.0.113.10`; its normalised form is `203.0.113.10/32`. `noa-old`
    sent `pmgsh delete /config/mynetworks/203.0.113.10/32` — a path PMG never printed for a line
    it stores under another name. The removal has to name the line, and only the wire says which
    one was named: a runner sending the normalised form against a fixture that stored the same
    spelling would answer identically.
    """
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[TARGET]))

    await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BARE_HOST_MATCH
        )
    )

    assert box.deleted_paths == [delete_path(TARGET)]
    assert delete_path(TARGET_NORMALIZED) not in box.deleted_paths


async def test_a_removal_of_a_line_stored_as_32_names_that_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the test above: the rule is "PMG's spelling", not "never `/32`".

    Without this case, a runner that always stripped the prefix length would pass — and would then
    fail to delete every line `mynetworks` genuinely stores as a network.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[TARGET_NORMALIZED])
    )

    await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture),
            action=ACTION_REMOVE,
            matches=[{"cidr": TARGET_NORMALIZED, "normalized": TARGET_NORMALIZED}],
        )
    )

    assert box.deleted_paths == [delete_path(TARGET_NORMALIZED)]


async def test_every_spelling_of_one_address_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two lines, one entry (`core.integrations.pmg.mynetworks`), and both have to go.

    `noa-old` deduplicated while parsing and sent one delete, so a whitelist holding both
    spellings kept relaying for the address after a removal reported success. The postflight is
    what turns that into a visible failure now, and taking both lines is what keeps it from
    happening.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[TARGET, BYSTANDER, TARGET_NORMALIZED])
    )

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BOTH_SPELLINGS
        )
    )

    assert box.deleted_paths == [delete_path(TARGET), delete_path(TARGET_NORMALIZED)]
    assert box.entries == [BYSTANDER]
    assert payload["ok"] is True
    assert payload["removed"] == [TARGET, TARGET_NORMALIZED]
    # The sentence names every line too, not just the count — the case where the two spellings
    # are the whole point is the case a card saying "Removed 203.0.113.10." would understate.
    assert payload["message"] == (
        f"{TARGET} may no longer relay email through {SERVER_NAME}. "
        f"Removed {TARGET}, {TARGET_NORMALIZED}."
    )


async def test_an_add_writes_the_masked_network_and_not_the_typed_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact membership on the side that writes: `203.0.113.10/24` is a request about
    `203.0.113.0/24`.

    The card showed the operator the normalised form, so that is what goes in the file. Writing
    the typed spelling back would put a line in `mynetworks` that NOA's own reader then normalises
    to something else — and the entry the operator approved would not be the entry PMG holds.
    """
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[]))

    await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture),
            target="203.0.113.10/24",
            normalized_target="203.0.113.0/24",
        )
    )

    assert box.created == ["203.0.113.0/24"]


# --- The CAS-token rule's fact-check half: the approval window is checked on the fact ---


async def test_an_address_whitelisted_while_the_card_was_pending_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody reached it first, and the world is as the operator wanted it.

    Not a failure: reporting one would send an operator to fix something that is not broken. Not a
    second `create` either — the assertion is on the absence of a mutation, because an `add` of a
    line already there is the change nobody authorised twice.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET])
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_NO_OP
    assert payload["verified"] is True
    assert payload["headline"] == f"Already on the list — {TARGET}"
    # The last clause is the whole of what membership means and deliberately nothing more.
    assert payload["message"].endswith(f"It may relay email through {SERVER_NAME}.")
    assert box.mutations == []
    assert box.synced == 0


async def test_an_entry_removed_while_the_card_was_pending_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same fact, one direction over: nothing left to delete."""
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER]))

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BARE_HOST_MATCH
        )
    )

    assert payload["status"] == STATUS_NO_OP
    assert payload["headline"] == f"Already off the list — {TARGET}"
    assert payload["message"].endswith(f"It may not relay email through {SERVER_NAME}.")
    assert box.mutations == []


async def test_a_whitelist_still_in_the_approved_state_is_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for both no-ops: the ordinary case still writes.

    Without it, a runner that answered `no_op` unconditionally would pass the two tests above.
    """
    fixture, box = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER]))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["status"] == STATUS_CHANGED
    assert box.mutations != []


async def test_a_read_that_cannot_answer_refuses_rather_than_deciding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One source, so silence is not a state. A change decided against a read that did not
    answer is a change decided against nothing — and here it would be an unauthorised write."""
    fixture, box = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            list_error=command_result(exit_code=1, stderr="pmgsh: connection refused")
        ),
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "pmgsh_command_failed"
    # Nothing was written, and the heading says that rather than naming a change that did not
    # happen. PMG's own words stay whole in the sentence: they are the only thing here naming a
    # remedy, and a `mynetworks` line has no comment for `pmgsh` to quote back.
    assert payload["headline"] == f"Change did not run — {TARGET}"
    assert payload["message"] == "pmgsh: connection refused"
    assert box.mutations == []


async def test_a_denied_sudo_keeps_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two causes, two remedies — a sudoers entry is not a broken `pmgsh` install.

    Collapsing them sends an operator hunting a problem that is not there (`noa-old` GH #82), and
    the code is what an approved change's receipt will carry.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(list_error=command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)),
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "ssh_sudo_required"


# --- Context persisted at gate time: the evidence, never the arguments ---


async def test_the_target_comes_from_the_evidence_and_not_from_the_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`server_ref` is a string a model supplied and inventory can be edited in the window.

    The request built here names `some-other-node` in its arguments and the real row in its
    evidence, so a runner resolving from the arguments would find nothing and this would fail on
    the answer rather than on a subtle mis-selection.
    """
    fixture, box = whitelist_change_context(monkeypatch)
    request = execution_request(server_id=server_id(fixture))

    payload = await build_runner(fixture)(request)

    assert request.arguments["server_ref"] == "some-other-node"
    assert payload["ok"] is True
    assert payload["server"] == SERVER_NAME
    assert box.created == [TARGET_NORMALIZED]


@pytest.mark.parametrize(
    "evidence_override",
    [
        pytest.param({"action": "purge"}, id="an-action-outside-the-enum"),
        pytest.param({"action": None}, id="no-action-at-all"),
        pytest.param({"target": "   "}, id="a-blank-target"),
        pytest.param({"normalized_target": "203.0.113.10"}, id="an-unnormalised-normalised-form"),
        pytest.param({"normalized_target": "not-an-address"}, id="a-target-that-stopped-parsing"),
        pytest.param({"normalized_target": 42}, id="a-target-that-changed-type-in-jsonb"),
    ],
)
async def test_evidence_that_did_not_survive_its_round_trip_is_declined(
    monkeypatch: pytest.MonkeyPatch,
    evidence_override: dict[str, Any],
) -> None:
    """The third place the enum is bounded, and the CIDR with it (the release-and-allow tool's
    three-place discipline).

    By the time a runner reads this, an operator has typed a reason and pressed Approve, so a
    value NOA cannot act on is declined rather than guessed at. The normalised target is re-parsed
    because it becomes an argv token in a `pmgsh` command — `203.0.113.10` is refused not because
    it is unparseable but because it is not what the gate wrote, and a value the gate did not write
    is one nothing on this path vouched for.
    """
    fixture, box = whitelist_change_context(monkeypatch)
    request = execution_request(server_id=server_id(fixture))
    request.evidence.update(evidence_override)

    payload = await build_runner(fixture)(request)

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert box.commands == []


async def test_a_server_row_deleted_after_approval_names_the_pmg_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its own code, not WHM's: an administrator sent to the wrong table is sent nowhere."""
    fixture, box = whitelist_change_context(monkeypatch)

    payload = await build_runner(fixture)(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert "PMG" in payload["message"]
    assert box.commands == []


# --- The postflight ---


async def test_a_mutation_that_answered_200_ok_while_the_list_did_not_move_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict is read off `mynetworks`, not off what `pmgsh` printed.

    `pmgsh create` reports the HTTP status of the underlying API call, and NOA already tolerates a
    non-zero exit when stdout carries `200 OK` (`require_pmg_mutation_success`). That makes the
    command's own answer exactly the wrong thing to verify against: here it says yes and the list
    says no.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER], ignore_writes=True)
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    assert payload["verified"] is False
    assert payload["exists"] is False
    assert payload["headline"] == f"List unchanged — {TARGET}"
    # The reading NOA took, not a verdict word: the corner on the card states the verdict off
    # the delta, and the sentence states what was seen.
    assert payload["message"] == f"NOA read the list back on {SERVER_NAME}: {TARGET} is not on it."
    assert box.created == [TARGET_NORMALIZED]


async def test_a_removal_the_list_still_shows_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same measurement one direction over, and the case a partial removal lands in."""
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, TARGET], ignore_writes=True)
    )

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BARE_HOST_MATCH
        )
    )

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    assert payload["exists"] is True
    assert payload["headline"] == f"List unchanged — {TARGET}"
    assert payload["message"].endswith(f"{TARGET} is still on it.")


async def test_a_postflight_that_cannot_be_read_is_unavailable_and_not_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The crypt-verify rule one system over: verification-unavailable is not verified, and not
    refuted.

    The write and the sync were both accepted, so a bare `verified: false` would read as a
    measurement and send an operator to repeat a change that has probably already happened. The
    cause travels with it, because "could not read" and "was refused" have different remedies.
    """
    fixture, box = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER], fail_reads_after_write=True)
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == "pmgsh_command_failed"
    # The heading is the confirmed one, because the commands were accepted — what is unconfirmed
    # is stated in the sentence, and the corner reads it off the verification state.
    assert payload["headline"] == f"Email relay allowed — {TARGET}"
    assert payload["message"] == (
        f"PMG accepted the change on {SERVER_NAME}. NOA could not read the list back afterwards, "
        f"so it cannot say {TARGET} may relay email."
    )
    assert box.created == [TARGET_NORMALIZED]


async def test_a_readable_postflight_that_agrees_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the two above: without it, "unavailable" passes unconditionally."""
    fixture, _ = whitelist_change_context(monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER]))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["verified"] is True
    assert "verification" not in payload


# --- The write, the sync, and the difference between them ---


async def test_a_refused_write_reports_pmgs_own_code_and_never_syncs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing moved, so nothing is applied — and the sync is what would have applied it."""
    fixture, box = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            entries=[BYSTANDER],
            create_error=command_result(exit_code=1, stderr="pmgsh: parameter verification failed"),
        ),
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "pmgsh_command_failed"
    assert payload["applied"] is False
    assert box.synced == 0


async def test_a_write_that_was_not_applied_is_reported_as_unapplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The split neither `ok` nor a bare failure can say: the config moved, mail flow did not.

    `ok: true, status: changed` would be a lie — Postfix has not picked the entry up. A bare
    failure would send the operator back to add an entry that is already in the config, straight
    into a `no_op`. So it is its own code, and the entry really is there.
    """
    fixture, box = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            entries=[BYSTANDER],
            sync_error=command_result(exit_code=1, stderr="pmgconfig: restart failed"),
        ),
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SYNC_FAILED
    assert payload["applied"] is False
    assert payload["headline"] == f"Saved, not live — {TARGET}"
    # `pmgconfig`'s own words are kept off the operator's sentence and kept on the payload, where
    # the admin drawer reads them and where they correlate to this run. Both halves asserted: a
    # strip that dropped them entirely would pass an assertion that only checked the sentence.
    assert payload["sync_error"] == "pmgconfig: restart failed"
    assert "pmgconfig" not in payload["message"]
    # And it survives the audit path it was put there for: the whole payload becomes
    # `result_summary`, which the admin tool-run drawer renders. Asserted rather than argued —
    # a key an administrator cannot reach would be the same loss with an extra step.
    assert "pmgconfig: restart failed" in str(result_summary(payload))
    # Both halves and the consequence, in one sentence: the config moved, the step that applies
    # it did not, and what that means for the address.
    assert payload["message"] == (
        f"{TARGET} was added to the list on {SERVER_NAME} as {TARGET_NORMALIZED}, and the step "
        "that puts it into effect did not run. It cannot relay email yet."
    )
    assert box.entries == [BYSTANDER, TARGET_NORMALIZED]
    assert status_for_payload(payload) is ToolRunStatus.FAILED


async def test_a_failed_sync_on_a_removal_names_every_line_it_took(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other arm of the sentence above, and the one that can name a line that never existed.

    An add writes exactly one line and it is the normalised form, so naming that form names what
    moved. A removal is not symmetric: it takes out every spelling of the address the file holds,
    and the normalised form may not be among them. A sentence that recomputed it would print a
    line the operator will not find in the file, beside a delta whose `removed` key lists the two
    that really went — one measurement, two surfaces, disagreeing.
    """
    fixture, box = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            entries=[TARGET, BYSTANDER, TARGET_NORMALIZED],
            sync_error=command_result(exit_code=1, stderr="pmgconfig: restart failed"),
        ),
    )

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BOTH_SPELLINGS
        )
    )

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SYNC_FAILED
    assert payload["applied"] is False
    assert payload["headline"] == f"Saved, not live — {TARGET}"
    # Both lines in the sentence, and both really gone from the file — the card names what the
    # box now holds rather than a form recomputed from what was asked for.
    assert payload["message"] == (
        f"{TARGET} was removed from the list on {SERVER_NAME} as {TARGET}, "
        f"{TARGET_NORMALIZED}, and the step that puts it into effect did not run. It can still "
        "relay email."
    )
    assert box.deleted_paths == [delete_path(TARGET), delete_path(TARGET_NORMALIZED)]
    assert box.entries == [BYSTANDER]


async def test_a_failed_sync_and_a_failed_postflight_do_not_say_the_same_thing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two failures of one add, and the sentences have to hold them apart.

    Both answer `ok: False` for the same requested change, and that is exactly why the wording
    cannot collapse to "failed" on either. The config row moved on the first and not on the
    second: an operator told only "failed" after a failed sync goes and re-adds a line that is
    already in the file, straight into a no-op, which is the reason these verification states are
    four rather than two.

    Asserted as a pair rather than one at a time, because a runner wording both branches the same
    way passes either assertion alone. The failed sync names the line PMG now holds and says the
    applying step did not run; the failed postflight says the list does not hold it.
    """
    # One gateway at a time: the fixture patches the SSH seam, so building both before running
    # either would point both runners at whichever box was patched last.
    sync_fixture, _ = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            entries=[BYSTANDER],
            sync_error=command_result(exit_code=1, stderr="pmgconfig: restart failed"),
        ),
    )
    sync_failed = await build_runner(sync_fixture)(
        execution_request(server_id=server_id(sync_fixture))
    )

    postflight_fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER], ignore_writes=True)
    )
    postflight_failed = await build_runner(postflight_fixture)(
        execution_request(server_id=server_id(postflight_fixture))
    )

    assert sync_failed["error_code"] == ERROR_SYNC_FAILED
    assert postflight_failed["error_code"] == ERROR_POSTFLIGHT_FAILED
    # The entry is in the config on one and not on the other, and each sentence says so.
    assert TARGET_NORMALIZED in sync_failed["message"]
    assert "did not run" in sync_failed["message"]
    assert "not on it" in postflight_failed["message"]
    assert "did not run" not in postflight_failed["message"]
    assert sync_failed["message"] != postflight_failed["message"]
    assert sync_failed["headline"] != postflight_failed["headline"]


async def test_a_refused_delete_stops_and_leaves_the_remaining_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `delete` that is refused stops the removal, and nothing is applied.

    Pressing on through the remaining lines would report a partial removal as a whole one. The
    honest answer is the refusal, a whitelist that still holds what was not taken, and no
    `pmgconfig sync` — because there is nothing to put in force.
    """
    fixture, box = whitelist_change_context(
        monkeypatch,
        box=FakePMGWhitelist(
            entries=[TARGET, TARGET_NORMALIZED],
            delete_error=command_result(exit_code=1, stderr="pmgsh: delete failed"),
        ),
    )

    payload = await build_runner(fixture)(
        execution_request(
            server_id=server_id(fixture), action=ACTION_REMOVE, matches=BOTH_SPELLINGS
        )
    )

    assert payload["ok"] is False
    assert payload["applied"] is False
    assert payload["headline"] == f"Change failed — {TARGET}"
    # The reading is what turns a refusal into a measurement, and it says what membership means
    # rather than restating the verdict.
    assert f"{TARGET} may relay email through {SERVER_NAME}" in payload["message"]
    assert box.entries == [TARGET, TARGET_NORMALIZED]
    assert box.synced == 0


# --- No reason value has an instance here, and that is asserted ---


async def test_the_operator_reason_reaches_neither_a_command_nor_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `mynetworks` entry is a CIDR, so nothing kept from the LLM leaves NOA here.

    Asserted on the serialized payload, the derived summary and the built receipt rather than on a
    key set — a value dropped in one place and kept in another passes a key compare — and
    on every command, because the only thing that can carry it out is a write.
    """
    sentinel = "customer-said-the-relay-is-theirs"
    fixture, box = whitelist_change_context(monkeypatch)
    request = execution_request(server_id=server_id(fixture), reason=sentinel)

    payload = await build_runner(fixture)(request)

    assert request.reason == sentinel
    assert sentinel not in payload_text(payload)
    assert sentinel not in result_summary(payload)
    receipt = build_receipt(evidence=request.evidence, payload=payload)
    assert sentinel not in payload_text(receipt)
    assert sentinel not in " ".join(box.commands)


async def test_the_sentinel_would_have_been_found_if_it_had_leaked() -> None:
    """The negative control for the assertion above: `payload_text` really does look.

    Without it, "the reason is not in the payload" passes against a helper that serializes
    nothing, which is exactly the compare that stops separating.
    """
    sentinel = "customer-said-the-relay-is-theirs"

    assert sentinel in payload_text({"ok": True, "message": sentinel})
    assert sentinel in result_summary({"ok": True, "message": sentinel})


async def test_the_only_thing_written_is_the_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    """No comment, no note, no description — the flag list is the whole of what goes onto PMG."""
    fixture, box = whitelist_change_context(monkeypatch)

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    [create] = [command for command in box.commands if command_step(command) == "create"]
    assert pmg_argv(create)[1:] == ["create", MYNETWORKS_PATH, "-cidr", TARGET_NORMALIZED]


# --- What the audit row and a model are told ---


async def test_the_payload_carries_both_spellings_and_not_the_rest_of_the_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This becomes `tool_runs.result_summary`, and `noa_get_action_result` hands it to a model.

    Both spellings, because a model told only that `203.0.113.10/24` was whitelisted would report
    a host where a network changed. Not the other entries: what a model needs is which
    address on which node moved which way, not a mail gateway's whole allow list.
    """
    fixture, _ = whitelist_change_context(
        monkeypatch, box=FakePMGWhitelist(entries=[BYSTANDER, "192.0.2.0/24"])
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["server"] == SERVER_NAME
    assert payload["action"] == ACTION_ADD
    assert payload["target"] == TARGET
    assert payload["normalized_target"] == TARGET_NORMALIZED
    assert BYSTANDER not in payload_text(payload)
    assert "192.0.2.0/24" not in payload_text(payload)


async def test_a_successful_run_classifies_as_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The envelope this runner answers is the one `core.audit.summaries` reads."""
    fixture, _ = whitelist_change_context(monkeypatch)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is ToolRunStatus.COMPLETED


# --- Internals of this file ---


def _argv(command: str) -> list[str]:
    """The composed command as argv, through the fixture's own reader."""
    from support.pmg import _pmg_argv

    return _pmg_argv(command)


def _step(command: str) -> str:
    """`ls`, `create`, `delete` or `sync` — which step of the flow a command is."""
    argv = _argv(command)
    if argv[:1] == ["pmgconfig"]:
        return "sync"
    return argv[1] if len(argv) > 1 else ""
