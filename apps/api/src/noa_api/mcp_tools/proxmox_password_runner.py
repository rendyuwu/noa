"""The half of `proxmox_reset_vm_password` that changes the VM.

Beside `proxmox_password.py` rather than inside it, for the file-size budget: a CHANGE tool is two
halves and together they run past 900 lines. The suspend tool made the same split for the same
reason and it falls on the boundary the design already draws — **nothing in the tool module can
change anything, and nothing here is reachable without an approval**. The evidence keys and the
tool's name come from that module; nothing there imports this one, so `registry.py` reaches the tool
and `change_runners.py` reaches the runner with no cycle between them.

**The order is the safety property, not an implementation detail**. Generate → *deliver* → apply.
yopass is stored **before** `cipassword` is written, so a delivery failure aborts with the VM
untouched and nobody locked out. The reverse order has one failure mode that cannot be recovered
from a log: the password is live on the VM and the only copy of it is gone. What the chosen order
costs instead is the case the verdict rule names and accepts — a write that fails *after* a
successful store leaves a URL holding a password nothing applied, and the old credentials still
work.

**Which failures ship the URL, and why it is not "all of them".** The rule is: the URL goes out
exactly when NOA cannot rule out that the new password reached the VM.

- Proxmox *refused* the config write, or the write's task exited non-`OK` — ruled out. No URL,
  because handing an operator a link to a password the VM never had is how a "reset" gets relayed
  to a customer who then cannot log in. This is the verdict rule's own sentence.
- Proxmox *accepted* the write — every later failure ships the URL: the regeneration failing, the
  task poll timing out, the crypt compare coming back unavailable or mismatched. The password may
  be live, and withholding the only copy of a live credential is a lockout **NOA** created.
  `noa-old` withheld it on the verification branches; this is the one behavioural correction to
  its flow beyond the three-state verdict.

**The three-state verdict is decided here**. `noa-old` answered verification with a `bool`, and a
host whose libcrypt would not load produced `False` — indistinguishable from a password that
genuinely does not match. So a change that in fact succeeded was reported as failed, and an operator
was sent to reset a password that was already live. `core.integrations.proxmox.cloudinit` answers
three states, and this module turns the third into an outcome an operator can read:
**verification-unavailable is not verified, and it is not refuted either.**

**What the payload carries, and what it deliberately does not.** The tool returns the
`yopass_url` and nothing else of the secret. `noa-old` also returned the sanitized cloud-init
user-data dump; here nothing derived from the dump enters the payload at all. The reason is
structural rather than fastidious: this payload becomes `tool_runs.result_summary`
(`core.audit.summaries`) and `noa_get_action_result` hands that string to a model, so the crypt
hash of the password NOA just set would reach the LLM through the audit row. It stays in this
frame; what leaves is a verdict.

**No path back to a model opens here, and that is worth stating rather than assuming.** This runner
never reads `request.reason`: cloud-init has no note field, so nothing the reason rule keeps from
the LLM is written onto the target system, and the approve-with-note permission is unused here — the
suspend tool's shape, one system over. Nothing NOA writes to this VM comes back to a model either,
because the only thing written is a password hash and the payload carries no reading of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import structlog

from core.approvals.delta import (
    # `VERIFICATION_UNAVAILABLE` arrives below through `change_target`, the same string from the
    # same definition — `core.approvals.delta` owns all four states.
    VERIFICATION_MISMATCH,
    VERIFICATION_VERIFIED,
    ChangeDelta,
    ChangeOutcome,
)
from core.approvals.execution import ChangeExecutionRequest, ChangeRunner
from core.errors import NoaError
from core.integrations.proxmox.client import ProxmoxClient
from core.integrations.proxmox.cloudinit import (
    CAUSE_PASSWORD_HASH_ABSENT,
    CloudInitPasswordVerification,
    CryptLibraryLoader,
    CryptVerdict,
    _load_crypt_lib,
    cloudinit_carries_password,
    verify_cloudinit_password,
)
from core.secrets.password import _generate_password
from noa_api.mcp_tools.change_target import (
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    WriteFailure,
    uuid_or_none,
)
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.proxmox_password import (
    ERROR_SERVER_UNAVAILABLE,
    EVIDENCE_NODE,
    EVIDENCE_SERVER_ID,
    EVIDENCE_USERNAME,
    EVIDENCE_VMID,
    MESSAGE_SERVER_UNAVAILABLE,
    TOOL_PROXMOX_RESET_VM_PASSWORD,
    text_or_none,
)
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    tool_failure,
    tool_ok,
)

# The evidence carries a value the runner cannot act on. Shares `change_target`'s code, because
# it shares its remedy exactly: ask for the change again, and tell an administrator if it
# recurs. Only the server-unavailable pair above is per-system.
ERROR_EVIDENCE_UNUSABLE: Final = "change_evidence_unusable"
MESSAGE_EVIDENCE_UNUSABLE: Final = (
    "NOA cannot run this change: what was approved was not recorded in a runnable form. Ask for "
    "the change again; contact an administrator if this continues."
)

# The write was accepted and the confirming read says the password on the VM is a different one.
ERROR_POSTFLIGHT_FAILED: Final = "postflight_failed"
MESSAGE_POSTFLIGHT_FAILED: Final = (
    "Proxmox accepted the password change, but the VM's cloud-init data still shows a different "
    "password. The link below holds the password NOA generated; check the VM before using it."
)

# The config write's own task did not reach a terminal state in time.
ERROR_TASK_TIMEOUT: Final = "task_timeout"
MESSAGE_TASK_TIMEOUT: Final = (
    "Proxmox accepted the password change but its task had not finished when NOA stopped "
    "waiting. The link below holds the password NOA generated; check the VM before using it."
)

# The task reached a terminal state and it was a failure.
ERROR_TASK_FAILED: Final = "task_failed"

# One structured event per outcome an operator may have to act on. Identifiers and codes only,
# never the password and never the yopass URL — a URL in a log is a credential in a
# log, because whoever holds the whole link holds the secret.
LOG_RESET_DELIVERY_FAILED: Final = "proxmox_reset_vm_password_delivery_failed"
LOG_RESET_UNVERIFIED: Final = "proxmox_reset_vm_password_unverified"

# `noa-old`'s numbers, kept. The config write answers with a UPID that finishes in
# well under a second in practice; the verification window exists because the rendered user-data
# lags the write by a moment.
TASK_POLL_ATTEMPTS: Final = 30
TASK_POLL_DELAY_SECONDS: Final = 0.5
VERIFICATION_POLL_ATTEMPTS: Final = 20
VERIFICATION_POLL_DELAY_SECONDS: Final = 0.5

logger = structlog.get_logger(__name__)


# --- The runner: reachable only after an operator approved (the cookie/CSRF boundary's far side)
# ---


@dataclass(frozen=True)
class ProxmoxChangeTarget:
    """The endpoint, VM and account an approved reset runs against, from the evidence."""

    client: ProxmoxClient
    server_name: str
    node: str
    vmid: int
    username: str


@dataclass(frozen=True)
class StepFailure:
    """One step of the reset that did not work, and what it implies about the VM.

    `password_may_be_live` is the field that decides whether the yopass URL ships (see the module
    docstring). It is carried on the failure rather than worked out by the caller, because the
    step that failed is the only thing that knows whether Proxmox had already accepted the write.
    """

    error_code: str
    message: str
    password_may_be_live: bool


def build_proxmox_reset_vm_password_runner(
    *,
    context: McpToolContext,
    crypt_loader: CryptLibraryLoader = _load_crypt_lib,
) -> ChangeRunner:
    """The half that resets the password, once an operator approved.

    A closure over the tool context rather than a class, for the suspend tool's reason: what it
    needs is the same session factory, cipher, repositories and delivery seam the tool used, so the
    change goes through the production decrypt site and the production yopass helper rather than
    second copies of either.

    `crypt_loader` is the three-state verdict's seam. It is a constructor argument rather than a
    patched module global so a test can present a host with no libcrypt without leaving the process
    in a state the next test inherits — the setup must not become the subject.

    `request.reason` is on the request — the executor reads it off the row for every approved change
    — and this runner never touches it. Cloud-init has no note field, so nothing the reason rule
    keeps from the LLM leaves NOA here; no path back to a model has an instance on this tool.
    """

    async def run(request: ChangeExecutionRequest) -> ChangeOutcome:
        """Reset this approved request's VM password, and say what happened.

        Resolve from the evidence, generate, deliver, apply, verify. Every refusal answers the
        ordinary tool envelope rather than raising, because the executor's own catch records
        something coarser than what this knew.

        The resolution refusal carries **no delta** — nothing was generated, delivered or
        written, so there is nothing to state. Every path below carries one, and **none of
        them carries a field change**: what this tool alters is a password, so neither side of
        that diff may be rendered at all. `delivered_credential` is the facet instead, and its
        presence carries the same claim the payload's `yopass_url` does — that the password may
        be live on the VM.
        """
        target = await _resolve_change_target(request.evidence, context=context)
        if not isinstance(target, ProxmoxChangeTarget):
            return ChangeOutcome(payload=target)

        # Generated here, in this frame, and never an argument. It is passed to exactly two
        # places — the delivery hop and Proxmox — and reaches no return value, no log and no row.
        password = _generate_password(context.secret_password_length)

        try:
            yopass_url = await context.secret_delivery(username=target.username, password=password)
        except NoaError as exc:
            # The verdict rule's abort: nothing has been written, so the VM is untouched and the old
            # credentials still work. The URL does not exist to withhold.
            logger.warning(
                LOG_RESET_DELIVERY_FAILED,
                tool=TOOL_PROXMOX_RESET_VM_PASSWORD,
                action_request_id=str(request.action_request_id),
                error_code=exc.error_code,
            )
            return ChangeOutcome(
                payload={**tool_failure(exc.error_code, exc.message), **_common(target)},
                # No `delivered_credential`, and the absence is the claim: the store came before
                # any write, so the VM is untouched and there is no copy of the new password to
                # hand anybody. The URL does not exist to withhold.
                delta=_reset_delta(
                    target,
                    verification=VERIFICATION_UNAVAILABLE,
                    verification_cause=exc.error_code,
                ),
            )

        async with target.client:
            failure = await _apply_password(target, password=password)
            if failure is not None and not failure.password_may_be_live:
                # **Proxmox took nothing**, so there is nothing to confirm. The write was refused
                # or its task came back rejected; the old credentials still work and this
                # password is live nowhere, which is what `password_may_be_live` is saying. A
                # crypt compare here could only agree, and ten seconds of polling for that
                # answer is ten seconds an operator waits for one already known.
                return _failure_payload(target, failure, yopass_url=yopass_url)

            # The write *was* accepted — its task never finished, or the drive rewrite failed —
            # so what the VM carries is an open question and the verify below is the only thing
            # that can answer it. It runs on the ordinary path and on this one alike.
            verification = await _verify_password(
                target, password=password, crypt_loader=crypt_loader
            )

        return _reset_outcome(
            target,
            request=request,
            verification=verification,
            yopass_url=yopass_url,
            write_failure=(
                None
                if failure is None
                else WriteFailure(code=failure.error_code, message=failure.message)
            ),
        )

    return run


def build_proxmox_password_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → runner for this module's CHANGE tool."""
    return {
        TOOL_PROXMOX_RESET_VM_PASSWORD: build_proxmox_reset_vm_password_runner(context=context),
    }


