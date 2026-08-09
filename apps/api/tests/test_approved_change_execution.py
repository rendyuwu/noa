"""Executing an approved change, without a database (T38 — V8, V20, V23, V46, V47).

Four claims live here and none of them is about SQL:

- **The authorization is re-read** (V23). Two identifiers are not permission, and the row can
  have moved between the decision's commit and the task being scheduled.
- **The runner is dispatched with what the gate recorded**, and refused before dispatch when
  those arguments are not runnable.
- **Both artifacts land in one commit** (V46): the run's terminal status and the receipt.
- **Nothing internal reaches the row** (V8). A raising runner's message, a host, an argument
  name — the operator-safe sentence is stored and the cause is logged.

The SQL — the `status = APPROVED AND tool_run_id = :run` predicate, and one receipt per request
— is `test_approved_change_execution_live.py`'s, against a real Postgres. The asyncio host is
`test_approved_change_execution_host.py`'s.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from core.approvals.execution import (
    ERROR_ARGUMENTS_REDACTED,
    ERROR_EXECUTION_FAILED,
    ERROR_RUNNER_UNAVAILABLE,
    LOG_EXECUTION_REFUSED,
    LOG_EXECUTION_STARTED,
    LOG_EXECUTION_UNAUTHORIZED,
    ApprovedChangeExecutionService,
    build_receipt,
)
from core.audit.summaries import MAX_RESULT_SUMMARY_LENGTH
from core.db.lifecycle import ToolRunStatus
from core.errors import NoaError
from core.secrets.redaction import REDACTED
from support.action_decisions import CHANGE_TOOL
from support.approved_change_execution import (
    EVIDENCE,
    RUNNER_OK,
    FakeApprovedChangeExecutionRepository,
    RecordingChangeRunner,
    authorized_change,
)


def build(
    repository: FakeApprovedChangeExecutionRepository,
    runner: RecordingChangeRunner | None = None,
    *,
    tool_name: str = CHANGE_TOOL,
) -> ApprovedChangeExecutionService:
    """The production service over the fake repository. Only the SQL and the runner are doubles."""
    return ApprovedChangeExecutionService(
        repository=repository,
        runners={} if runner is None else {tool_name: runner},
    )


async def execute(
    service: ApprovedChangeExecutionService,
    repository: FakeApprovedChangeExecutionRepository,
) -> ToolRunStatus | None:
    """Run the change the repository is seeded with."""
    authorized = repository.authorized
    assert authorized is not None, "seed an authorization first"
    return await service.execute_approved_tool_run(
        action_request_id=authorized.action_request_id,
        tool_run_id=authorized.tool_run_id,
    )


# --------------------------------------------------------------------------------------
# The authorization (V23)
# --------------------------------------------------------------------------------------


async def test_a_request_that_is_not_approved_is_never_executed() -> None:
    """V23: "may this run?" comes off the row, not off the two ids a caller supplied.

    `load_authorized` answering `None` covers every way that can be false — PENDING, DENIED,
    EXPIRED, or a run belonging to a different request — because the predicate is in the
    statement. What is asserted here is the consequence: no runner, no write, no commit.
    """
    repository = FakeApprovedChangeExecutionRepository()
    runner = RecordingChangeRunner()

    with capture_logs() as logs:
        status = await build(repository, runner).execute_approved_tool_run(
            action_request_id=uuid4(),
            tool_run_id=uuid4(),
        )

    assert status is None
    assert runner.calls == []
    assert repository.finishes == []
    assert repository.receipts == []
    assert repository.commits == 0
    assert any(entry["event"] == LOG_EXECUTION_UNAUTHORIZED for entry in logs)


async def test_the_ids_it_was_handed_are_the_ids_it_looks_up() -> None:
    """The pair is the predicate: a run id from somewhere else must not resolve this request."""
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)

    await execute(build(repository, RecordingChangeRunner()), repository)

    assert repository.loads == [(authorized.action_request_id, authorized.tool_run_id)]


async def test_the_authorization_is_read_before_anything_else_happens() -> None:
    """Ordering, not just occurrence: a runner dispatched before the read would have run a
    change that turned out not to be authorised."""
    journal: list[str] = []
    repository = FakeApprovedChangeExecutionRepository(journal, authorized=authorized_change())
    runner = RecordingChangeRunner(journal=journal)

    await execute(build(repository, runner), repository)

    assert journal == ["load", "run", "finish:COMPLETED", "receipt", "commit"]


# --------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------


async def test_the_runner_is_handed_what_the_gate_recorded() -> None:
    """C9/V17: the arguments and the before-state are the gate's own, carried on the row.

    Not re-gathered here, and not taken from anything the caller passed: what an operator
    approved against is what the change runs against.
    """
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)
    runner = RecordingChangeRunner()

    await execute(build(repository, runner), repository)

    call = runner.only_call
    assert call.action_request_id == authorized.action_request_id
    assert call.tool_run_id == authorized.tool_run_id
    assert call.tool_name == CHANGE_TOOL
    assert call.arguments == authorized.arguments
    assert call.evidence == EVIDENCE


async def test_a_tool_with_no_runner_fails_the_run_by_name() -> None:
    """The reachable path today: T22-T29 are unbuilt, so the registry is empty.

    A named terminal failure rather than a run left `STARTED` until the reaper — which is what
    makes shipping the executor before its first CHANGE tool safe rather than a silent hole.
    """
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())

    status = await execute(build(repository), repository)

    assert status is ToolRunStatus.FAILED
    assert ERROR_RUNNER_UNAVAILABLE in (repository.only_finish.result_summary or "")
    assert repository.only_receipt.receipt_data["error_code"] == ERROR_RUNNER_UNAVAILABLE


async def test_a_runner_registered_under_another_name_is_not_reached() -> None:
    """The negative control for the dispatch above (V87): the map is keyed by tool name, so a
    runner for a different tool must not answer for this one."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner()

    status = await execute(build(repository, runner, tool_name="whm_unsuspend_account"), repository)

    assert runner.calls == []
    assert status is ToolRunStatus.FAILED


