"""The half that runs after an operator approved — the allowlist-remove tool's runner
(the far side of the cookie/CSRF boundary).

`test_whm_tools_firewall_allowlist_remove.py` covers the tool, which changes nothing. This
covers the thing that does, and the two are separate files because the boundary between them is
the design's own: a runner is reachable only from `core.approvals.execution`, never from the MCP
path, and it is driven here with a `ChangeExecutionRequest` built the way the executor builds one
(`support.whm_firewall_change`).

Four claims carry the weight.

**Both allow lists are asked, in `noa-old`'s order**. `csf -tra` drops a temporary
allow and `csf -ar` the `csf.allow` entry; an address lives in one of them, so the other always
reports "not in that list" and that is the ordinary answer rather than a failure.

**Nothing here is required to succeed, so the postflight is the only authority.** The
release-and-allow tool could keep one strict command — the allow entry the operator asked to *exist*
— and a removal has no equivalent. The exception is a sudo-rights refusal, which is never "the entry
was not there": sudoers can permit the probe and refuse the write, and tolerating that would report
a change that could not run as a change that found nothing to do.

**"Still allowlisted" is read from `allow_entry`, not from the verdict.** Both backends resolve
block-over-allow, so on an address csf also denies the verdict cannot see a surviving allow
entry. Its own case here, because it is the difference between reporting a failed removal and
reporting it as done.

**The reason goes nowhere, and this tool never wrote one.** What it deletes is an entry the
release-and-allow tool wrote, which carries `noa:<id> <reason>` — so the doors back are a backend
failure message that quotes it and the postflight lines the verdict is read from, and both are
closed here.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from core.approvals.execution import build_receipt
from core.audit.summaries import result_summary
from core.errors import NoaError
from core.integrations.whm.firewall_gate import ERROR_NO_FIREWALL_BACKEND
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE
from noa_api.mcp_tools.whm_firewall import NOA_COMMENT_MARKER
from noa_api.mcp_tools.whm_firewall_allowlist import (
    ERROR_ALLOWLIST_REMOVE_FAILED,
    EVIDENCE_FIREWALL,
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    build_whm_firewall_allowlist_remove_runner,
)
from noa_api.mcp_tools.whm_firewall_change_common import (
    BACKEND_DISPLAY_NAMES,
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_SERVER_UNAVAILABLE,
)
from support.action_decisions import REASON
from support.change_delta import payload_runner
from support.remote_exec import SUDO_DENIED_STDERR, command_result
from support.secrets import build_cipher
from support.whm_firewall import (
    CSF_ALLOW_AND_DENY_OUTPUT,
    CSF_ALLOW_LINE,
    CSF_ALLOW_REMOVE,
    CSF_CLEAN_OUTPUT,
    CSF_NOT_IN_LIST,
    CSF_READ,
    CSF_TEMP_ALLOW_REMOVE,
    IMUNIFY_CLEAN,
    IMUNIFY_DELETE,
    IMUNIFY_NOT_IN_LIST,
    IMUNIFY_WHITE,
    IMUNIFY_WHITE_AND_DROP,
    SERVER_NAME,
    TARGET,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    csf_step,
    imunify_answer,
    imunify_backend,
    preflight_server,
)
from support.whm_firewall_change import (
    csf_commands,
    imunify_commands,
    release_context,
    removal_request,
    removed_box,
)


async def test_the_runner_clears_both_allow_lists_then_re_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both lists, in `noa-old`'s order, and the confirming read last.

    Both commands are sent because NOA does not know which list holds the entry: the
    release-and-allow tool writes temporary allows, an operator's own hand-added ones are
    permanent. Asserted as a sequence,
    which is what a dropped command breaks and what a membership test would not.
    """
    fixture, fake = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert [csf_step(command) for command in csf_commands(fake)] == [
        CSF_TEMP_ALLOW_REMOVE,
        CSF_ALLOW_REMOVE,
        CSF_READ,
    ]
    imunify = imunify_commands(fake)
    assert IMUNIFY_DELETE in imunify[0] and "--purpose white" in imunify[0]
    assert payload["ok"] is True


