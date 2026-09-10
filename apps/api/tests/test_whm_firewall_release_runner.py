"""The half that runs after an operator approved — T25's runner (V22's far side).

`test_whm_tools_firewall_release_and_allow.py` covers the tool, which changes nothing. This
covers the thing that does, and the two are separate files because the boundary between them is
the design's own: a runner is reachable only from `core.approvals.execution`, never from the MCP
path, and it is driven here with a `ChangeExecutionRequest` built the way the executor builds one
(`support.whm_firewall_change`).

Four claims carry the weight.

**The two outcomes stay apart** (DECISIONS §6.5). "Released" and "allowlisted" are separate
booleans and separate failure codes, so a receipt cannot say "done" over a half-finished change —
and one instant feeds both backends, because csf takes a TTL in seconds and Imunify an absolute
epoch.

**Release before allow** (`core.integrations.whm.csf`). CSF resolves a conflict block-first, so
an allow written before the deny entry is removed buys nothing and reads as success. Asserted as
a sequence, which is what a reordering breaks and what a membership test would not.

**A postflight that did not answer is not a verified change** (§V.86, §V.62's rule). §V.57 bounds
the zero-backend case; a *partial* answer is where the fabrication lives, and on an approved
CHANGE it is worse than on a READ. Its negative control is here too: one backend *installed* is
not one backend silent.

**The operator's reason goes out and does not come back** (C8, §V.43, §V.96). The write is
asserted here — on both backends, because a comment written on one and not the other is a bound
held on one side only — along with the two doors back: the runner's payload, and a backend
failure message that quotes the command it could not run. The READ that would otherwise echo it
is closed in `test_whm_tools_firewall_preflight.py`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from core.approvals.execution import build_receipt
from core.audit.summaries import result_summary
from core.errors import NoaError
from core.integrations.whm.firewall_gate import ERROR_NO_FIREWALL_BACKEND
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE
from noa_api.mcp_tools.whm_firewall import NOA_COMMENT_MARKER
from noa_api.mcp_tools.whm_firewall_change import (
    ERROR_ALLOW_FAILED,
    ERROR_EVIDENCE_UNUSABLE,
    ERROR_RELEASE_FAILED,
    ERROR_SERVER_UNAVAILABLE,
    EVIDENCE_FIREWALL,
    MAX_DURATION_MINUTES,
    STATUS_CHANGED,
    VERIFICATION_UNAVAILABLE,
    build_whm_firewall_release_runner,
)
from support.action_decisions import REASON
from support.change_delta import payload_runner
from support.remote_exec import SUDO_DENIED_STDERR, command_result
from support.secrets import build_cipher
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    CSF_DENY_RELEASE,
    CSF_NOT_IN_LIST,
    CSF_READ,
    CSF_TEMP_ALLOW,
    CSF_TEMP_RELEASE,
    IMUNIFY_ADD,
    IMUNIFY_CLEAN,
    IMUNIFY_DELETE,
    IMUNIFY_NOT_IN_LIST,
    IMUNIFY_WHITE,
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
    DURATION_MINUTES,
    csf_commands,
    execution_request,
    imunify_commands,
    release_context,
    released_box,
)


async def test_the_runner_releases_then_allows_on_both_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order is the invariant, not the command list (T25, `core.integrations.whm.csf`).

    CSF resolves a conflict block-first, so an allow written before the deny entry is removed
    buys nothing and reads as success. Asserted as a sequence per backend, which is what a
    reordering breaks and what a set membership test would not.
    """
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert [csf_step(command) for command in csf_commands(fake)] == [
        CSF_TEMP_RELEASE,
        CSF_DENY_RELEASE,
        CSF_TEMP_ALLOW,
        CSF_READ,
    ]
    imunify = imunify_commands(fake)
    assert IMUNIFY_DELETE in imunify[0] and IMUNIFY_ADD in imunify[1]
    assert payload["ok"] is True