# --- Internals ---


async def _resolve_change_target(
    evidence: Mapping[str, Any], *, context: McpToolContext
) -> ProxmoxChangeTarget | ToolPayload:
    """The endpoint and VM an approved reset runs against.

    **From the evidence, never from the arguments.** `server_ref` is a string a model supplied and
    inventory can be edited between a request and its approval; the evidence is the state the
    operator actually saw on the card. Re-resolving here would be a second resolution that can
    disagree with the one the decision rests on.

    Every value is re-checked as it comes back out of JSONB, because a value that no longer
    parses is a request NOA declines instead of guessing at — by the time a runner reads it, an
    operator has typed a reason and pressed Approve, so a guess here is a guess with an
    authorisation attached to it.

    The database session closes before the HTTP hops — the account search's rule — and here it
    matters twice over: the executor's own session is open for the whole of the call.
    """
    node = evidence.get(EVIDENCE_NODE)
    username = evidence.get(EVIDENCE_USERNAME)
    vmid = evidence.get(EVIDENCE_VMID)
    if (
        not isinstance(node, str)
        or not node.strip()
        or not isinstance(username, str)
        or not username.strip()
        or isinstance(vmid, bool)
        or not isinstance(vmid, int)
        or vmid < 1
    ):
        return tool_failure(ERROR_EVIDENCE_UNUSABLE, MESSAGE_EVIDENCE_UNUSABLE)

    server_id = uuid_or_none(evidence.get(EVIDENCE_SERVER_ID))
    if server_id is None:
        return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)

    async with context.session_factory() as session:
        repository = context.proxmox_server_repository_factory(session)
        server = await repository.get_by_id(server_id)
        if server is None:
            return tool_failure(ERROR_SERVER_UNAVAILABLE, MESSAGE_SERVER_UNAVAILABLE)
        server_name = server.name
        client = context.proxmox_client_factory(server, cipher=context.secret_cipher)

    return ProxmoxChangeTarget(
        client=client,
        server_name=server_name,
        node=node.strip(),
        vmid=vmid,
        username=username.strip(),
    )