async def test_a_change_whose_arguments_were_redacted_is_refused() -> None:
    """Fail-closed rather than running a change with `[redacted]` where a value belonged (V8).

    Vacuous today — no CHANGE tool NOA plans declares a sensitive-named argument, because
    C15/V49 generate secrets server-side — and a guard at the mechanism so that the tool which
    eventually does inherits it instead of discovering it (T68's rule).
    """
    repository = FakeApprovedChangeExecutionRepository(
        authorized=authorized_change(arguments={"account": "acmeco", "password": REDACTED}),
    )
    runner = RecordingChangeRunner()

    status = await execute(build(repository, runner), repository)

    assert runner.calls == []
    assert status is ToolRunStatus.FAILED
    assert repository.only_receipt.receipt_data["error_code"] == ERROR_ARGUMENTS_REDACTED


async def test_a_nested_redacted_argument_is_refused_too() -> None:
    """The guard walks as deep as the redactor does (V66).

    `redact_sensitive_data` recurses, so `{"server": {"ssh_password": ...}}` reaches
    `approval_context` with the value already replaced. A top-level-only scan sees an
    innocent `server` object and hands the runner `[redacted]` as a password — which is the
    exact outcome the refusal exists to prevent, one nesting level down.
    """
    repository = FakeApprovedChangeExecutionRepository(
        authorized=authorized_change(
            arguments={"account": "acmeco", "server": {"ssh_password": REDACTED}},
        ),
    )
    runner = RecordingChangeRunner()

    status = await execute(build(repository, runner), repository)

    assert runner.calls == []
    assert status is ToolRunStatus.FAILED
    assert repository.only_receipt.receipt_data["error_code"] == ERROR_ARGUMENTS_REDACTED


async def test_the_redaction_guard_reads_key_names_not_values() -> None:
    """The negative control for the guard above (V87).

    Redaction is by key name (`core.secrets.redaction`), so an ordinary argument whose *value*
    happens to be the placeholder string is not a redacted argument — and a guard that compared
    values would refuse a legitimate change for the wrong cause (T33(d)'s argument for an
    explicit key set over a substring rule).
    """
    repository = FakeApprovedChangeExecutionRepository(
        authorized=authorized_change(arguments={"account": REDACTED}),
    )
    runner = RecordingChangeRunner()

    status = await execute(build(repository, runner), repository)

    assert runner.calls == [runner.only_call]
    assert status is ToolRunStatus.COMPLETED