async def test_a_confirmed_removal_says_so_once_and_names_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The success shape: one outcome, because a removal is one claim.

    The release-and-allow tool keeps `released` and `allowlisted` apart because it makes two
    claims. This makes one, and inventing a second boolean to mirror it would be a field nobody
    measured.
    """
    fixture, _ = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["removed"] is True
    assert payload["verified"] is True
    # The card's heading, written here rather than derived from the tool name: `Firewall
    # Allowlist Remove` names the machinery, and this names what happened to the address.
    assert payload["headline"] == f"Allow entry removed — {TARGET}"
    assert payload["server"] == SERVER_NAME
    assert payload["target"] == TARGET


async def test_the_runner_acts_on_the_server_the_card_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Context persisted at gate time: inventory can change between a request and its approval, and
    `server_ref` is a string the model supplied. The evidence carries the id of the machine the
    preflight read and the operator saw, so that is what the change reaches — asserted on the
    host the transport was
    handed, which is the only way "it ran somewhere else" would show."""
    cipher = build_cipher()
    alpha = preflight_server(SERVER_NAME, cipher=cipher)
    beta = preflight_server("beta", cipher=cipher)
    fixture, fake = release_context(
        monkeypatch, box=removed_box(), cipher=cipher, servers=[alpha, beta]
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    # The arguments name the other server; only the evidence names alpha.
    await runner(removal_request(server_id=alpha.id, server_ref="beta"))

    assert {run.config.host for run in fake.runs} == {f"{SERVER_NAME}.example.net"}


async def test_a_removal_that_did_not_take_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address is still on an allow list after the removal ran. Reporting that as done is the
    fabrication the postflight exists to stop."""
    fixture, _ = release_context(
        monkeypatch, box=removed_box(csf_after=CSF_ALLOW_LINE, imunify_after=IMUNIFY_WHITE)
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_ALLOWLIST_REMOVE_FAILED
    assert payload["headline"] == f"Allow entry still there — {TARGET}"
    assert payload["removed"] is False


async def test_a_surviving_allow_entry_is_caught_even_when_a_block_outranks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the combined verdict cannot answer, and the reason `allow_entry` exists.

    Both backends resolve a conflict block-first, so this postflight reads `blocked` — and the
    `csf.allow` line it also matched is exactly what the removal was supposed to delete. A
    runner that asked the verdict "is it still allowlisted?" would be told no by the deny entry
    and would report a failed removal as done.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=removed_box(csf_after=CSF_ALLOW_AND_DENY_OUTPUT, imunify_after=IMUNIFY_WHITE_AND_DROP),
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_ALLOWLIST_REMOVE_FAILED
    assert payload["removed"] is False


async def test_a_blocked_address_with_no_allow_entry_is_a_clean_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the case above.

    Without it, "a blocked postflight fails the removal" would pass just as well against a
    runner that treated every `blocked` verdict as a surviving allow entry — which would fail
    every removal on an address that is also denied, the ordinary state after a re-block.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=removed_box(
            csf_after=f"Found {TARGET} in /etc/csf/csf.deny", imunify_after=IMUNIFY_CLEAN
        ),
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["removed"] is True


async def test_a_backend_that_did_not_answer_leaves_the_change_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unknown-when-silent rule on a CHANGE, which is where the zero-backend error's bound runs
    out.

    CSF says the address is clean; Imunify's confirming read is unreadable. Answering "removed"
    from the half that spoke is the fabrication the dual-backend firewall read was written to
    stop, one side worse — and
    answering "failed" would send an operator to repeat a removal that already took.
    So: it happened, it is not verified, and the silent backend is named.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer("not json at all")),
        ),
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["unanswered_backends"] == ["imunify"]
    # The heading is the confirmed one, because the commands were accepted — what is unconfirmed
    # is stated in the sentence, and the corner reads it off the verification state.
    assert payload["headline"] == f"Allow entry removed — {TARGET}"
    assert BACKEND_DISPLAY_NAMES["imunify"] in str(payload["message"])
    # The claim is not made, because it was not measured.
    assert "removed" not in payload


async def test_a_refused_backend_beside_a_silent_one_still_names_the_silent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch that now consults the read is still not allowed to collapse the sources.

    csf refused its removal and Imunify answered the confirming read with nothing readable. The
    refusal is what the envelope reports, and the silent backend is *still named* beside it —
    a source that cannot answer gets named, and a code from the other backend is not a
    substitute for saying which one went quiet.

    Nothing is claimed about the entry either: with one source silent there is no reading to
    hold the refusal up against, so this stays an unknown outcome rather than a measurement.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_ALLOW_LINE),
                mutations={
                    CSF_ALLOW_REMOVE: command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)
                },
            ),
            imunify=imunify_backend(imunify_answer("not json at all")),
        ),
        ssh_username="operator",
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == SSH_SUDO_REQUIRED_CODE
    assert payload["unanswered_backends"] == ["imunify"]
    assert payload["headline"] == f"Removal failed — {TARGET}"
    # The sentence names the source in the words the product uses; the payload key beside it
    # keeps the raw name an administrator greps for. Named either way, never counted.
    assert BACKEND_DISPLAY_NAMES["imunify"] in str(payload["message"])
    assert "removed" not in payload


async def test_a_backend_that_answered_alone_is_still_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the case above, and the subset-naming rule's own bound.

    One backend *installed* is not one backend silent: a box without Imunify is answered in full
    by CSF, and refusing to verify there would make the unverified branch fire for every
    CSF-only server — which is most of them.
    """
    fixture, _ = release_context(
        monkeypatch, box=FakeFirewallBox(csf=csf_backend(csf_answer(CSF_CLEAN_OUTPUT)))
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["removed"] is True
    assert payload["verified"] is True
    assert payload["unanswered_backends"] == []


async def test_a_step_that_finds_nothing_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case, and the one a strict exit-code check would refuse.

    An entry is on the temporary allow list or the permanent one, never both, so one of the two
    csf commands always reports "not in that list" — and Imunify refuses a delete for an entry it
    does not hold. Treating either as a failure would refuse every removal this tool exists for.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_CLEAN_OUTPUT),
                mutations={CSF_ALLOW_REMOVE: command_result(exit_code=1, stdout=CSF_NOT_IN_LIST)},
            ),
            imunify=imunify_backend(
                imunify_answer(IMUNIFY_CLEAN),
                mutations={IMUNIFY_DELETE: command_result(exit_code=1, stdout=IMUNIFY_NOT_IN_LIST)},
            ),
        ),
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["removed"] is True


