"""`pmg_whitelist`'s runner — the half that edits `mynetworks` (T29).

Reachable only after an operator approved (§V.22's far side), so nothing here goes through the
tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`, and that is what these
tests build.

**Four properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **The runner re-reads before it decides.** PMG has no compare-and-set token, so §V.98's first
   half has no instance — but its second does: what the approval window is checked against is the
   *fact* the operator approved, re-measured now. An address whitelisted by somebody else while
   the card sat pending is a `no_op`, not a second add and not a failure.
2. **A removal takes every matching line, by PMG's own spelling** — T29's departure from
   `noa-old`, which deleted the normalised form once. Asserted on the **bytes written**: a runner
   sending `/config/mynetworks/203.0.113.10/32` for a line PMG printed as `203.0.113.10` answers
   identically and removes nothing, so the return value cannot catch it.
3. **The postflight asks the change's own question** (§V.97). It re-reads the list, not the
   `200 OK` the mutation printed. `ignore_writes` is exactly that case: `pmgsh` says yes and the
   whitelist says no.
4. **Unavailable is not refuted** (§V.62's rule one system over, §V.86). A postflight read that
   could not answer is `changed` + `verified: false` + `verification: unavailable`, never a bare
   `false` an operator reads as a measurement. A failed `pmgconfig sync` is its own third thing:
   the config moved and mail flow did not.

The fifth thread is **§V.96 having no instance here**, which is asserted rather than assumed: a
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
    TOOL_PMG_WHITELIST,
)
from noa_api.mcp_tools.pmg_whitelist_runner import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SYNC_FAILED,
    build_pmg_whitelist_runner,
    build_pmg_whitelist_runners,
)
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


def build_runner(fixture: Any):  # type: ignore[no-untyped-def]
    """The runner over this fixture's context."""
    return build_pmg_whitelist_runner(context=fixture.context)


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
    assert box.entries == [BYSTANDER]


# --- §V.60, §V.61: the commands, in order, and the sync that applies them ---


async def test_an_add_reads_creates_syncs_then_reads_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pmgsh create` then `pmgconfig sync --restart 1`, and the order is the property.

    A mutation that skipped the sync looks applied and is not: `pmgsh` writes PMG's config and
    Postfix does not pick it up until the sync runs. The trailing read is the postflight (§V.97),
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


# --- T29's departure from `noa-old`: which spelling a removal names ---


async def test_a_removal_deletes_by_pmgs_own_spelling_and_not_the_normalised_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bytes, not the verdict (§V.59, and `noa-old`'s defect).

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


async def test_an_add_writes_the_masked_network_and_not_the_typed_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§V.59 on the side that writes: `203.0.113.10/24` is a request about `203.0.113.0/24`.

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


# --- §V.98's fact-check half: the approval window is checked on the fact ---


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
    """One source, so silence is not a state (§V.86). A change decided against a read that did not
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
    assert box.mutations == []


async def test_a_denied_sudo_keeps_its_own_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """§V.55: two causes, two remedies — a sudoers entry is not a broken `pmgsh` install.

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


# --- §V.33: the evidence, never the arguments ---


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
    """The third place the enum is bounded, and the CIDR with it (T25's discipline).

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


# --- §V.97, §V.86, §V.62: the postflight ---


async def test_a_mutation_that_answered_200_ok_while_the_list_did_not_move_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§V.97: the verdict is read off `mynetworks`, not off what `pmgsh` printed.

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


async def test_a_postflight_that_cannot_be_read_is_unavailable_and_not_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§V.62's rule one system over: verification-unavailable is not verified, and not refuted.

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
    assert box.entries == [BYSTANDER, TARGET_NORMALIZED]
    assert status_for_payload(payload) is ToolRunStatus.FAILED


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
    assert box.entries == [TARGET, TARGET_NORMALIZED]
    assert box.synced == 0


# --- §V.96 has no instance here, and that is asserted ---


async def test_the_operator_reason_reaches_neither_a_command_nor_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `mynetworks` entry is a CIDR, so nothing C8 keeps from the LLM leaves NOA here.

    Asserted on the serialized payload, the derived summary and the built receipt rather than on a
    key set — a value dropped in one place and kept in another passes a key compare (§V.87) — and
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
    nothing, which is exactly the compare that stops separating (§V.87).
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
    a host where a network changed (§V.59). Not the other entries: what a model needs is which
    address on which node moved which way, not a mail gateway's whole allow list (§V.26, §V.76).
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
    """The envelope this runner answers is the one `core.audit.summaries` reads (V20, V46)."""
    fixture, _ = whitelist_change_context(monkeypatch)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is ToolRunStatus.COMPLETED


async def test_the_runner_map_is_keyed_by_the_tool_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """What `change_runners` merges and what `registry` checks coverage against (T38, V46)."""
    fixture, _ = whitelist_change_context(monkeypatch)

    runners = build_pmg_whitelist_runners(context=fixture.context)

    assert set(runners) == {TOOL_PMG_WHITELIST}


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