# --------------------------------------------------------------------------------------
# What gets recorded (V20, V46, V47)
# --------------------------------------------------------------------------------------


async def test_a_successful_change_completes_its_run() -> None:
    """V20/V47: the status comes off the envelope's `ok`, the same rule the READ path uses."""
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)

    status = await execute(build(repository, RecordingChangeRunner()), repository)

    assert status is ToolRunStatus.COMPLETED
    finish = repository.only_finish
    assert finish.tool_run_id == authorized.tool_run_id
    assert finish.status is ToolRunStatus.COMPLETED
    assert json.loads(finish.result_summary or "") == RUNNER_OK


async def test_a_runner_that_answers_a_refusal_records_failed() -> None:
    """`sanitize_tool_errors` returns `ok: False` rather than raising (V19), so without this
    branch every refused change would be recorded as one that worked."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner({"ok": False, "error_code": "ssh_sudo_required"})

    status = await execute(build(repository, runner), repository)

    assert status is ToolRunStatus.FAILED
    assert "ssh_sudo_required" in (repository.only_finish.result_summary or "")


async def test_a_failed_runner_records_failed_with_a_named_code() -> None:
    """A `NoaError` keeps its own code, so the thing an admin has to fix survives the boundary.

    The same pass-through V19's decorator makes one layer up: collapsing
    `ssh_host_key_not_validated` into a generic failure would send an operator hunting an
    outage rather than a pinned fingerprint.
    """

    class HostKeyError(NoaError):
        error_code = "ssh_host_key_not_validated"
        message = "That server has not been validated yet."

    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner()
    runner.fail = HostKeyError("fingerprint SHA256:abc does not match the pinned one")

    status = await execute(build(repository, runner), repository)

    assert status is ToolRunStatus.FAILED
    assert repository.only_receipt.receipt_data["error_code"] == "ssh_host_key_not_validated"


async def test_a_raising_runner_does_not_leak_its_message() -> None:
    """V8: the cause is logged, never stored. This row is read by the card, by T63's tool and
    by the admin audit surface, and a traceback names hosts, paths and configuration."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner()
    runner.fail = RuntimeError("connect root@db.internal password=hunter2")

    with capture_logs() as logs:
        status = await execute(build(repository, runner), repository)

    assert status is ToolRunStatus.FAILED
    stored = repr(repository.only_finish.result_summary) + repr(repository.only_receipt)
    assert "hunter2" not in stored
    assert "db.internal" not in stored
    assert ERROR_EXECUTION_FAILED in (repository.only_finish.result_summary or "")
    # Recoverable from the logs, which is the other half of the split.
    refusal = next(entry for entry in logs if entry["event"] == LOG_EXECUTION_REFUSED)
    assert "hunter2" in str(refusal["detail"])


async def test_a_cancelled_runner_is_not_recorded_at_all() -> None:
    """Shutdown cancels executions (V30), and a cancelled change has no outcome to record.

    `BaseException` is deliberately not caught: the run stays `STARTED`, which is exactly the
    row the reaper resolves, and swallowing the cancellation would turn a shutdown into a hang.
    """
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner()
    runner.fail = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await execute(build(repository, runner), repository)

    assert repository.finishes == []
    assert repository.commits == 0