async def _apply_password(target: ProxmoxChangeTarget, *, password: str) -> StepFailure | None:
    """Write `cipassword`, wait for its task, regenerate the drive. `None` when all three took.

    The three steps differ in what a failure implies, which is the whole reason they are not one
    `if not ok` chain:

    - **the write refused** — Proxmox never took the password, so the old credentials still work
      and the generated one is not live anywhere. This is the verdict rule's named residual case.
    - **the task exited non-`OK`** — the same: Proxmox took the request and then rejected it.
    - **the task did not finish in time, or the regeneration failed** — the write was *accepted*.
      The password may already be on the VM, so the URL has to go out with the failure.

    `regenerate_qemu_cloudinit` goes through `_request_json` rather than `_request_json_task`, so
    a UPID it answers is not polled. That is `noa-old`'s call and the integration layer left the
    decision here explicitly (`client.regenerate_qemu_cloudinit`): the verification below re-reads
    cloud-init until it agrees, which is a stronger check than waiting on the task would be.
    """
    set_result = await target.client.set_qemu_cloudinit_password(target.node, target.vmid, password)
    if set_result.get("ok") is not True:
        return StepFailure(
            error_code=str(set_result.get("error_code") or ERROR_UNKNOWN),
            message=str(set_result.get("message") or "Proxmox cloud-init password update failed"),
            password_may_be_live=False,
        )

    upid = text_or_none(set_result.get("upid"))
    if upid is not None:
        task_failure = await _wait_for_terminal_task(target, upid=upid)
        if task_failure is not None:
            return task_failure

    regenerate_result = await target.client.regenerate_qemu_cloudinit(target.node, target.vmid)
    if regenerate_result.get("ok") is not True:
        return StepFailure(
            error_code=str(regenerate_result.get("error_code") or ERROR_UNKNOWN),
            message=str(
                regenerate_result.get("message") or "Proxmox cloud-init regeneration failed"
            ),
            # The password write already landed; only the drive rewrite did not.
            password_may_be_live=True,
        )
    return None