async def test_the_two_outcomes_are_reported_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """DECISIONS §6.5: one approval, a two-part receipt, and never a single "done".

    Two booleans rather than one status word, because "we let it through the deny list" and "we
    put it on the allow list" are two claims and an operator reading a receipt has to be able to
    see which of them is false.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["released"] is True
    assert payload["allowlisted"] is True
    assert payload["verified"] is True
    assert payload["server"] == SERVER_NAME
    assert payload["target"] == TARGET


async def test_the_after_state_carries_the_resolved_expiry_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V77: the after-state shows *when*, not merely how long was asked for.

    Asserted as a property rather than against a literal, because the value is clock-stamped and
    an equality on it would either be impossible or would have to freeze the clock and stop
    testing the arithmetic. The window either side is generous on purpose — what is
    being separated is 137 minutes from a default, not one second from another.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    before = datetime.now(UTC)
    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))
    after = datetime.now(UTC)

    expires_at = datetime.fromisoformat(payload["expires_at"])
    assert payload["duration_minutes"] == DURATION_MINUTES
    assert before.timestamp() + DURATION_MINUTES * 60 <= expires_at.timestamp()
    assert expires_at.timestamp() <= after.timestamp() + DURATION_MINUTES * 60


async def test_both_backends_are_given_the_same_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One instant, two spellings of it — csf takes a TTL in seconds, Imunify an absolute epoch.

    Deriving them separately is how the two backends end up disagreeing by however long the first
    SSH hop took, and the disagreement is invisible until an operator wonders why one list
    expired before the other.
    """
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    expires_at = datetime.fromisoformat(payload["expires_at"])
    allow = next(command for command in csf_commands(fake) if csf_step(command) == CSF_TEMP_ALLOW)
    assert f" {DURATION_MINUTES * 60} " in allow

    epoch = _flag_value(imunify_commands(fake)[1], "--expiration")
    assert int(epoch) == int(expires_at.timestamp())


async def test_the_runner_acts_on_the_server_the_card_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V33: inventory can change between a request and its approval, and `server_ref` is a string
    the model supplied. The evidence carries the id of the machine the preflight read and the
    operator saw, so that is what the change reaches — asserted on the host the transport was
    handed, which is the only way "it ran somewhere else" would show."""
    cipher = build_cipher()
    alpha = preflight_server(SERVER_NAME, cipher=cipher)
    beta = preflight_server("beta", cipher=cipher)
    fixture, fake = release_context(
        monkeypatch, box=released_box(), cipher=cipher, servers=[alpha, beta]
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    # The arguments name the other server; only the evidence names alpha.
    await runner(execution_request(server_id=alpha.id, server_ref="beta"))

    assert {run.config.host for run in fake.runs} == {f"{SERVER_NAME}.example.net"}


async def test_a_release_that_did_not_take_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address is still blocked after the release ran. Reporting that as done is the
    fabrication the postflight exists to stop, and the code names *which* half failed."""
    fixture, _ = release_context(monkeypatch, box=released_box(csf_after=CSF_DENY_LINE))
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_RELEASE_FAILED
    assert payload["released"] is False
    assert payload["allowlisted"] is False


async def test_an_allow_that_did_not_take_is_a_different_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing blocks it and nothing allows it either: the release took and the allow did not.

    The distinction is the whole of DECISIONS §6.5's "do not collapse the two outcomes" — one
    code sends an operator to the deny lists and the other to the allow lists.
    """
    fixture, _ = release_context(
        monkeypatch, box=released_box(csf_after=CSF_CLEAN_OUTPUT, imunify_after=IMUNIFY_CLEAN)
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_ALLOW_FAILED
    assert payload["released"] is True
    assert payload["allowlisted"] is False


async def test_a_backend_that_did_not_answer_leaves_the_change_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V86 on a CHANGE, which is where V57's zero-case bound runs out.

    CSF says the address is allowed; Imunify's confirming read is unreadable. Answering "released
    and allowed" from the half that spoke is exactly `noa-old`'s fabrication, one side worse —
    and answering "failed" would send an operator to repeat a release that already took (V62's
    rule). So: it happened, it is not verified, and the silent backend is named.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_ALLOW_LINE)),
            imunify=imunify_backend(imunify_answer("not json at all")),
        ),
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["status"] == STATUS_CHANGED
    assert payload["verified"] is False
    assert payload["verification"] == VERIFICATION_UNAVAILABLE
    assert payload["unanswered_backends"] == ["imunify"]
    # Neither claim is made, because neither was measured.
    assert "released" not in payload
    assert "allowlisted" not in payload


async def test_a_backend_that_answered_alone_is_still_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the case above, and V86's own bound.

    One backend *installed* is not one backend silent: a box without Imunify is answered in full
    by CSF, and refusing to verify there would make the unverified branch fire for every
    CSF-only server — which is most of them.
    """
    fixture, _ = release_context(
        monkeypatch, box=FakeFirewallBox(csf=csf_backend(csf_answer(CSF_ALLOW_LINE)))
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["unanswered_backends"] == []


async def test_a_release_step_that_finds_nothing_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case, and the one a strict exit-code check would refuse.

    An address held only by a temporary ban has no `csf.deny` line, and Imunify refuses a delete
    for an entry it does not hold — both exit non-zero. Treating either as a failure would break
    the release this tool exists for. The allow still has to succeed, and the postflight is what
    decides whether the change took.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_ALLOW_LINE),
                mutations={CSF_DENY_RELEASE: command_result(exit_code=1, stdout=CSF_NOT_IN_LIST)},
            ),
            imunify=imunify_backend(
                imunify_answer(IMUNIFY_WHITE),
                mutations={IMUNIFY_DELETE: command_result(exit_code=1, stdout=IMUNIFY_NOT_IN_LIST)},
            ),
        ),
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is True
    assert payload["released"] is True
    assert payload["allowlisted"] is True


