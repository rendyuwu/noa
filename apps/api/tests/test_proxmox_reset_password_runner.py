"""`proxmox_reset_vm_password`'s runner — the half that changes the VM (T27, T69).

Reachable only after an operator approved (§V.22's far side), so nothing here goes through the
tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`, and that is what
these tests build.

**Three properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **Deliver before you apply** (§V.62). yopass is stored *first*, so a delivery failure aborts
   with the VM untouched. Asserted on the **absence of a config write** rather than on the
   returned envelope — a runner that wrote first and reported the failure afterwards would
   produce the same return value and leave a live password nobody has a copy of.
2. **The generated password never leaves this frame** (C15, §V.49). The runner's payload becomes
   `tool_runs.result_summary` and the receipt's `after`, and `noa_get_action_result` hands the
   summary to a model — so the assertion is made against the *serialized* payload, the derived
   summary and the built receipt, not against a key set (§V.87's shape: a field dropped in one
   place and kept in another passes a key compare).
3. **Verification-unavailable is not verified, and not refuted either** (§V.62, §T.69). A host
   with no libcrypt reports `changed` + `verified: false` + `verification: unavailable`, and the
   link still goes out because Proxmox accepted the write.

**Which failures ship the yopass URL** is the fourth thread running through these tests, and the
rule is the module's own: the URL goes out exactly when NOA cannot rule out that the password
reached the VM. The two directions are asserted against each other, because "always attach it"
and "never attach it" each pass half of these.

The seams: a real `ProxmoxClient` over an `httpx` transport, a real `SecretCipher` decrypting a
real ciphertext token, the real `crypt(3)`, the real resolver. `RecordingSecretDelivery` stands in
for the yopass hop — `test_yopass_store.py` covers that helper against its own transport, and what
a runner test needs from it is the *order* it happened in.
"""

from __future__ import annotations

from ctypes import CDLL
from uuid import uuid4

import pytest

from core.approvals.execution import build_receipt
from core.audit.summaries import result_summary, status_for_payload
from core.db.lifecycle import ToolRunStatus
from core.integrations.proxmox.cloudinit import (
    CAUSE_CRYPT_LIBRARY_UNAVAILABLE,
    CAUSE_PASSWORD_HASH_ABSENT,
    crypt_password,
)
from core.secrets.errors import YopassNotConfiguredError, YopassStoreError
from noa_api.mcp_tools.change_target import STATUS_CHANGED, VERIFICATION_UNAVAILABLE
from noa_api.mcp_tools.proxmox_password_runner import (
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_POSTFLIGHT_FAILED,
    ERROR_SERVER_UNAVAILABLE,
    ERROR_TASK_FAILED,
    ERROR_TASK_TIMEOUT,
    TASK_POLL_ATTEMPTS,
    VERIFICATION_POLL_ATTEMPTS,
    build_proxmox_password_runners,
    build_proxmox_reset_vm_password_runner,
)
from support.change_delta import PayloadRunner, payload_runner
from support.proxmox_password import (
    NODE,
    OLD_PASSWORD,
    SALT,
    SERVER_NAME,
    USERNAME,
    VMID,
    FakeProxmoxVM,
    execution_request,
    no_polling_delay,
    payload_text,
    reset_context,
)
from support.servers import SECRET_PASSWORD_LENGTH, SECRETS, YOPASS_URL, RecordingSecretDelivery


def no_crypt_library() -> CDLL | None:
    """A host with no libcrypt — §T.69's subject, injected rather than patched."""
    return None


def build_runner(fixture, **kwargs) -> PayloadRunner:
    """The runner over this fixture's context, answering its envelope.

    The delta it publishes beside that envelope is asserted in
    `test_change_delta.py`; every claim in this file is about the envelope.
    """
    return payload_runner(build_proxmox_reset_vm_password_runner(context=fixture.context, **kwargs))


def server_id(fixture):
    return fixture.proxmox_servers.servers[0].id


# --- The happy path ---


async def test_an_approved_reset_changes_the_password_and_confirms_it() -> None:
    """The whole flow, with the real crypt compare deciding the verdict.

    The VM starts with a hash of `OLD_PASSWORD` and the fake recomputes its stored hash from
    whatever is actually POSTed, so `verified: true` here means the runner's own generated
    password is the one the rendered user-data now carries — not that a canned fixture agreed
    with itself.
    """
    fixture, vm = reset_context()
    before = vm.password_hash

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is True
    assert payload["yopass_url"] == YOPASS_URL
    assert payload["server"] == SERVER_NAME
    assert payload["node"] == NODE
    assert payload["vmid"] == VMID
    assert payload["username"] == USERNAME
    assert vm.password_hash != before