async def _wait_for_terminal_task(target: ProxmoxChangeTarget, *, upid: str) -> StepFailure | None:
    """Poll one UPID to a terminal state. `None` when it finished successfully.

    Terminality is `status == "stopped"`, or an exit status present while the task is no longer
    running — `client.get_task_status` normalises both to `None` when absent, so an empty string
    cannot read as present.

    A poll that cannot *read* the status is treated as a timeout rather than as a task failure:
    not knowing what a task did is not evidence that it failed, and the write it belongs to has
    already been accepted.
    """
    task_status: str | None = None
    task_exit_status: str | None = None

    for attempt in range(TASK_POLL_ATTEMPTS):
        status_result = await target.client.get_task_status(target.node, upid)
        if status_result.get("ok") is not True:
            break

        task_status = text_or_none(status_result.get("task_status"))
        task_exit_status = text_or_none(status_result.get("task_exit_status"))
        if task_status == "stopped" or (task_exit_status is not None and task_status != "running"):
            if task_exit_status in (None, "OK"):
                return None
            return StepFailure(
                error_code=ERROR_TASK_FAILED,
                message=(
                    f"Proxmox rejected the password change: its task finished with exit status "
                    f"'{task_exit_status}'."
                ),
                # A terminal non-OK task is Proxmox saying it did not apply the write.
                password_may_be_live=False,
            )

        if attempt < TASK_POLL_ATTEMPTS - 1:
            await asyncio.sleep(TASK_POLL_DELAY_SECONDS)

    return StepFailure(
        error_code=ERROR_TASK_TIMEOUT,
        message=MESSAGE_TASK_TIMEOUT,
        password_may_be_live=True,
    )