async def test_a_step_sudo_refuses_is_not_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sudo-prefix rule, and the hole tolerating everything would otherwise leave.

    sudoers can permit `csf -v` — which is what the availability probe runs, so the box looks
    usable — and refuse `csf -ar`. Every command would then report "not in that list", and a
    change that could not run would read as a change that found nothing to do. `ssh_sudo_required`
    names a remedy; a tolerated exit code names none.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_CLEAN_OUTPUT),
                mutations={
                    CSF_ALLOW_REMOVE: command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)
                },
            ),
        ),
        ssh_username="operator",
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == SSH_SUDO_REQUIRED_CODE
    assert payload["backends"]["csf"]["ok"] is False


async def test_a_server_that_vanished_after_approval_is_refused_before_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed on the far side of the boundary too: the row the operator approved against is
    gone, so the change does not run against whatever `server_ref` resolves to today."""
    fixture, fake = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert fake.commands == []


async def test_evidence_without_a_usable_target_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address round-tripped through JSONB, and a value that no longer parses is a request
    NOA declines rather than guesses at — by now an operator has already pressed Approve."""
    fixture, fake = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id, target="  "))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert fake.commands == []


async def test_zero_usable_backends_after_approval_raises_for_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-backend error on the runner side, where the silent no-op would be an approved
    change.

    A runner is not decorated with `sanitize_tool_errors` — the executor catches `NoaError` and
    keeps its code (`core.approvals.execution`), so the receipt names `no_firewall_backend`
    rather than a generic failure. What must not happen is an `ok: True` from a box NOA could not
    drive at all.
    """
    fixture, _ = release_context(monkeypatch, box=FakeFirewallBox())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    with pytest.raises(NoaError) as raised:
        await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert raised.value.error_code == ERROR_NO_FIREWALL_BACKEND


async def test_a_non_root_user_escalates_every_firewall_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sudo-prefix rule is a biconditional, and the runner is where it has the most to lose.

    Every command the change sends — probes, both removals and the confirming read — runs under
    `sudo -n` when the resolved SSH user is not root. One unescalated write is a change that
    silently does not happen.
    """
    fixture, fake = release_context(monkeypatch, box=removed_box(), ssh_username="operator")
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert fake.commands
    assert all("sudo -n" in command for command in fake.commands)


async def test_a_root_user_escalates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the biconditional: root running under `sudo` is the same bug."""
    fixture, fake = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert fake.commands
    assert all("sudo" not in command for command in fake.commands)


# --------------------------------------------------------------------------------------
# The reason field: nothing is written out, and nothing comes back
# --------------------------------------------------------------------------------------


async def test_no_command_carries_a_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-reason-field rule is not used on this side, and that is worth asserting rather than
    assuming.

    `csf -tra` / `-ar` and Imunify's delete take no comment, so a removal writes none of the
    operator's words anywhere — the marker appears in no command NOA sends. Without this, "the
    reason does not come back" would be true of a tool that had quietly started sending it.
    """
    fixture, fake = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))
    request = removal_request(server_id=fixture.servers.servers[0].id)

    await runner(request)

    sent = " ".join(fake.commands)
    assert REASON not in sent
    assert NOA_COMMENT_MARKER not in sent