async def test_an_allow_the_backend_refused_fails_with_that_backends_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allow is the entry the operator asked to exist, so its refusal is a failure — and it
    keeps the backend's own code, because `csf_command_failed` and `ssh_sudo_required` send an
    administrator to different places."""
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
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert payload["ok"] is False
    assert payload["error_code"] == SSH_SUDO_REQUIRED_CODE
    assert payload["backends"]["csf"]["ok"] is False


async def test_a_server_that_vanished_after_approval_is_refused_before_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed on the far side of the boundary too: the row the operator approved against is
    gone, so the change does not run against whatever `server_ref` resolves to today."""
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=uuid4()))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_SERVER_UNAVAILABLE
    assert fake.commands == []


@pytest.mark.parametrize("duration", [0, MAX_DURATION_MINUTES + 1, "137", None, True])
async def test_evidence_with_an_unusable_duration_is_refused(
    monkeypatch: pytest.MonkeyPatch, duration: Any
) -> None:
    """V77 again, on the far side of the JSONB round trip.

    A window outside the bound — or one that came back as a string, or as `True`, which is an
    `int` in Python and would otherwise pass as one minute — is a request NOA refuses rather than
    guesses at. Guessing here writes an allow entry nobody approved the length of.
    """
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(
        execution_request(server_id=fixture.servers.servers[0].id, duration_minutes=duration)
    )

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert fake.commands == []


async def test_evidence_without_a_usable_target_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rule one field over: the address is what the change acts on."""
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id, target="  "))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_EVIDENCE_UNUSABLE
    assert fake.commands == []


async def test_zero_usable_backends_after_approval_raises_for_the_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V57 on the runner side, where the silent no-op would be an approved change.

    A runner is not decorated with `sanitize_tool_errors` — the executor catches `NoaError` and
    keeps its code (`core.approvals.execution`), so the receipt names `no_firewall_backend`
    rather than a generic failure. What must not happen is an `ok: True` from a box NOA could not
    drive at all.
    """
    fixture, _ = release_context(monkeypatch, box=FakeFirewallBox())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    with pytest.raises(NoaError) as raised:
        await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert raised.value.error_code == ERROR_NO_FIREWALL_BACKEND


async def test_a_non_root_user_escalates_every_firewall_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V55 is a biconditional, and the runner is where it has the most to lose.

    Every command the change sends — probes, releases, the allow and the confirming read — runs
    under `sudo -n` when the resolved SSH user is not root. One unescalated write is a change
    that silently does not happen.
    """
    fixture, fake = release_context(monkeypatch, box=released_box(), ssh_username="operator")
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert fake.commands
    assert all("sudo -n" in command for command in fake.commands)


async def test_a_root_user_escalates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half of the biconditional: root running under `sudo` is the same bug."""
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert fake.commands
    assert all("sudo" not in command for command in fake.commands)


# --------------------------------------------------------------------------------------
# C8, V43, V96: the reason goes out, and it does not come back
# --------------------------------------------------------------------------------------


