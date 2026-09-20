"""`proxmox_reset_vm_password`'s runner — the half that changes the VM.

Reachable only after an operator approved — the far side of the cookie/CSRF boundary — so nothing
here goes through the tool. `core.approvals.execution` hands a runner a `ChangeExecutionRequest`,
and that is what these tests build.

**Three properties carry this file**, and each is a claim a plausible implementation gets wrong:

1. **Deliver before you apply.** yopass is stored *first*, so a delivery failure aborts
   with the VM untouched. Asserted on the **absence of a config write** rather than on the
   returned envelope — a runner that wrote first and reported the failure afterwards would
   produce the same return value and leave a live password nobody has a copy of.
2. **The generated password never leaves this frame.** Server-side generation; only `yopass_url`
   returns. The runner's payload becomes
   `tool_runs.result_summary` and the receipt's `after`, and `noa_get_action_result` hands the
   summary to a model — so the assertion is made against the *serialized* payload, the derived
   summary and the built receipt, not against a key set — a field dropped in one
   place and kept in another passes a key compare.
3. **Verification-unavailable is not verified, and not refuted either.** A host
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
    VERIFICATION_POLL_ATTEMPTS,
    build_proxmox_reset_vm_password_runner,
)
from noa_api.mcp_tools.proxmox_task import (
    ERROR_TASK_FAILED,
    ERROR_TASK_TIMEOUT,
    TASK_POLL_ATTEMPTS,
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

# What every heading and every sentence on this tool names: the account and VM, and the same with
# the endpoint it sits on.
SUBJECT = f"{USERNAME} on VM {VMID}"
WHERE = f"{SUBJECT} ({SERVER_NAME})"

# The owner's own sentence, spelled out here rather than imported from the runner. It is the one
# "do this next" line that survives on a card — it states NOA's permission boundary rather than
# giving advice — and an assertion that imported the constant would go on passing through any
# rewording of the words the owner actually supplied.
STOP_START = (
    "The old password keeps working until the VM is stopped and started again. NOA cannot stop or "
    "start a VM — stop and start it from the customer portal or from Proxmox."
)

# What this fixture's delivery configuration renders to (`SECRET_DELIVERY_*` in `support.servers`:
# two days, and re-fetchable within them). Two days rather than `Settings`' seven on purpose, so a
# runner that spelled this deployment's expiry into a string fails here instead of reading it.
LINK = "Give the operator the link; it works for 2 days."


def no_crypt_library() -> CDLL | None:
    """A host with no libcrypt — the crypt verdict's subject, injected rather than patched."""
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

    The sentence is asserted whole rather than by substring, so a clause appearing where none
    belongs fails as loudly as one going missing — and no verdict word may appear in it, because
    the card states the verdict in its status corner and a sentence saying it too teaches a
    reader to skip both.
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
    assert payload["headline"] == f"Password reset — {SUBJECT}"
    assert payload["message"] == (
        f"The cloud-init password for {WHERE} was changed. {STOP_START} {LINK}"
    )
    assert vm.password_hash != before


async def test_the_password_is_generated_here_at_the_configured_length() -> None:
    """Server-side, and long enough that a fallback default would show.

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
    """The blob names the account the operator saw on the card, not a tool argument — context
    persisted at gate time.
    """
    fixture, _ = reset_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert fixture.secret_delivery.calls[0]["username"] == USERNAME


# --- The plaintext never leaves this frame ---


async def test_the_generated_password_is_absent_from_payload_receipt_and_summary() -> None:
    """Only `yopass_url` returns, asserted at all three places the payload becomes durable.

    `result_summary` is what `noa_get_action_result` hands a model, and the
    receipt's `after` is what the card and the admin audit surface read. Asserted on the
    *serialized* text rather than on a key set, because a field dropped from the top level and
    kept in a nested structure passes a key compare.

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
    """No path back to a model opens on this tool, and that is a property rather than an oversight.

    Cloud-init has no note field, so nothing the operator typed is written onto the VM — the
    shape the suspend and allowlist-remove tools have on their write side. The reason is on the
    request — the one operator-typed field carries it for
    every approved change; what matters is that it does not come back out.
    """
    fixture, vm = reset_context()
    request = execution_request(server_id=server_id(fixture), reason="customer locked out")

    payload = await build_runner(fixture)(request)

    assert "customer locked out" not in payload_text(payload)
    assert "customer locked out" not in (result_summary(payload) or "")
    assert all("customer locked out" not in path for _method, path in vm.requests)