async def test_the_runner_payload_never_carries_the_reason_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-path-back rule's audit half: `result_summary` is derived from this payload, and
    `noa_get_action_result` returns the summary to a model.

    The reason is on the `ChangeExecutionRequest` — the executor reads it off the row for every
    approved change — so it is in front of this runner throughout, and the entry it just
    deleted carried it too. Asserted on the derived summary as well as on the payload, because
    the summary is the thing a model actually reads — the no-path-back rule's own "on the
    serialized result and on the derived summary" requirement.
    """
    fixture, _ = release_context(
        monkeypatch, box=removed_box(csf_after=f"{CSF_ALLOW_LINE} noa:{uuid4()} {REASON}")
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    assert REASON not in json.dumps(payload)
    assert REASON not in (result_summary(payload) or "")


async def test_the_postflight_evidence_never_reaches_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The door this tool opens widest.

    The confirming read is `csf -g`, and on a failed removal its output is the allow entry the
    release-and-allow tool wrote — marker, reason and all. Those lines are read for a verdict and go
    no further: the after-state says what changed, not what csf printed. Asserted on the failing
    branch, because that is the branch where the lines exist.
    """
    surviving = f"{CSF_ALLOW_LINE} noa:{uuid4()} {REASON}"
    fixture, _ = release_context(monkeypatch, box=removed_box(csf_after=surviving))
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(removal_request(server_id=fixture.servers.servers[0].id))

    rendered = json.dumps(payload)
    assert payload["error_code"] == ERROR_ALLOWLIST_REMOVE_FAILED
    assert REASON not in rendered
    assert CSF_ALLOW_LINE not in rendered


async def test_a_backend_failure_message_is_cut_before_it_reaches_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V96b again, through the door a failure opens.

    A backend that refuses a removal frequently quotes the entry it could not remove — and that
    entry is the one the release-and-allow tool wrote, comment included. The message becomes
    `tool_runs.result_summary`, which `noa_get_action_result` hands to a model, so it is cut on the
    way into the payload rather than trusted to be harmless.

    The marker is the real request's, because a cut aimed at a marker this call never saw would
    pass without cutting anything.
    """
    request_id = uuid4()
    marker = f"{NOA_COMMENT_MARKER}{request_id}"
    quoted = f"csf: cannot remove '{TARGET} # {marker} {REASON}' from csf.allow"
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_CLEAN_OUTPUT),
                mutations={
                    CSF_ALLOW_REMOVE: command_result(
                        exit_code=1, stdout=quoted, stderr=SUDO_DENIED_STDERR
                    )
                },
            ),
        ),
        ssh_username="operator",
    )
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))

    payload = await runner(
        removal_request(server_id=fixture.servers.servers[0].id, action_request_id=request_id)
    )

    rendered = json.dumps(payload)
    assert payload["ok"] is False
    assert REASON not in rendered
    # The pointer survives: it is what tells a human on the box which approval this entry is.
    assert marker in rendered


# --------------------------------------------------------------------------------------
# The one-commit receipt rule, DECISIONS section 6.5: the receipt an operator reads
# --------------------------------------------------------------------------------------


async def test_the_receipt_keeps_the_before_and_after_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-commit receipt rule, through the real `build_receipt`.

    `before` is the gate's own preflight — the allow entry the operator decided against, read
    with its own comment intact, because a receipt is behind the operator's cookie and is one of
    the two surfaces the no-path-back rule deliberately does *not* cut. `after` is what the change
    answered.
    """
    fixture, _ = release_context(monkeypatch, box=removed_box())
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))
    request = removal_request(server_id=fixture.servers.servers[0].id)

    payload = await runner(request)
    receipt = build_receipt(evidence=request.evidence, payload=payload)

    assert receipt["ok"] is True
    assert receipt["before"][EVIDENCE_FIREWALL]["combined_verdict"] == "allowlisted"
    assert REASON in " ".join(receipt["before"][EVIDENCE_FIREWALL]["matches"])
    assert receipt["after"]["removed"] is True
    assert receipt["after"]["target"] == TARGET


async def test_a_failed_change_keeps_its_before_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-commit receipt rule: a receipt with a before-state and no working after-state IS the
    record of a change
    that did not complete — so the half the operator authorised against survives the failure."""
    fixture, _ = release_context(monkeypatch, box=removed_box(csf_after=CSF_ALLOW_LINE))
    runner = payload_runner(build_whm_firewall_allowlist_remove_runner(context=fixture.context))
    request = removal_request(server_id=fixture.servers.servers[0].id)

    receipt = build_receipt(evidence=request.evidence, payload=await runner(request))

    assert receipt["ok"] is False
    assert receipt["error_code"] == ERROR_ALLOWLIST_REMOVE_FAILED
    assert receipt["before"][EVIDENCE_FIREWALL]["combined_verdict"] == "allowlisted"
    assert receipt["after"]["removed"] is False