async def test_a_long_result_is_bounded_before_it_is_stored() -> None:
    """`tool_runs.result_summary` is `String(2000)`; an unbounded change result would fail the
    UPDATE and lose the outcome of a change that already happened."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner({"ok": True, "log": "x" * 5000})

    await execute(build(repository, runner), repository)

    assert len(repository.only_finish.result_summary or "") == MAX_RESULT_SUMMARY_LENGTH


async def test_a_credential_in_a_result_is_redacted_before_it_is_stored() -> None:
    """V8, and the reason redaction is applied to results and not only to arguments: this row
    outlives the call and the receipt outlives the row.

    Both artifacts, because they carry the same payload in front of the same readers — T42's
    card, T63's tool, the admin audit surface. Redacting only the summary would put the
    credential one JSONB column over, where nothing looks for it.
    """
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner({"ok": True, "password": "hunter2"})

    await execute(build(repository, runner), repository)

    assert "hunter2" not in (repository.only_finish.result_summary or "")
    assert REDACTED in (repository.only_finish.result_summary or "")
    assert "hunter2" not in repr(repository.only_receipt.receipt_data)
    assert repository.only_receipt.receipt_data["after"]["password"] == REDACTED


# --------------------------------------------------------------------------------------
# The receipt (V46)
# --------------------------------------------------------------------------------------


async def test_the_receipt_is_two_part() -> None:
    """DECISIONS §6.5 and §T.25: before-state and after-state, never collapsed into "done".

    The before-state is the gate's evidence — the state the operator authorised against — so
    the two halves describe one decision rather than two readings taken minutes apart.
    """
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())

    await execute(build(repository, RecordingChangeRunner()), repository)

    receipt = repository.only_receipt
    assert receipt.receipt_data["before"] == EVIDENCE
    assert receipt.receipt_data["after"] == RUNNER_OK
    assert receipt.receipt_data["ok"] is True


async def test_a_failed_change_still_gets_a_receipt() -> None:
    """V46 is literal, and a receipt with a before-state and no working after-state is the
    truthful record of a change that did not complete — not an absence."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    runner = RecordingChangeRunner({"ok": False, "error_code": "ssh_sudo_required"})

    await execute(build(repository, runner), repository)

    receipt = repository.only_receipt
    assert receipt.receipt_data["ok"] is False
    assert receipt.receipt_data["before"] == EVIDENCE
    assert receipt.receipt_data["error_code"] == "ssh_sudo_required"


async def test_the_receipt_points_at_the_request_and_the_run() -> None:
    """One edge each: the request it is about, and the run that produced it."""
    authorized = authorized_change()
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized)

    await execute(build(repository, RecordingChangeRunner()), repository)

    receipt = repository.only_receipt
    assert receipt.action_request_id == authorized.action_request_id
    assert receipt.tool_run_id == authorized.tool_run_id


async def test_the_run_and_the_receipt_share_one_commit() -> None:
    """A `COMPLETED` run whose receipt rolled back is V46 asserted by prose and held by nothing.

    Asserted as a count *and* as order: two commits would satisfy "both were written".
    """
    journal: list[str] = []
    repository = FakeApprovedChangeExecutionRepository(journal, authorized=authorized_change())

    await execute(build(repository, RecordingChangeRunner(journal=journal)), repository)

    assert repository.commits == 1
    assert journal[-3:] == ["finish:COMPLETED", "receipt", "commit"]


async def test_an_already_written_receipt_is_not_an_error() -> None:
    """The reaper may have got to this run first (T36's UNIQUE). Whichever writer arrives second
    is a no-op, and the change is still recorded as having completed."""
    repository = FakeApprovedChangeExecutionRepository(authorized=authorized_change())
    repository.receipt_exists = True

    status = await execute(build(repository, RecordingChangeRunner()), repository)

    assert status is ToolRunStatus.COMPLETED
    assert repository.commits == 1


def test_build_receipt_omits_an_error_code_when_there_is_none() -> None:
    """`tool_failure`'s rule: an absent field beats an empty one, because a reader that finds
    `error_code: null` has to decide whether that means success."""
    assert "error_code" not in build_receipt(evidence={}, payload={"ok": True})


def test_build_receipt_treats_a_missing_ok_as_failure() -> None:
    """Matching `status_for_payload`: a result that cannot be read as a success is not one."""
    assert build_receipt(evidence={}, payload={})["ok"] is False


async def test_the_start_is_logged_with_the_identifiers_and_not_the_arguments() -> None:
    """V46's audit-log third, and V8's bound on it: the log names the change, never its payload.

    "Why did this account get suspended at 03:00" has to be answerable from the logs; "what was
    the account's password" must not be.
    """
    repository = FakeApprovedChangeExecutionRepository(
        authorized=authorized_change(arguments={"account": "acmeco", "secret": "hunter2"}),
    )

    with capture_logs() as logs:
        await execute(build(repository, RecordingChangeRunner()), repository)

    started = next(entry for entry in logs if entry["event"] == LOG_EXECUTION_STARTED)
    assert started["tool"] == CHANGE_TOOL
    assert "hunter2" not in repr(logs)