# --- Deliver before you apply ---


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(YopassNotConfiguredError("no base url"), id="not-configured"),
        pytest.param(YopassStoreError("yopass returned HTTP 502"), id="store-failed"),
    ],
)
async def test_a_delivery_failure_aborts_before_any_config_write(error: Exception) -> None:
    """**yopass-before-set ordering, and the assertion is the empty write list.**

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
    assert payload["headline"] == f"Password not changed — {SUBJECT}"
    # The delivery hop's own words — `yopass_base_url is not configured` names a deployment to
    # fix — ride on the envelope's `error_code` where an administrator looks, and not in the
    # sentence, whose reader has one question: did the VM move.
    assert payload["message"] == (
        f"NOA could not deliver a new password for {WHERE}, so nothing was changed on the VM."
    )
    assert str(error) not in str(payload["message"])


async def test_the_delivery_hop_runs_before_the_write_in_the_happy_path_too() -> None:
    """The negative control for the ordering above.

    "No writes" also holds for a runner that does nothing at all, so the ordering claim needs the
    case where both happen: delivery was called, *and* the VM was written, in that order.
    """
    fixture, vm = reset_context()

    await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert len(fixture.secret_delivery.calls) == 1
    assert [method for method, _path in vm.config_writes] == ["POST", "PUT"]


# --- Which failures ship the URL ---


async def test_a_refused_config_write_withholds_the_link() -> None:
    """The named residual case of deliver-before-apply, from the safe side.

    Proxmox refused the write, so the VM keeps its old credentials and the generated password is
    live nowhere. Handing over a link to it is how a customer is told to use a password that
    never existed.
    """
    fixture, vm = reset_context(vm=FakeProxmoxVM(set_error={"status": 403}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == "permission_denied"
    assert "yopass_url" not in payload
    # The heading and the last sentence are the same claim the absent link is: Proxmox took
    # nothing, so the old credentials still work and the generated password is live nowhere.
    # Proxmox's own words stay in the middle, because on a refusal they are frequently the only
    # thing naming a remedy; its error code does not, and rides on the envelope instead.
    assert payload["headline"] == f"Password not changed — {SUBJECT}"
    assert payload["message"] == (
        f"Proxmox refused a step of the password change for {WHERE}. Proxmox permission denied: "
        "boom. No new password was delivered."
    )
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
    assert payload["headline"] == f"Password not changed — {SUBJECT}"
    # The exit status is the whole of what Proxmox said, so it travels; `task_failed` names the
    # remedy to an engineer and stays on the envelope.
    assert payload["message"] == (
        f"Proxmox refused a step of the password change for {WHERE}. Its task finished with exit "
        "status 'unable to parse'. No new password was delivered."
    )


async def test_a_task_that_never_finishes_ships_the_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The other direction, and the one an "attach nothing on failure" rule gets wrong.**

    Proxmox accepted the write; NOA simply stopped waiting for its task. The password may be
    live, and the link is the only copy of it — withholding it here is a lockout NOA created.

    The VM here is the one that did *not* take the password, because a write Proxmox accepted is
    now compared against the VM before anything is reported. The VM that took it is the case
    below, and it is the one that stops being a failure at all.
    """
    no_polling_delay(monkeypatch)
    vm = FakeProxmoxVM(task_never_finishes=True, ignore_password_write=True)
    fixture, _ = reset_context(vm=vm)

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_TASK_TIMEOUT
    assert payload["yopass_url"] == YOPASS_URL
    # Not `Password not changed`: Proxmox accepted the write, which is why the link ships, so a
    # heading claiming the VM is untouched would state what nobody measured. `yet` carries the
    # same asymmetry in the sentence — the read may simply be earlier than the change.
    assert payload["headline"] == f"Password change failed — {SUBJECT}"
    assert payload["message"] == (
        f"Proxmox did not answer a step of the password change for {WHERE}, and the VM does not "
        f"carry the new password yet. {STOP_START} {LINK}"
    )
    assert len([path for _m, path in vm.requests if "/tasks/" in path]) == TASK_POLL_ATTEMPTS


