"""`proxmox_vm_nic`'s runner — the half that flips the link.

Reachable only after an operator approved — the far side of the cookie/CSRF boundary — so nothing
here goes through the tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`,
and that is what these tests build.

**Three properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **The runner re-reads before it writes** — the NIC tool's departure from `noa-old`. The digest it
   writes
   under is the one from *its own* read, so an unrelated edit made while the card sat pending
   neither refuses the change nor gets reverted by it. The fake enforces the digest the way
   Proxmox does, so "it re-read" is something the transport can fail rather than a comment — and
   the lost-update half is asserted on the **bytes written**, because a runner writing the
   gate-time line back would answer identically.
2. **The postflight asks the change's own question.** It reads the `netN` line, not the
   task's exit status. `ignore_write` with `task_exit_status="OK"` is exactly that case: Proxmox
   says yes and the interface says no.
3. **Unavailable is not refuted.** A postflight read that
   could not answer is `changed` + `verified: false` + `verification: unavailable`, never a bare
   `false` an operator reads as a measurement.

The fourth thread is **no path back to a model opening here**, which is asserted rather than
assumed: the runner never reads `request.reason`, so a sentinel driven through the approval must not
appear in the serialized payload, the derived summary, the built receipt — or on the wire.

The seams: a real `ProxmoxClient` over an `httpx` transport, a real `SecretCipher` decrypting a
real ciphertext token, the real codec, the real resolver. Only the socket is doubled.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from core.approvals.execution import build_receipt
from core.audit.summaries import result_summary, status_for_payload
from core.db.lifecycle import ToolRunStatus
from noa_api.mcp_tools.change_target import (
    ERROR_EVIDENCE_UNUSABLE,
    STATUS_CHANGED,
    STATUS_NO_OP,
    VERIFICATION_UNAVAILABLE,
)
from noa_api.mcp_tools.proxmox_nic import (
    ACTION_DISABLE,
    ACTION_ENABLE,
    ERROR_NET_NOT_FOUND,
    EVIDENCE_NIC,
)
from noa_api.mcp_tools.proxmox_nic_runner import (
    ERROR_POSTFLIGHT_FAILED,
    MESSAGE_NO_BEFORE_READING,
    build_proxmox_vm_nic_runner,
)
from noa_api.mcp_tools.proxmox_password import ERROR_SERVER_UNAVAILABLE
from noa_api.mcp_tools.proxmox_task import (
    ERROR_TASK_FAILED,
    ERROR_TASK_TIMEOUT,
    TASK_POLL_ATTEMPTS,
)
from support.action_decisions import REASON
from support.change_delta import PayloadRunner, payload_runner
from support.proxmox_nic import (
    NET0,
    NET0_UP,
    NET1,
    NET1_UP,
    SERVER_NAME,
    VMID,
    FakeProxmoxNICVM,
    execution_request,
    nic_context,
    no_polling_delay,
    payload_text,
)


def build_runner(fixture: Any) -> PayloadRunner:
    """The runner over this fixture's context, answering its envelope.

    The delta it publishes beside that envelope is asserted in
    `test_change_delta.py`; every claim in this file is about the envelope.
    """
    return payload_runner(build_proxmox_vm_nic_runner(context=fixture.context))


def server_id(fixture: Any):
    return fixture.proxmox_servers.servers[0].id


def written(vm: FakeProxmoxNICVM) -> str:
    """The one `netN` value this runner put on the wire."""
    [(_key, value)] = vm.written_values
    return value


# --- The happy path, both directions ---


@pytest.mark.parametrize(
    ("action", "start", "expected_state"),
    [
        pytest.param(ACTION_DISABLE, NET0_UP, "down", id="disable"),
        pytest.param(ACTION_ENABLE, f"{NET0_UP},link_down=1", "up", id="enable"),
    ],
)
async def test_an_approved_change_moves_the_link_and_confirms_it(
    action: str, start: str, expected_state: str
) -> None:
    """The whole flow, with the VM's own line deciding the verdict.

    The fake keeps what was POSTed and the postflight reads it back through the production codec,
    so `verified: true` here means the interface really carries the state that was asked for — not
    that a canned fixture agreed with itself.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET0: start}))
    request = execution_request(
        server_id=server_id(fixture),
        action=action,
        link_state="up" if action == ACTION_DISABLE else "down",
    )

    payload = await build_runner(fixture)(request)

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    assert payload["link_state"] == expected_state
    assert vm.link_state() == expected_state
    # The card's heading, written here rather than derived from the tool name: `Proxmox Vm Nic`
    # names the machinery, and this names what happened to the interface. The verb is the
    # operator's own, and it turns with the direction of the change.
    verb = "disabled" if action == ACTION_DISABLE else "enabled"
    assert payload["headline"] == f"Network interface {verb} — {NET0} on VM {VMID}"
    # Two facts and no verdict word — `confirmed` is the status corner's job on the card — with
    # the interface's own `up` / `down` left untranslated in both of them.
    assert payload["message"] == (
        f"{NET0} on VM {VMID} ({SERVER_NAME}) is {expected_state}.\n"
        f"It was {'up' if action == ACTION_DISABLE else 'down'} before this ran."
    )