async def test_the_password_is_generated_here_at_the_configured_length() -> None:
    """C15, §V.49: server-side, and long enough that a fallback default would show.

    `SECRET_PASSWORD_LENGTH` is deliberately not `Settings`' 24, which is also
    `_DEFAULT_PASSWORD_LENGTH` — so a runner that ignored the context and called the generator
    bare would produce a 24-character password and fail here.
    """
    fixture, _ = reset_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    delivered = fixture.secret_delivery.delivered_password
    assert delivered is not None
    assert len(delivered) == SECRET_PASSWORD_LENGTH


async def test_the_username_delivered_is_the_one_on_the_evidence() -> None:
    """§V.33: the blob names the account the operator saw on the card, not a tool argument."""
    fixture, _ = reset_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert fixture.secret_delivery.calls[0]["username"] == USERNAME


# --- V49: the plaintext never leaves this frame ---


async def test_the_generated_password_is_absent_from_payload_receipt_and_summary() -> None:
    """§V.49 and §V.8, asserted at all three places the payload becomes durable.

    `result_summary` is what `noa_get_action_result` hands a model (§V.45, §V.76), and the
    receipt's `after` is what the card and the admin audit surface read. Asserted on the
    *serialized* text rather than on a key set, because a field dropped from the top level and
    kept in a nested structure passes a key compare (§V.87).

    The credential-shaped literals from the server row are checked too: the API token is
    decrypted on the way to every call here, so this is also the assertion that it is not
    riding back out in an error message.
    """
    fixture, _ = reset_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    password = fixture.secret_delivery.delivered_password
    assert password is not None

    rendered = payload_text(payload)
    summary = result_summary(payload) or ""
    receipt = payload_text(build_receipt(evidence={}, payload=payload))

    for haystack in (rendered, summary, receipt):
        assert password not in haystack
        for secret in SECRETS:
            assert secret not in haystack


async def test_no_reading_of_the_cloudinit_dump_reaches_the_payload() -> None:
    """The crypt hash of the password NOA just set stays in the runner's frame.

    `noa-old` returned a sanitized copy of the rendered user-data. Under the approval gate that
    document would reach a model through `result_summary`, so nothing derived from it is carried
    at all — a verdict is, the evidence for it is not.
    """
    fixture, vm = reset_context()

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    rendered = payload_text(payload)
    assert vm.password_hash is not None
    assert vm.password_hash not in rendered
    assert "cloud-config" not in rendered
    assert "cloudinit_dump_user" not in payload


async def test_the_runner_never_reads_the_operator_s_reason() -> None:
    """§V.96 has no instance on this tool, and that is a property rather than an oversight.

    Cloud-init has no note field, so nothing C8 keeps from the LLM is written onto the VM — the
    shape T23 and T26 have on their write side. The reason is on the request (V43 carries it for
    every approved change); what matters is that it does not come back out.
    """
    fixture, vm = reset_context()
    request = execution_request(server_id=server_id(fixture), reason="customer locked out")

    payload = await build_runner(fixture)(request)

    assert "customer locked out" not in payload_text(payload)
    assert "customer locked out" not in (result_summary(payload) or "")
    assert all("customer locked out" not in path for _method, path in vm.requests)


# --- V62: deliver before you apply ---


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(YopassNotConfiguredError("no base url"), id="not-configured"),
        pytest.param(YopassStoreError("yopass returned HTTP 502"), id="store-failed"),
    ],
)
async def test_a_delivery_failure_aborts_before_any_config_write(error: Exception) -> None:
    """**§V.62's ordering, and the assertion is the empty write list.**

    A runner that applied the password first and then failed to deliver it would return exactly
    the same envelope as this one. The only thing that separates the two is whether the VM was
    touched — so that is what is asserted, and the return value is checked second.
    """
    fixture, vm = reset_context(secret_delivery=RecordingSecretDelivery(error=error))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert vm.config_writes == []
    assert payload["ok"] is False
    assert payload["error_code"] == error.error_code  # type: ignore[attr-defined]
    # Nothing was stored, so there is no link to hand over and none is invented.
    assert "yopass_url" not in payload


async def test_the_delivery_hop_runs_before_the_write_in_the_happy_path_too() -> None:
    """The negative control for the ordering above (§V.87).

    "No writes" also holds for a runner that does nothing at all, so the ordering claim needs the
    case where both happen: delivery was called, *and* the VM was written, in that order.
    """
    fixture, vm = reset_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert len(fixture.secret_delivery.calls) == 1
    assert [method for method, _path in vm.config_writes] == ["POST", "PUT"]


# --- Which failures ship the URL ---


