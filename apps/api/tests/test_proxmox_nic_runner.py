"""`proxmox_vm_nic`'s runner — the half that flips the link (T28).

Reachable only after an operator approved (§V.22's far side), so nothing here goes through the
tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`, and that is what these
tests build.

**Three properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **The runner re-reads before it writes** (T28's departure from `noa-old`). The digest it writes
   under is the one from *its own* read, so an unrelated edit made while the card sat pending
   neither refuses the change nor gets reverted by it. The fake enforces the digest the way
   Proxmox does, so "it re-read" is something the transport can fail rather than a comment — and
   the lost-update half is asserted on the **bytes written**, because a runner writing the
   gate-time line back would answer identically.
2. **The postflight asks the change's own question** (§V.97). It reads the `netN` line, not the
   task's exit status. `ignore_write` with `task_exit_status="OK"` is exactly that case: Proxmox
   says yes and the interface says no.
3. **Unavailable is not refuted** (§V.62's rule one system over, §V.86). A postflight read that
   could not answer is `changed` + `verified: false` + `verification: unavailable`, never a bare
   `false` an operator reads as a measurement.

The fourth thread is **§V.96 having no instance here**, which is asserted rather than assumed: the
runner never reads `request.reason`, so a sentinel driven through the approval must not appear in
the serialized payload, the derived summary, the built receipt — or on the wire.

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
    TOOL_PROXMOX_VM_NIC,
)
from noa_api.mcp_tools.proxmox_nic_runner import (
    ERROR_POSTFLIGHT_FAILED,
    ERROR_TASK_FAILED,
    ERROR_TASK_TIMEOUT,
    TASK_POLL_ATTEMPTS,
    build_proxmox_nic_runners,
    build_proxmox_vm_nic_runner,
)
from noa_api.mcp_tools.proxmox_password import ERROR_SERVER_UNAVAILABLE
from support.action_decisions import REASON
from support.change_delta import PayloadRunner, payload_runner
from support.proxmox_nic import (
    NET0,
    NET0_UP,
    NET1,
    NET1_UP,
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


# --- T28's departure from `noa-old`: the digest is the runner's own ---


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


# --- §V.33: the evidence, never the arguments ---


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
    """§V.63's third place, and §V.33's refusal rather than repair.

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


# --- §V.97, §V.86, §V.62: the postflight ---


async def test_a_task_that_reports_ok_while_the_nic_did_not_move_is_a_failure() -> None:
    """§V.97: the postflight asks the change's own question.

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


async def test_a_postflight_that_cannot_be_read_is_unavailable_and_not_unverified() -> None:
    """§V.86, §V.62: silence is not evidence of absence, and it is not refutation either.

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


async def test_a_readable_postflight_that_agrees_is_verified() -> None:
    """The negative control for both branches above.

    Without it, "report unavailable" and "report a mismatch" each pass against a runner that never
    verifies anything at all (§V.87).
    """
    fixture, _ = nic_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["verified"] is True
    assert "verification" not in payload


# --- The write and its task ---


async def test_a_refused_write_is_reported_with_proxmoxs_own_code() -> None:
    """`digest_mismatch` and `permission_denied` send an operator to different places, so the
    integration layer's classification is kept rather than collapsed (§V.19's argument)."""
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


async def test_a_task_that_finishes_badly_is_a_failure_and_the_nic_is_unchanged() -> None:
    """A terminal non-`OK` task is Proxmox saying it did not apply the write."""
    fixture, _vm = nic_context(vm=FakeProxmoxNICVM(ignore_write=True, task_exit_status="unable"))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_FAILED
    assert "unable" in payload["message"]


async def test_a_task_that_never_finishes_times_out_rather_than_reporting_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not knowing what a task did is not evidence that it failed, and the write was accepted."""
    no_polling_delay(monkeypatch)
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(task_never_finishes=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_TIMEOUT
    assert len([path for _method, path in vm.requests if "/tasks/" in path]) == TASK_POLL_ATTEMPTS


async def test_a_write_that_answers_synchronously_is_not_polled() -> None:
    """Proxmox answers `data: null` when a config write finished inline (`docs/integrations`)."""
    fixture, vm = nic_context(vm=FakeProxmoxNICVM(write_upid=None))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert not [path for _method, path in vm.requests if "/tasks/" in path]


# --- §V.96 has no instance here, and that is asserted ---


async def test_the_operator_reason_reaches_neither_the_wire_nor_the_payload() -> None:
    """A `netN` line has no note field, so nothing C8 keeps from the LLM leaves NOA here.

    Asserted on the serialized payload, the derived summary and the built receipt rather than on a
    key set — a value dropped in one place and kept in another passes a key compare (§V.87) — and
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
    nothing, which is exactly the compare that stops separating (§V.87).
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
    bridge of a machine it is about to describe to somebody (§V.45, §V.76, §V.26).
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
    """§V.20, §V.46: the run's terminal status is read off `ok`, so every branch has to set it.

    The unavailable branch is deliberately `COMPLETED` — the change happened; only the
    confirmation did not — which is the whole of §V.62's distinction expressed in the audit row.
    """
    fixture, _ = nic_context(vm=FakeProxmoxNICVM(**vm_kwargs))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is expected


async def test_the_runner_map_covers_this_tool_and_nothing_else() -> None:
    """What `registry.assert_change_runners_cover` and `noa_api.main` both read (T38)."""
    context = nic_context()[0].context

    assert set(build_proxmox_nic_runners(context=context)) == {TOOL_PROXMOX_VM_NIC}


async def test_the_reason_is_on_the_request_and_the_runner_simply_does_not_read_it() -> None:
    """§V.43's permission is unused here, which is a fact about this runner rather than the gate.

    Stated as a test because the alternative is a comment: the executor puts the operator's words
    on every `ChangeExecutionRequest` (T22), and a future edit that started reading them would
    open §V.96's return paths on a tool that has none today.
    """
    fixture, _ = nic_context()
    request = execution_request(server_id=server_id(fixture), reason=REASON)

    payload = await build_runner(fixture)(request)

    assert request.reason == REASON
    assert REASON not in payload_text(payload)