async def test_the_write_keeps_every_other_segment_of_the_line() -> None:
    """The codec's property, asserted where it costs something: on a live VM's config.

    `tag=42` and `firewall=1` are on this line. A rewrite that dropped either would move the VM
    onto a different VLAN or off its firewall, and the runner would still answer `ok`.
    """
    fixture, vm = nic_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert written(vm) == f"{NET0_UP},link_down=1"


async def test_only_the_named_interface_is_written() -> None:
    """A VM with two NICs keeps the one nobody approved a change to."""
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET0: NET0_UP, NET1: NET1_UP}))

    await build_runner(fixture)(execution_request(server_id=server_id(fixture), net=NET0))

    assert [key for key, _value in vm.written_values] == [NET0]
    assert vm.nets[NET1] == NET1_UP


# --- The NIC tool's departure from `noa-old`: the digest is the runner's own ---


async def test_a_config_edit_made_while_the_card_was_pending_survives_the_change() -> None:
    """The lost-update case, and the reason the gate-time line is not written back.

    Somebody moves the VM to `vmbr9` while the approval card sits pending. A runner writing the
    line the card described would revert that, silently, as a side effect of a change that was
    about the link state — and would answer `ok`. Asserted on the bytes written, because the
    return value is identical either way.
    """
    fixture, vm = nic_context()
    request = execution_request(server_id=server_id(fixture))

    vm.nets[NET0] = "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr9,firewall=1,tag=42"
    vm.bump_digest()

    payload = await build_runner(fixture)(request)

    assert payload["ok"] is True
    assert "bridge=vmbr9" in written(vm)
    assert vm.nets[NET0] == "virtio=AA:BB:CC:DD:EE:01,bridge=vmbr9,firewall=1,tag=42,link_down=1"


async def test_a_stale_digest_is_refused_by_proxmox_and_the_fake_enforces_it() -> None:
    """The negative control for the test above — the transport really can fail a stale write.

    Without this, "the runner re-read" passes against a runner that sent no digest at all, or one
    the fake never checked. The write goes out with a digest the VM has moved past, and Proxmox's
    own refusal comes back.
    """
    fixture, vm = nic_context()
    client = fixture.context.proxmox_client_factory(
        fixture.proxmox_servers.servers[0], cipher=fixture.cipher
    )

    async with client:
        result = await client.update_qemu_config(
            "pve1", 110, digest="a-digest-from-an-older-read", net_key=NET0, net_value=NET0_UP
        )

    assert result["ok"] is False
    assert result["error_code"] == "digest_mismatch"
    assert vm.nets[NET0] == NET0_UP


# --- What the approval window is checked against: the link state ---