async def test_a_refused_config_write_withholds_the_link() -> None:
    """§V.62's named residual case, from the safe side.

    Proxmox refused the write, so the VM keeps its old credentials and the generated password is
    live nowhere. Handing over a link to it is how a customer is told to use a password that
    never existed.
    """
    fixture, vm = reset_context(vm=FakeProxmoxVM(set_error={"status": 403}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "permission_denied"
    assert "yopass_url" not in payload
    # Delivery still happened — that is the ordering, and it is why this case exists at all.
    assert len(fixture.secret_delivery.calls) == 1
    assert vm.password_hash == crypt_password(OLD_PASSWORD, SALT)


async def test_a_task_that_finished_badly_withholds_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal non-`OK` task is Proxmox saying it did not apply the write."""
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(task_exit_status="unable to parse"))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["error_code"] == ERROR_TASK_FAILED
    assert "yopass_url" not in payload


async def test_a_task_that_never_finishes_ships_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The other direction, and the one an "attach nothing on failure" rule gets wrong.**

    Proxmox accepted the write; NOA simply stopped waiting for its task. The password may be
    live, and the link is the only copy of it — withholding it here is a lockout NOA created.
    """
    no_polling_delay(monkeypatch)
    vm = FakeProxmoxVM(task_never_finishes=True)
    fixture, _ = reset_context(vm=vm)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_TIMEOUT
    assert payload["yopass_url"] == YOPASS_URL
    assert len([path for _m, path in vm.requests if "/tasks/" in path]) == TASK_POLL_ATTEMPTS


async def test_a_failed_regeneration_ships_the_link() -> None:
    """Same rule: the `cipassword` write already landed, only the drive rewrite did not."""
    fixture, _ = reset_context(vm=FakeProxmoxVM(regenerate_error={"status": 500}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["yopass_url"] == YOPASS_URL


async def test_a_synchronous_write_polls_no_task() -> None:
    """Proxmox answers `data: null` when the config write finished inline.

    `_request_json_task` reports that as `synchronous`, and a runner that polled a `None` UPID
    would spend fifteen seconds asking about a task id that does not exist.
    """
    vm = FakeProxmoxVM(set_upid=None)
    fixture, _ = reset_context(vm=vm)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert [path for _m, path in vm.requests if "/tasks/" in path] == []


# --- T69: the crypt guard, at the runner ---


async def test_a_host_without_libcrypt_reports_changed_but_unverified() -> None:
    """**§T.69 at the surface an operator reads** (§V.62).

    Three things at once, and each is a different way of getting this wrong:

    - `ok` is **true** — the change happened, and reporting it as failed would send an operator
      to reset a password that is already live;
    - `verified` is **false** with `verification: unavailable` beside it — never a bare `false`,
      which reads as a measurement nobody took;
    - the link **ships**, because the password may well be on the VM.
    """
    fixture, _ = reset_context()

    payload = await build_runner(fixture, crypt_loader=no_crypt_library)(
        execution_request(server_id=server_id(fixture))
    )

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["verification_cause"] == CAUSE_CRYPT_LIBRARY_UNAVAILABLE
    assert payload["yopass_url"] == YOPASS_URL


async def test_a_host_without_libcrypt_does_not_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The library is probed once, up front.

    Re-reading a document nobody can compare against is an operator waiting for an answer that
    was already known. Asserted on the dump-read count, because the polling loop is invisible in
    the return value.
    """
    no_polling_delay(monkeypatch)
    vm = FakeProxmoxVM()
    fixture, _ = reset_context(vm=vm)

    await build_runner(fixture, crypt_loader=no_crypt_library)(
        execution_request(server_id=server_id(fixture))
    )

    assert [path for _m, path in vm.requests if path.endswith("/cloudinit/dump")] == []


async def test_a_dump_that_never_carries_a_hash_is_unavailable_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second unavailable cause, reached through the real polling loop.

    A VM whose rendered user-data never grows a `password:` line has told NOA nothing. It is
    reported the same way as a missing libcrypt and with its own cause, because "we could not
    check" is one answer with two remedies.
    """
    no_polling_delay(monkeypatch)
    vm = FakeProxmoxVM(drop_password_line=True)
    fixture, _ = reset_context(vm=vm)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification_cause"] == CAUSE_PASSWORD_HASH_ABSENT
    assert payload["yopass_url"] == YOPASS_URL
    dumps = [path for _m, path in vm.requests if path.endswith("/cloudinit/dump")]
    assert len(dumps) == VERIFICATION_POLL_ATTEMPTS


async def test_a_vm_still_carrying_another_password_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The negative control the two tests above need** (§V.87).

    Every assertion in this section is satisfied by a runner that answers `unavailable` for
    everything. This is the one that is not: a VM that accepted the write and still shows a
    different password is a measured `MISMATCH`, and it is a failure — with the link attached,
    because Proxmox accepted the write and the password may be live anyway.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(ignore_password_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_POSTFLIGHT_FAILED
    assert payload["verified"] is False
    # Not the unavailable shape: this one was measured.
    assert "verification" not in payload
    assert payload["yopass_url"] == YOPASS_URL


async def test_a_dump_that_cannot_be_read_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§V.86: a document NOA could not fetch has not told it the password is wrong."""
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(dump_error={"status": 500}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE


# --- V33: the runner acts on the evidence, never on the arguments ---


async def test_the_runner_resolves_the_server_from_the_evidence() -> None:
    """§V.33. The request's `arguments` name a different endpoint on purpose.

    Inventory can be edited between a request and its approval, and the evidence is the state the
    operator actually saw on the card — so a runner that re-resolved `server_ref` would run the
    change somewhere the decision never covered.
    """
    fixture, vm = reset_context()

    payload = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), server_ref="a-different-cluster")
    )

    assert payload["ok"] is True
    assert payload["server"] == SERVER_NAME
    assert vm.config_writes != []


async def test_a_server_row_that_is_gone_refuses_before_delivery() -> None:
    """The row was deleted after the operator approved. Named separately from WHM's code.

    `proxmox_server_unavailable` sends an administrator to Proxmox inventory;
    `whm_server_unavailable` would send them to the wrong table.

    Nothing is generated and nothing is delivered, because the refusal comes first.
    """
    fixture, vm = reset_context()

    payload = await build_runner(fixture)(execution_request(server_id=uuid4()))

    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert fixture.secret_delivery.calls == []
    assert vm.requests == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("node", "   ", id="blank-node"),
        pytest.param("node", 7, id="node-is-not-a-string"),
        pytest.param("username", "", id="empty-username"),
        pytest.param("vmid", "110", id="vmid-round-tripped-as-a-string"),
        pytest.param("vmid", 0, id="vmid-out-of-range"),
    ],
)
async def test_unusable_evidence_is_refused_before_anything_is_generated(
    field: str, value: object
) -> None:
    """A value that did not survive its JSONB round trip is refused, never guessed at.

    By the time a runner reads this, an operator has typed a reason and pressed Approve — so a
    guess here is a guess with an authorisation attached to it. Nothing is delivered and nothing
    is written.
    """
    fixture, vm = reset_context()

    payload = await build_runner(fixture)(
        execution_request(server_id=server_id(fixture), **{field: value})
    )

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert fixture.secret_delivery.calls == []
    assert vm.requests == []


# --- How the executor records what came back (V20, V46) ---


async def test_a_confirmed_reset_records_a_completed_run_and_a_two_part_receipt() -> None:
    """§V.20, §V.46: the status is read off the envelope and the receipt keeps both halves.

    The before-state is the gate's own preflight, so a failed change still has a record of what
    was authorised — which is why the receipt is built here rather than derived from the answer.
    """
    fixture, _ = reset_context()
    evidence = {"server": SERVER_NAME, "vm": {"ciuser": USERNAME}}

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))
    receipt = build_receipt(evidence=evidence, payload=payload)

    assert status_for_payload(payload) is ToolRunStatus.COMPLETED
    assert receipt["ok"] is True
    assert receipt["before"] == evidence
    assert receipt["after"]["verified"] is True


async def test_an_unverified_reset_still_records_a_completed_run() -> None:
    """§T.69's consequence one layer out, and the reason `ok` is true on that branch.

    An unavailable verification is a change that happened, so the run is `COMPLETED` and the
    receipt says `verified: false` — rather than a `FAILED` run that would read as "the password
    was not changed" in the audit trail.
    """
    fixture, _ = reset_context()

    payload = await build_runner(fixture, crypt_loader=no_crypt_library)(
        execution_request(server_id=server_id(fixture))
    )

    assert status_for_payload(payload) is ToolRunStatus.COMPLETED
    assert build_receipt(evidence={}, payload=payload)["after"]["verification"] == (
        VERIFICATION_UNAVAILABLE
    )


async def test_a_mismatched_reset_records_a_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The separation the test above needs (§V.87): a measured mismatch is `FAILED`."""
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(ignore_password_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is ToolRunStatus.FAILED


# --- Registration ---


def test_the_runner_map_names_this_tool() -> None:
    """What `core.approvals.execution` dispatches on, and what the registry demands at startup."""
    fixture, _ = reset_context()

    runners = build_proxmox_password_runners(context=fixture.context)

    assert set(runners) == {"proxmox_reset_vm_password"}