async def _verify_password(
    target: ProxmoxChangeTarget,
    *,
    password: str,
    crypt_loader: CryptLibraryLoader,
) -> CloudInitPasswordVerification:
    """Is the password NOA generated the one the VM now carries?

    **The library is probed once, up front, and a host without it does not poll.** Ten seconds of
    re-reading a document nobody can compare against is time an operator waits for an answer that
    was already known — and the answer is `unavailable`, not `mismatch`.

    Everything else is polled, because the rendered user-data lags the write: a hash that is
    absent or does not match yet is a reason to look again, while a match is final. A read that
    fails outright ends the loop as `unavailable` too — a document NOA could not fetch has not
    told it anything — silence is not evidence of absence.

    The last verdict wins when the attempts run out, which keeps "we compared and it differed"
    distinguishable from "there was never a hash to compare".
    """
    if crypt_loader() is None:
        return verify_cloudinit_password(None, password, loader=crypt_loader)

    verification = CloudInitPasswordVerification(
        CryptVerdict.UNAVAILABLE, CAUSE_PASSWORD_HASH_ABSENT
    )
    for attempt in range(VERIFICATION_POLL_ATTEMPTS):
        cloudinit_result = await target.client.get_qemu_cloudinit(target.node, target.vmid)
        dump_result = await target.client.get_qemu_cloudinit_dump_user(target.node, target.vmid)
        if cloudinit_result.get("ok") is not True or dump_result.get("ok") is not True:
            return CloudInitPasswordVerification(
                CryptVerdict.UNAVAILABLE, CAUSE_PASSWORD_HASH_ABSENT
            )

        if cloudinit_carries_password(cloudinit_result.get("data")):
            verification = verify_cloudinit_password(
                dump_result.get("data"), password, loader=crypt_loader
            )
            if verification.verdict is CryptVerdict.MATCH:
                return verification

        if attempt < VERIFICATION_POLL_ATTEMPTS - 1:
            await asyncio.sleep(VERIFICATION_POLL_DELAY_SECONDS)

    return verification


def _reset_delta(
    target: ProxmoxChangeTarget,
    *,
    verification: str,
    verification_cause: str | None = None,
    delivered_credential: str | None = None,
) -> ChangeDelta:
    """This change's before→after, as the runner that ran it states it.

    **No `changed_fields`, on any branch, and that is the point of the facet this uses instead.**
    The value that moved is a password: the old one NOA never held, the new one it must not
    record, and a crypt hash of either is exactly what stays in this frame rather than reaching a
    payload a model can read. So the delta says a credential was delivered and whether the change
    was confirmed, and says nothing about the value on either side.

    `delivered_credential` present is the claim that the password may be live on the VM, which is
    the same rule its `yopass_url` twin follows in the payload — a failure carrying no link is
    NOA saying the VM never got this password, and that difference is what a reader acts on.
    """
    return ChangeDelta(
        identity=_common(target),
        verification=verification,
        verification_cause=verification_cause,
        delivered_credential=delivered_credential,
    )