async def test_a_nic_already_in_the_asked_for_state_is_a_no_op_rather_than_a_failure() -> None:
    """Somebody reached it first. The world is as the operator wanted it.

    Reporting this as a failure would send an operator to fix something that is not broken, and
    the assertion that matters is that nothing was written.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET0: f"{NET0_UP},link_down=1"}))

    payload = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), action=ACTION_DISABLE)
    )

    assert payload["ok"] is True
    assert payload["status"] == STATUS_NO_OP
    assert payload["verified"] is True
    assert vm.config_writes == []
    assert vm.written_values == []
    assert payload["headline"] == f"Nothing to change — {NET0} on VM {VMID}"
    # `down`, not `disabled`: the interface's own word for its state, untranslated. And this
    # sentence **is** the branch's empty diff said in words, which is why no before-clause prints
    # beside it — asserted on the whole string, because a trailing one would pass a substring test.
    assert payload["message"] == (
        f"{NET0} on VM {VMID} ({SERVER_NAME}) was already down when NOA ran this change, so "
        "nothing was written."
    )


async def test_a_nic_that_still_has_to_move_is_written() -> None:
    """The negative control: without it, "answer `no_op`" passes against a runner that never
    writes at all, and every change silently becomes a report of one."""
    fixture, vm = nic_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["status"] == STATUS_CHANGED
    assert len(vm.config_writes) == 1


async def test_an_interface_removed_before_the_change_ran_is_refused_by_name() -> None:
    """A line that no longer exists cannot be edited, and a neighbouring one is not it."""
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(nets={NET1: NET1_UP}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture), net=NET0))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_NET_NOT_FOUND
    assert vm.config_writes == []
    assert vm.nets[NET1] == NET1_UP


# --- The evidence, never the arguments ---


async def test_the_target_comes_from_the_evidence_and_not_from_the_arguments() -> None:
    """`server_ref` on the request names an endpoint that does not resolve.

    Inventory can be edited between a request and its approval, and the evidence is the machine
    the operator actually saw on the card. A runner re-resolving the model's string would be a
    second resolution that can disagree with the one the decision rests on.
    """
    fixture, vm = nic_context()

    payload = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), server_ref="an-endpoint-that-is-not-here")
    )

    assert payload["ok"] is True
    assert len(vm.config_writes) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"node": "   "}, id="blank-node"),
        pytest.param({"node": 7}, id="node-is-not-a-string"),
        pytest.param({"net": ""}, id="blank-net"),
        pytest.param({"vmid": 0}, id="zero-vmid"),
        pytest.param({"vmid": True}, id="bool-is-not-a-vmid"),
        pytest.param({"vmid": "110"}, id="vmid-came-back-a-string"),
        pytest.param({"action": "toggle"}, id="a-third-word"),
        pytest.param({"action": None}, id="action-went-missing"),
    ],
)
async def test_evidence_that_did_not_survive_its_round_trip_is_declined(
    overrides: dict[str, Any],
) -> None:
    """The enum's third bound place, and the gate-time evidence's refusal rather than repair.

    By the time this runs an operator has typed a reason and pressed Approve, so a guess here is a
    guess with an authorisation attached to it.
    """
    fixture, vm = nic_context()

    payload = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), **overrides)
    )

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert vm.requests == []


async def test_a_server_row_deleted_after_approval_names_the_proxmox_inventory() -> None:
    """Two codes because two remedies: this one sends an administrator to `proxmox_servers`."""
    fixture, vm = nic_context()

    payload = await build_runner(fixture)(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert vm.requests == []


# --- The postflight ---


async def test_a_task_that_reports_ok_while_the_nic_did_not_move_is_a_failure() -> None:
    """The postflight asks the change's own question.

    `ignore_write` accepts the write and keeps the old line, with the task still exiting `OK`.
    A runner reading its success off the task's exit status answers `verified: true` here, which
    is a change reported as applied on a machine where it was not.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(ignore_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    assert payload["verified"] is False
    assert payload["link_state"] == "up"
    assert len(vm.config_writes) == 1
    # `unchanged`, because the reading earned it: NOA read the interface back and it had not
    # moved. The sentence is that measurement, so no before-clause prints beside it either.
    assert payload["headline"] == f"Interface unchanged — {NET0} on VM {VMID}"
    assert payload["message"] == (
        f"NOA read the interface back on {SERVER_NAME}: {NET0} on VM {VMID} is still up."
    )


async def test_a_postflight_that_cannot_be_read_is_unavailable_and_not_unverified() -> None:
    """Silence is not evidence of absence, and it is not refutation either.

    Proxmox accepted the write, so calling this a failure would send an operator to repeat a
    change that has probably already happened. `verification: unavailable` says which kind of
    nothing it was.
    """
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(fail_reads_after_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"]
    assert len(vm.config_writes) == 1
    # The heading is the applied one, because Proxmox took the write. What is unconfirmed is in
    # the sentence, and the corner reads it off the verification state rather than off a word here.
    assert payload["headline"] == f"Network interface disabled — {NET0} on VM {VMID}"
    # No comparison was made, and the clause says only that. The link state the operator approved
    # against is still on the evidence, and this is the branch that sends them to the VM to look
    # for themselves — so the card names what to compare against rather than claiming NOA holds
    # nothing, which would contradict the card they approved from a minute earlier.
    assert payload["message"] == (
        f"Proxmox accepted the change to {NET0} on VM {VMID} ({SERVER_NAME}). NOA could not read "
        f"the interface back afterwards, so it cannot say the link is down.\n"
        "It was up when NOA last read it."
    )


async def test_a_readable_postflight_that_agrees_is_verified() -> None:
    """The negative control for both branches above.

    Without it, "report unavailable" and "report a mismatch" each pass against a runner that never
    verifies anything at all.
    """
    fixture, _ = nic_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["verified"] is True
    assert "verification" not in payload


# --- The before-clause: where a missing reading and a measured-equal one stay apart ---


async def test_the_four_before_clauses_are_four_different_sentences() -> None:
    """`None` and `()` are not one answer, and neither are the two halves of `None`.

    Four claims, and each states something different: an interface the card watched move, one that
    read then what it reads now, one NOA could not confirm but did read at gate time, and one the
    card carried no reading for at all.

    **The grammar is where two of them would fold.** "before this ran" claims a comparison; "when
    NOA last read it" claims only a reading. A runner spelling a missing `old` side and an
    unconfirmed-but-recorded one the same way tells an operator NOA holds nothing on the one
    branch where they have to go and check the VM by hand — which is the branch where they most
    need something to compare what they find against, and where the card they approved from
    displayed exactly that reading.

    Asserted together rather than one per test, because what has to hold is that the four
    *differ*: each sentence alone passes against an implementation that prints one of them always.
    """
    fixture, _ = nic_context()
    moved = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    fixture, _ = nic_context()
    # The interface was edited away and back while the request sat pending: the card's reading and
    # the postflight's agree, so NOA compared and nothing moved.
    unmoved = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), link_state="down")
    )

    # Proxmox took the write and the postflight read could not answer, so nothing was compared —
    # but the gate-time `up` is on the evidence and survives a read that failed after it.
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(fail_reads_after_write=True))
    unconfirmed = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    # The same unconfirmed branch with nothing recorded to name. Evidence replaced rather than
    # merged: what is being arranged is the *absence* of the key.
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(fail_reads_after_write=True))
    request = execution_request(server_id=server_id(fixture))
    request.evidence[EVIDENCE_NIC] = {"net": NET0, "bridge": "vmbr0"}
    uncompared = await build_runner(fixture)(request)

    assert str(moved["message"]).endswith("It was up before this ran.")
    assert str(unmoved["message"]).endswith("It already read down before this ran.")
    assert str(unconfirmed["message"]).endswith("It was up when NOA last read it.")
    assert str(uncompared["message"]).endswith(MESSAGE_NO_BEFORE_READING)
    # The last two run the same branch on the same VM and differ only in what the evidence
    # recorded, so this pair is the whole claim: the sentence turns on the reading's presence and
    # on nothing else.
    assert str(unconfirmed["message"]).removesuffix("It was up when NOA last read it.") == str(
        uncompared["message"]
    ).removesuffix(MESSAGE_NO_BEFORE_READING)
    assert (
        len({moved["message"], unmoved["message"], unconfirmed["message"], uncompared["message"]})
        == 4
    )