async def test_the_written_comment_carries_the_operators_reason_behind_noas_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V43: the one field may leave NOA, and a firewall allow entry is somewhere it belongs.

    The alternative is a NOA-authored placeholder in a record a human reads on the box. What
    rides with it is the marker, and the marker is what makes V96's cut possible at all — asserted
    on both backends, because a comment written on one and not the other is a bound held on one
    side only.
    """
    fixture, fake = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))
    request = execution_request(server_id=fixture.servers.servers[0].id)

    await runner(request)

    marker = f"{NOA_COMMENT_MARKER}{request.action_request_id}"
    allow = next(command for command in csf_commands(fake) if csf_step(command) == CSF_TEMP_ALLOW)
    assert marker in allow and REASON in allow

    added = imunify_commands(fake)[1]
    assert marker in added and REASON in added


async def test_the_runner_payload_never_carries_the_reason_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V96b: `result_summary` is derived from this payload, and `noa_get_action_result` returns
    the summary to a model.

    The reason is on the `ChangeExecutionRequest` — the executor reads it off the row for every
    approved change — so it is in front of this runner throughout. Asserted on the derived
    summary as well as on the payload, because the summary is the thing a model actually reads
    (V96's own "on the serialized result ∧ on the derived summary").
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=fixture.servers.servers[0].id))

    assert REASON not in json.dumps(payload)
    assert REASON not in (result_summary(payload) or "")
    # Nor any evidence line: the after-state says what changed, not what csf printed.
    assert CSF_ALLOW_LINE not in json.dumps(payload)


async def test_a_backend_failure_message_is_cut_before_it_reaches_the_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V96b again, through the door a failure opens.

    A backend that refuses a command frequently quotes the command back — and the command NOA
    just ran carries the operator's reason in its comment. That message becomes
    `tool_runs.result_summary`, which `noa_get_action_result` hands to a model, so it is cut on
    the way into the payload rather than trusted to be harmless.

    The request is built first so the failure output can quote the *real* marker: a cut aimed at
    a marker this call never wrote would pass without cutting anything.
    """
    request = execution_request(server_id=uuid4())
    marker = f"{NOA_COMMENT_MARKER}{request.action_request_id}"
    quoted = f"csf: cannot run '-ta {TARGET} 8220 {marker} {REASON}'"
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(
                csf_answer(CSF_DENY_LINE),
                mutations={CSF_TEMP_ALLOW: command_result(exit_code=1, stdout=quoted)},
            ),
        ),
    )
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))

    payload = await runner(
        execution_request(
            server_id=fixture.servers.servers[0].id,
            action_request_id=request.action_request_id,
        )
    )

    rendered = json.dumps(payload)
    assert payload["ok"] is False
    assert REASON not in rendered
    # The pointer survives: it is what tells a human on the box which approval this entry is.
    assert marker in rendered


# --------------------------------------------------------------------------------------
# V46, DECISIONS §6.5: the receipt an operator reads
# --------------------------------------------------------------------------------------


async def test_the_receipt_keeps_the_two_halves_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """V46 and DECISIONS §6.5, through the real `build_receipt`.

    `before` is the gate's own preflight — why the address was blocked and the log line it was
    read from — and `after` is what the change answered, with its two outcomes named separately.
    Asserted through the production function rather than by hand, because "the receipt has two
    parts" is a claim about what the executor stores, not about what this test can assemble.
    """
    fixture, _ = release_context(monkeypatch, box=released_box())
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))
    request = execution_request(server_id=fixture.servers.servers[0].id)

    payload = await runner(request)
    receipt = build_receipt(evidence=request.evidence, payload=payload)

    assert receipt["ok"] is True
    assert receipt["before"][EVIDENCE_FIREWALL]["combined_verdict"] == "blocked"
    assert receipt["before"][EVIDENCE_FIREWALL]["matches"] == [CSF_DENY_LINE]
    assert receipt["after"]["released"] is True
    assert receipt["after"]["allowlisted"] is True
    assert receipt["after"]["expires_at"] == payload["expires_at"]


async def test_a_failed_change_keeps_its_before_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """V46: a receipt with a before-state and no working after-state IS the record of a change
    that did not complete — so the half the operator authorised against survives the failure."""
    fixture, _ = release_context(monkeypatch, box=released_box(csf_after=CSF_DENY_LINE))
    runner = payload_runner(build_whm_firewall_release_runner(context=fixture.context))
    request = execution_request(server_id=fixture.servers.servers[0].id)

    receipt = build_receipt(evidence=request.evidence, payload=await runner(request))

    assert receipt["ok"] is False
    assert receipt["error_code"] == ERROR_RELEASE_FAILED
    assert receipt["before"][EVIDENCE_FIREWALL]["matches"] == [CSF_DENY_LINE]
    assert receipt["after"]["released"] is False


# --- Helpers ---


def _flag_value(command: str, flag: str) -> str:
    """The token after `flag` in one composed command."""
    match = re.search(rf"{re.escape(flag)}\s+(\S+)", command)
    assert match is not None, f"`{flag}` is not in `{command}`"
    return match.group(1)