def _reset_outcome(
    target: ProxmoxChangeTarget,
    *,
    request: ChangeExecutionRequest,
    verification: CloudInitPasswordVerification,
    yopass_url: str,
    write_failure: WriteFailure | None = None,
) -> ChangeOutcome:
    """What the change did, read off the crypt compare.

    Three answers, and the middle one is the three-state verdict:

    1. **verified** — the VM carries this password. `verified: true`, and the link goes out.
    2. **unavailable** — no comparison happened, and the cause says which kind of nothing it was.
       `status: changed` with `verified: false` and `verification: unavailable`, never a bare
       `false` that reads as a measurement. Reporting this as failed would send an operator to
       reset a password that is already live; reporting it as verified would be the fabrication
       the verdict rule exists to stop.
    3. **mismatch** — the VM carries a *different* password. That is a failure, and the link still
       goes out because Proxmox accepted the write and the password may be live anyway.

    **`write_failure` is how a step that failed after the write landed reaches here.** It is
    `None` on the ordinary path and every branch reads as it did before. When it is set, the
    password write was accepted and something after it was not — the task never finished, or the
    drive rewrite was refused — so the crypt compare is the only thing that can say what the VM
    now carries, and the failure shapes the sentence so a confirmation is never read as a call
    that answered.

    **A match needs no hedge on any branch here, and this tool is the only one that can say
    that.** The comparison is against a password generated in this frame and never written down,
    so a VM carrying it is a VM that took NOA's write — nobody else could have set that exact
    value. Where a tool comparing a shared field would have to report "the state matches but the
    remote said it did nothing", this one reports a plain confirmation.

    A **mismatch** does not get the same treatment, and that is the asymmetry rather than an
    oversight: a rendered document that has not caught up yet reads exactly like one that never
    will, so a disagreeing compare after a step that never answered is `unavailable` with that
    step's own cause, and only a step the remote explicitly refused makes it a measurement.

    The `yopass_url` is the only thing here derived from the secret. Nothing read out
    of the cloud-init dump reaches this payload: it becomes `result_summary`, and
    `noa_get_action_result` hands that to a model.
    """
    common = _common(target)
    where = f"`{target.username}` on VM {target.vmid} ({target.server_name})"
    if verification.verdict is CryptVerdict.MATCH:
        return ChangeOutcome(
            payload=tool_ok(
                **common,
                status=STATUS_CHANGED,
                verified=True,
                yopass_url=yopass_url,
                message=(
                    (
                        f"The cloud-init password for {where} was changed and confirmed. "
                        if write_failure is None
                        else f"Proxmox {write_failure.verb} a step of the cloud-init password "
                        f"change for {where} (`{write_failure.code}`), so NOA compared the "
                        "password it generated against the VM: the VM carries it. "
                    )
                    + "Give the operator the link; it opens once. The guest reads the new "
                    "password when its cloud-init drive is next read."
                ),
            ),
            delta=_reset_delta(
                target,
                verification=VERIFICATION_VERIFIED,
                delivered_credential=yopass_url,
            ),
        )

    if verification.verdict is CryptVerdict.UNAVAILABLE:
        logger.warning(
            LOG_RESET_UNVERIFIED,
            tool=TOOL_PROXMOX_RESET_VM_PASSWORD,
            action_request_id=str(request.action_request_id),
            node=target.node,
            vmid=target.vmid,
            cause=verification.cause,
            write_failure=None if write_failure is None else write_failure.code,
        )
        # The cause names the **compare** on both paths: it answers why NOA holds no
        # measurement, while the failed step's own code sits on the envelope beside it.
        return ChangeOutcome(
            payload=(
                tool_ok(
                    **common,
                    status=STATUS_CHANGED,
                    verified=False,
                    verification=VERIFICATION_UNAVAILABLE,
                    verification_cause=verification.cause,
                    yopass_url=yopass_url,
                    message=(
                        f"Proxmox accepted the new cloud-init password for {where}, but NOA "
                        "could not confirm it took. Give the operator the link and check the VM "
                        "before relying on it."
                    ),
                )
                if write_failure is None
                else {
                    **tool_failure(
                        write_failure.code,
                        f"Proxmox {write_failure.verb} a step of the cloud-init password change "
                        f"for {where} (`{write_failure.code}`), and NOA could not compare the "
                        "password against the VM either. Give the operator the link and check "
                        "the VM before relying on it.",
                    ),
                    **common,
                    "verified": False,
                    "yopass_url": yopass_url,
                }
            ),
            delta=_reset_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=verification.cause,
                delivered_credential=yopass_url,
            ),
        )

    if write_failure is not None and not write_failure.refused:
        # The compare disagrees and the step it follows never answered. A cloud-init document
        # that has not been re-rendered yet reads exactly like one that never will, so this is
        # `unavailable` with that step's own cause rather than a measurement.
        return ChangeOutcome(
            payload={
                **tool_failure(
                    write_failure.code,
                    f"Proxmox did not answer a step of the cloud-init password change for "
                    f"{where} (`{write_failure.code}`), and the VM does not carry the new "
                    "password yet. Give the operator the link and check the VM before relying "
                    "on it.",
                ),
                **common,
                "verified": False,
                "yopass_url": yopass_url,
            },
            delta=_reset_delta(
                target,
                verification=VERIFICATION_UNAVAILABLE,
                verification_cause=write_failure.code,
                delivered_credential=yopass_url,
            ),
        )

    return ChangeOutcome(
        payload={
            **tool_failure(ERROR_POSTFLIGHT_FAILED, MESSAGE_POSTFLIGHT_FAILED),
            **common,
            "verified": False,
            "yopass_url": yopass_url,
        },
        # A measurement: the VM carries a *different* password. The link still goes out, because
        # Proxmox accepted the write and the generated password may be live anyway.
        delta=_reset_delta(
            target,
            verification=VERIFICATION_MISMATCH,
            delivered_credential=yopass_url,
        ),
    )