# --- The write and its task ---


async def test_a_refused_write_is_reported_with_proxmoxs_own_code() -> None:
    """`digest_mismatch` and `permission_denied` send an operator to different places, so the
    integration layer's classification is kept rather than collapsed."""
    fixture, vm = nic_context(
        vm=FakeProxmoxNICVM(
            write_error={
                "status": 403,
                "message": "Permission check failed (/vms/110, VM.Config.Network)",
            }
        )
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "permission_denied"
    assert vm.link_state() == "up"
    # The other arm of the heading: Proxmox refused and the fresh read agrees with the refusal, so
    # the pair is a measurement that nothing moved and the heading may say so. The sentence is
    # that measurement, so no before-clause prints — asserted on the whole string.
    assert payload["headline"] == f"Interface unchanged — {NET0} on VM {VMID}"
    assert payload["message"] == (
        f"Proxmox refused the change to {NET0} on VM {VMID} ({SERVER_NAME}). Proxmox permission "
        "denied: Permission check failed (/vms/110, VM.Config.Network). A fresh read agrees: "
        f"{NET0} on VM {VMID} reads as up."
    )


async def test_a_task_that_finishes_badly_is_a_failure_and_the_nic_is_unchanged() -> None:
    """A terminal non-`OK` task is Proxmox saying it did not apply the write."""
    fixture, _vm = nic_context(vm=FakeProxmoxNICVM(ignore_write=True, task_exit_status="unable"))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_FAILED
    assert "unable" in payload["message"]
    assert payload["headline"] == f"Interface unchanged — {NET0} on VM {VMID}"


async def test_a_task_that_never_finishes_times_out_rather_than_reporting_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not knowing what a task did is not evidence that it failed, and the write was accepted.

    The interface is read back before the timeout is reported, so the case has to be the VM whose
    link did not move: a task NOA stopped waiting on, on a VM that still reads the old state, is
    `task_timeout` and stays it. The VM that *did* move is the case one test down.
    """
    no_polling_delay(monkeypatch)
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(task_never_finishes=True, ignore_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_TIMEOUT
    assert len([path for _method, path in vm.requests if "/tasks/" in path]) == TASK_POLL_ATTEMPTS
    # Not `Interface unchanged`: the read agreeing with a write nobody answered is not a
    # measurement that nothing moved, so the heading says the change failed and stops there.
    assert payload["headline"] == f"Interface change failed — {NET0} on VM {VMID}"
    assert payload["message"] == (
        f"Proxmox did not answer the change to {NET0} on VM {VMID} ({SERVER_NAME}), and a fresh "
        f"read says {NET0} on VM {VMID} reads as up. The change may still land, so NOA cannot "
        "report it as one that did not happen.\nIt was up when NOA last read it."
    )
    # The write's own code names a remedy to an engineer and names nothing to the person reading
    # the card. It stays on the envelope above, and `/admin` renders it there.
    assert ERROR_TASK_TIMEOUT not in payload["message"]


async def test_a_task_that_never_finishes_on_a_link_that_moved_reports_the_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident this whole shape exists for, one system over from where it happened.

    Proxmox took the write and NOA stopped waiting on its task. The interface itself says the
    link moved, and that reading is the better witness than a deadline NOA chose: reporting
    `task_timeout` here sends an operator to check a change that has already landed.
    """
    no_polling_delay(monkeypatch)
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(task_never_finishes=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["link_state"] == vm.link_state()
    assert payload["headline"] == f"Network interface disabled — {NET0} on VM {VMID}"
    # The call is named in the sentence, so "confirmed" is never read as "answered".
    assert payload["message"] == (
        f"Proxmox did not answer the change to {NET0} on VM {VMID} ({SERVER_NAME}), so NOA read "
        f"the interface back: {NET0} on VM {VMID} is down.\nIt was up before this ran."
    )


async def test_a_write_that_answers_synchronously_is_not_polled() -> None:
    """Proxmox answers `data: null` when a config write finished inline (`docs/integrations`)."""
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(write_upid=None))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert not [path for _method, path in vm.requests if "/tasks/" in path]


# --- No path back to a model opens here, and that is asserted ---


async def test_the_operator_reason_reaches_neither_the_wire_nor_the_payload() -> None:
    """A `netN` line has no note field, so nothing the operator typed leaves NOA here.

    Asserted on the serialized payload, the derived summary and the built receipt rather than on a
    key set — a value dropped in one place and kept in another passes a key compare — and
    on every request body, because the only thing that can carry it out is a write.
    """
    sentinel = "customer-said-the-box-is-spamming"
    fixture, vm = nic_context()
    request = execution_request(server_id=server_id(fixture), reason=sentinel)

    payload = await build_runner(fixture)(request)

    assert request.reason == sentinel
    assert sentinel not in payload_text(payload)
    assert sentinel not in result_summary(payload)
    receipt = build_receipt(evidence=request.evidence, payload=payload)
    assert sentinel not in payload_text(receipt)
    assert sentinel not in written(vm)


async def test_the_sentinel_would_have_been_found_if_it_had_leaked() -> None:
    """The negative control for the assertion above: `payload_text` really does look.

    Without it, "the reason is not in the payload" passes against a helper that serializes
    nothing, which is exactly the compare that stops separating.
    """
    sentinel = "customer-said-the-box-is-spamming"

    assert sentinel in payload_text({"ok": True, "message": sentinel})
    assert sentinel in result_summary({"ok": True, "message": sentinel})


async def test_the_write_carries_only_the_interface_line_and_a_digest() -> None:
    """Nothing else of NOA's goes onto this VM — no description, no note, no comment field."""
    fixture, vm = nic_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert [key for key, _value in vm.written_values] == [NET0]


# --- What the audit row and a model are told ---


async def test_the_payload_carries_the_identifiers_and_the_verdict_and_not_the_line() -> None:
    """This becomes `tool_runs.result_summary`, and `noa_get_action_result` hands it to a model.

    What a model needs is which interface on which VM moved which way — not the MAC address and
    bridge of a machine it is about to describe to somebody.
    """
    fixture, _ = nic_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert set(payload) == {
        "ok",
        "server",
        "node",
        "vmid",
        "net",
        "action",
        "headline",
        "status",
        "link_state",
        "verified",
        "message",
    }
    text = payload_text(payload)
    assert "AA:BB:CC:DD:EE:01" not in text
    assert "vmbr0" not in text


@pytest.mark.parametrize(
    ("vm_kwargs", "expected"),
    [
        pytest.param({}, ToolRunStatus.COMPLETED, id="changed"),
        pytest.param(
            {"nets": {NET0: f"{NET0_UP},link_down=1"}}, ToolRunStatus.COMPLETED, id="no-op"
        ),
        pytest.param({"ignore_write": True}, ToolRunStatus.FAILED, id="postflight-failed"),
        pytest.param({"write_error": {"status": 500}}, ToolRunStatus.FAILED, id="write-refused"),
    ],
)
async def test_the_executor_can_classify_every_outcome_this_runner_produces(
    vm_kwargs: dict[str, Any], expected: ToolRunStatus
) -> None:
    """The run's terminal status is read off `ok`, so every branch has to set it.

    The unavailable branch is deliberately `COMPLETED` — the change happened; only the confirmation
    did not — the whole of write-and-apply-reported-separately expressed in the audit row.
    """
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(**vm_kwargs))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is expected


async def test_the_reason_is_on_the_request_and_the_runner_simply_does_not_read_it() -> None:
    """The one reason field's permission is unused here, which is a fact about this runner rather
    than the gate.

    Stated as a test because the alternative is a comment: the executor puts the operator's words
    on every `ChangeExecutionRequest`, and a future edit that started reading them would
    open a path back to a model on a tool that has none today.
    """
    fixture, _ = nic_context()
    request = execution_request(server_id=server_id(fixture), reason=REASON)

    payload = await build_runner(fixture)(request)

    assert request.reason == REASON
    assert REASON not in payload_text(payload)