async def test_a_task_that_never_finishes_on_a_vm_that_took_it_reports_the_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The crypt compare is the strongest confirm NOA has, and a failed step does not waste it.

    The password compared against was generated in the runner's own frame and written down
    nowhere, so a VM carrying it is a VM that took this write — nobody else could have set that
    exact value. That is why this reports a plain confirmation with no qualifier about
    attribution, where a tool comparing a shared field could not.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(task_never_finishes=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["yopass_url"] == YOPASS_URL
    assert payload["headline"] == f"Password reset — {SUBJECT}"
    # The step that failed is still named, so a confirmation is never read as a call that
    # answered — in words rather than by its code, which names a remedy to an engineer and
    # nothing to the operator. There is no `error_code` on this envelope to carry it either, and
    # nothing distinguishable goes with it: every failure that can reach this branch and still
    # match is one Proxmox never answered for.
    assert payload["message"] == (
        f"Proxmox did not answer a step of the password change for {WHERE}, so NOA compared the "
        f"password it generated against the VM: the VM carries it. {STOP_START} {LINK}"
    )
    assert ERROR_TASK_TIMEOUT not in str(payload["message"])


async def test_a_failed_regeneration_ships_the_link() -> None:
    """Same rule: the `cipassword` write already landed, only the drive rewrite did not.

    The VM is the one whose rendered document still shows somebody else's password — the drive
    was never rewritten, which is exactly what this failure is — so the compare disagrees. A
    step Proxmox *refused* makes that disagreement a measurement, which is why this is a failure
    and not an unknown.
    """
    fixture, _ = reset_context(
        vm=FakeProxmoxVM(regenerate_error={"status": 500}, ignore_password_write=True)
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is False
    assert payload["yopass_url"] == YOPASS_URL
    # A refusal with a reading behind it is a measurement, so this lands on the mismatch branch
    # and gets the one heading allowed to say the VM still has what it had.
    assert payload["headline"] == f"Password not changed — {SUBJECT}"
    assert payload["message"] == (
        f"NOA checked afterwards: {WHERE} does not carry the new password. {LINK}"
    )


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


# --- The crypt guard, at the runner ---


async def test_a_host_without_libcrypt_reports_changed_but_unverified() -> None:
    """**The crypt verdict at the surface an operator reads.**

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
    # The confirmed heading, because Proxmox took the write; what is unconfirmed belongs in the
    # sentence and in the status corner beside the heading. `could not check` rather than `could
    # not read`: this host read the VM perfectly well and had nothing to compare with, and the
    # unreadable-dump branch below never got that far — what they share is that nothing was
    # compared.
    assert payload["headline"] == f"Password reset — {SUBJECT}"
    assert payload["message"] == (
        f"Proxmox accepted the new password for {WHERE}. NOA could not check whether the VM "
        f"carries it. {STOP_START} {LINK}"
    )


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
    """**The negative control the two tests above need.**

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
    assert payload["headline"] == f"Password not changed — {SUBJECT}"
    # **The stop-and-start clause is absent here and that is the assertion.** Everywhere the VM
    # may carry the new password it rides, because a stop and start is what makes the password
    # take. Here NOA measured that the VM does not carry it, so a stop and start would change
    # nothing and telling an operator to perform one is advice this very reading disproves. Stated
    # before the whole message below, which would catch the same fold as an unreadable diff of two
    # long strings.
    assert STOP_START not in str(payload["message"])
    assert payload["message"] == (
        f"NOA checked afterwards: {WHERE} does not carry the new password. {LINK}"
    )


async def test_a_dump_that_cannot_be_read_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document NOA could not fetch has not told it the password is wrong — no answer, never the
    benign value.
    """
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(dump_error={"status": 500}))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE


# --- What the operator is told about the link ---


@pytest.mark.parametrize(
    ("one_time", "expiration_seconds", "clause"),
    [
        pytest.param(True, 604800, "it opens once", id="one-open-deployment"),
        pytest.param(False, 3600, "it works for 1 hour", id="an-hour-and-singular"),
        pytest.param(False, 259200, "it works for 3 days", id="three-days"),
        pytest.param(False, 5400, "it works for 90 minutes", id="an-hour-and-a-half"),
    ],
)
async def test_the_link_clause_is_read_off_the_delivery_configuration(
    one_time: bool, expiration_seconds: int, clause: str
) -> None:
    """**Both facts in that sentence are settings, so neither may be written into it.**

    This tool shipped `it opens once` against a deployment whose `YOPASS_ONE_TIME` is off, which
    was simply false — and correcting it to a literal `7 days` would have been the same bug with
    a new number, because the expiry is a setting too. So the cases below are deliberately *not*
    this deployment's values: a one-time deployment gets the one-open wording back because the
    flag says so, and every other case reads its duration off
    `YOPASS_SECRET_EXPIRATION_SECONDS`.

    The two that do not divide into whole days are the ones a `// 86400` would get wrong — an
    hour would render as no days at all — and they are here because the rounding this avoids
    would be a second false clause of exactly the kind being corrected. The singular rides on the
    same case.
    """
    fixture, _ = reset_context(
        secret_delivery_one_time=one_time,
        secret_delivery_expiration_seconds=expiration_seconds,
    )

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert payload["message"] == (
        f"The cloud-init password for {WHERE} was changed. {STOP_START} Give the operator the "
        f"link; {clause}."
    )


# --- The runner acts on the evidence, never on the arguments ---


async def test_the_runner_resolves_the_server_from_the_evidence() -> None:
    """Context persisted at gate time. The request's `arguments` name a different endpoint on
    purpose.

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


# --- How the executor records what came back ---


async def test_a_confirmed_reset_records_a_completed_run_and_a_two_part_receipt() -> None:
    """The status is read off the envelope and the receipt keeps both halves — run plus receipt, one
    commit.

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
    """The crypt verdict's consequence one layer out, and the reason `ok` is true on that branch.

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
    """The separation the test above needs: a measured mismatch is `FAILED`."""
    no_polling_delay(monkeypatch)
    fixture, _ = reset_context(vm=FakeProxmoxVM(ignore_password_write=True))

    payload = await build_runner(fixture)(execution_request(server_id=server_id(fixture)))

    assert status_for_payload(payload) is ToolRunStatus.FAILED