def _failure_payload(
    target: ProxmoxChangeTarget, failure: StepFailure, *, yopass_url: str
) -> ChangeOutcome:
    """A failed step, with the link attached exactly when the password may already be live.

    The absent field is doing work here rather than saving bytes: a failure carrying no
    `yopass_url` is NOA saying the VM never got this password, and a caller — the card, the
    audit surface, a model reading `noa_get_action_result` — can act on that difference.

    The delta beside it takes the same decision from the same field, so the two cannot disagree
    about whether a credential was delivered.
    """
    payload: ToolPayload = {
        **tool_failure(failure.error_code, failure.message),
        **_common(target),
    }
    if failure.password_may_be_live:
        payload["yopass_url"] = yopass_url
    return ChangeOutcome(
        payload=payload,
        delta=_reset_delta(
            target,
            verification=VERIFICATION_UNAVAILABLE,
            verification_cause=failure.error_code,
            delivered_credential=yopass_url if failure.password_may_be_live else None,
        ),
    )


def _common(target: ProxmoxChangeTarget) -> ToolPayload:
    """The identifiers every answer from this runner carries. No credential material."""
    return {
        "server": target.server_name,
        "node": target.node,
        "vmid": target.vmid,
        "username": target.username,
    }


__all__ = [
    "ERROR_EVIDENCE_UNUSABLE",
    "ERROR_POSTFLIGHT_FAILED",
    "ERROR_SERVER_UNAVAILABLE",
    "ERROR_TASK_FAILED",
    "ERROR_TASK_TIMEOUT",
    "LOG_RESET_DELIVERY_FAILED",
    "LOG_RESET_UNVERIFIED",
    "MESSAGE_EVIDENCE_UNUSABLE",
    "MESSAGE_POSTFLIGHT_FAILED",
    "MESSAGE_SERVER_UNAVAILABLE",
    "MESSAGE_TASK_TIMEOUT",
    "TASK_POLL_ATTEMPTS",
    "TASK_POLL_DELAY_SECONDS",
    "VERIFICATION_POLL_ATTEMPTS",
    "VERIFICATION_POLL_DELAY_SECONDS",
    "ProxmoxChangeTarget",
    "StepFailure",
    "build_proxmox_password_runners",
    "build_proxmox_reset_vm_password_runner",
]
