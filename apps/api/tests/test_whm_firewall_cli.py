"""CSF and Imunify command composition, execution and failure classification.

Three things are pinned here, and each one is a production incident in `noa-old`:

- **The sudo rule, as a biconditional.** `sudo -n` appears ⟺ the resolved SSH user is not `root`.
  Both
  directions are asserted for both backends, because the port removed the `escalate=` boolean that
  let a call site get it wrong (deviation (a)) and the replacement only holds if the command
  builders really do read the config.
- **`ssh_sudo_required` ≠ "no firewall tools"** (`noa-old` GH #82). A denied `sudo -n` and a
  missing binary produce different codes, so an operator is told to fix sudoers rather than
  hunting an install that is already there.
- **A banner in front of JSON still parses** (`noa-old` GH #83). `core.remote_exec.banner_strip`
  is the real fix; the raw-decode fallback is what keeps an unrecognised banner variant
  from turning an approved CHANGE into a parse error.

`run_*_command` takes a resolved `SSHConnectionConfig` since the firewall preflight landed, so the
row refusals it used to raise on the way past are no longer its business — `test_whm_ssh_config.py`
owns them, and `test_whm_tools_firewall_preflight.py` owns the answer an operator gets.

No host: `ssh_exec` is replaced inside each module's namespace (`support.remote_exec`).
"""

from __future__ import annotations

import json
import shlex

import pytest
from fastapi import status

import core.remote_exec.ssh as ssh_module
from core.errors import NoaError
from core.integrations.whm.csf_cli import (
    CSF_BINARY,
    build_csf_command,
    require_csf_success,
    run_csf_command,
)
from core.integrations.whm.errors import (
    CSFCLIError,
    ImunifyCLIError,
    WHMFirewallCLIError,
)
from core.integrations.whm.imunify_cli import (
    IMUNIFY_BINARY,
    build_imunify_command,
    parse_imunify_json_output,
    run_imunify_command,
)
from core.remote_exec.errors import SSHExecutionError
from noa_api.api.errors import error_body
from support.remote_exec import (
    SUDO_DENIED_STDERR,
    SUDO_MISSING_BINARY_STDERR,
    command_result,
    install_fake_ssh_exec,
    ssh_config,
)

_LVE_BANNER = (
    "***************************************************************************\n"
    "*             !!!!  WARNING: YOU ARE INSIDE LVE !!!!                      *\n"
    "***************************************************************************"
)


# --- The sudo rule: prefix ⟺ user ≠ root ---


def test_csf_command_escalates_only_for_non_root_user() -> None:
    as_root = build_csf_command(["-g", "1.2.3.4"], config=ssh_config(username="root"))
    as_operator = build_csf_command(["-g", "1.2.3.4"], config=ssh_config(username="noa-ops"))

    assert "sudo -n" not in as_root
    assert as_root == f"TERM=dumb {CSF_BINARY} -g 1.2.3.4"
    assert as_operator == f"TERM=dumb sudo -n {CSF_BINARY} -g 1.2.3.4"


def test_imunify_command_escalates_only_for_non_root_user() -> None:
    as_root = build_imunify_command(["version"], config=ssh_config(username="root"))
    as_operator = build_imunify_command(["version"], config=ssh_config(username="noa-ops"))

    assert as_root == f"{IMUNIFY_BINARY} version"
    assert as_operator == f"sudo -n {IMUNIFY_BINARY} version"


def test_csf_command_keeps_term_dumb_ahead_of_sudo() -> None:
    """`TERM=dumb` is the environment csf sees either way. Inside the escalated command it
    would apply only when sudo runs, and csf would paint its tables for the root case."""
    command = build_csf_command(["-g", "1.2.3.4"], config=ssh_config(username="noa-ops"))

    assert command.index("TERM=dumb") < command.index("sudo -n")


def test_csf_binary_is_an_absolute_path() -> None:
    """Under `sudo -n` the PATH is sudoers' `secure_path`, which need not carry /usr/sbin."""
    assert CSF_BINARY == "/usr/sbin/csf"


@pytest.mark.parametrize("hostile", ["1.2.3.4; rm -rf /", "$(id)", "a b", "--flag"])
def test_arguments_are_quoted_not_interpolated(hostile: str) -> None:
    """Targets reach here from an LLM tool argument. `command_from_argv` quotes every token,
    so a `;` or `$(…)` stays one argument to csf instead of becoming shell syntax."""
    command = build_csf_command(["-g", hostile], config=ssh_config())

    assert shlex.split(command) == ["TERM=dumb", CSF_BINARY, "-g", hostile]


# --- GH #82: denied sudo is not a missing binary ---


def test_sudo_rights_failure_raises_ssh_sudo_required_not_command_failed() -> None:
    result = command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)

    with pytest.raises(CSFCLIError) as exc:
        require_csf_success(result, default_message="csf failed")

    assert exc.value.error_code == "ssh_sudo_required"
    assert SUDO_DENIED_STDERR in exc.value.message


def test_missing_binary_is_not_reported_as_sudo_rights_failure() -> None:
    result = command_result(exit_code=127, stderr="sudo: /usr/sbin/csf: command not found")

    with pytest.raises(CSFCLIError) as exc:
        require_csf_success(result, default_message="csf failed")

    assert exc.value.error_code == "csf_command_failed"


def test_require_csf_success_returns_combined_output_on_exit_zero() -> None:
    result = command_result(exit_code=0, stdout="csf: v14.16", stderr="warning: noisy")

    assert require_csf_success(result, default_message="x") == "csf: v14.16\nwarning: noisy"


def test_require_csf_success_falls_back_to_the_default_message_when_silent() -> None:
    with pytest.raises(CSFCLIError) as exc:
        require_csf_success(command_result(exit_code=2), default_message="csf refused")

    assert exc.value.message == "csf refused"


def test_imunify_sudo_denial_also_reports_ssh_sudo_required() -> None:
    result = command_result(exit_code=1, stderr=SUDO_DENIED_STDERR)

    with pytest.raises(ImunifyCLIError) as exc:
        parse_imunify_json_output(result)

    assert exc.value.error_code == "ssh_sudo_required"


def test_imunify_missing_binary_reports_command_failed() -> None:
    result = command_result(exit_code=127, stderr=SUDO_MISSING_BINARY_STDERR)

    with pytest.raises(ImunifyCLIError) as exc:
        parse_imunify_json_output(result)

    assert exc.value.error_code == "imunify_command_failed"


# --- GH #83: JSON survives a banner the signature gate missed ---


def test_imunify_json_parse_recovers_from_leading_banner_fragment() -> None:
    payload = {"items": [{"ip": "1.2.3.4", "purpose": "drop"}]}
    result = command_result(exit_code=0, stdout=f"{_LVE_BANNER}\n{json.dumps(payload)}")

    assert parse_imunify_json_output(result) == payload


def test_imunify_json_parses_a_clean_document() -> None:
    result = command_result(exit_code=0, stdout='{"items": []}')

    assert parse_imunify_json_output(result) == {"items": []}


def test_imunify_empty_output_is_its_own_code() -> None:
    with pytest.raises(ImunifyCLIError) as exc:
        parse_imunify_json_output(command_result(exit_code=0, stdout="   "))

    assert exc.value.error_code == "imunify_empty_response"


def test_imunify_json_array_is_not_an_object() -> None:
    with pytest.raises(ImunifyCLIError) as exc:
        parse_imunify_json_output(command_result(exit_code=0, stdout="[1, 2, 3]"))

    assert exc.value.error_code == "imunify_invalid_response"


def test_imunify_unrecoverable_output_reports_a_parse_error() -> None:
    with pytest.raises(ImunifyCLIError) as exc:
        parse_imunify_json_output(command_result(exit_code=0, stdout="not json at all"))

    assert exc.value.error_code == "imunify_json_parse_error"


# --- run_*: one exception tree out ---


async def test_run_csf_command_sends_the_composed_command_over_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(monkeypatch, lambda _cmd: command_result(stdout="ok"))

    result = await run_csf_command(ssh_config(username="noa-ops"), args=["-g", "1.2.3.4"])

    assert result.stdout == "ok"
    assert fake.commands == [f"TERM=dumb sudo -n {CSF_BINARY} -g 1.2.3.4"]


async def test_run_imunify_command_sends_the_composed_command_over_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(monkeypatch, lambda _cmd: command_result(stdout="{}"))

    await run_imunify_command(ssh_config(), args=["ip-list", "local", "list"])

    assert fake.commands == [f"{IMUNIFY_BINARY} ip-list local list"]


@pytest.mark.parametrize(
    ("runner", "error"),
    [
        pytest.param(run_csf_command, CSFCLIError, id="csf"),
        pytest.param(run_imunify_command, ImunifyCLIError, id="imunify"),
    ],
)
async def test_run_converts_an_ssh_failure_into_the_backend_error_tree(
    monkeypatch,  # type: ignore[no-untyped-def]
    runner,  # type: ignore[no-untyped-def]
    error: type[WHMFirewallCLIError],
) -> None:
    """One exception tree out of the module, so a caller does not catch two.

    The transport code travels intact rather than being flattened: `ssh_host_key_mismatch` and
    `csf_command_failed` send an operator to different places.

    Patched on `core.remote_exec.ssh` because both runners reach the transport through
    `run_cli`, which resolves `ssh_exec` from that module's globals.
    """

    async def refuse(config, *, command: str, **_):  # type: ignore[no-untyped-def]
        raise SSHExecutionError(code="ssh_host_key_mismatch", message="presented key ≠ pin")

    monkeypatch.setattr(ssh_module, "ssh_exec", refuse)

    with pytest.raises(error) as exc:
        await runner(ssh_config(), args=["-v"])

    assert exc.value.error_code == "ssh_host_key_mismatch"


# --- One taxonomy, one handler, mapped status ---


def test_firewall_error_tree_is_mapped_in_status_by_error() -> None:
    """Every subclass, walked — a class added later without an entry fails here rather than
    silently answering the 503 unclassified fallback."""
    seen: list[type[WHMFirewallCLIError]] = []

    def walk(klass: type[WHMFirewallCLIError]) -> None:
        seen.append(klass)
        for child in klass.__subclasses__():
            walk(child)

    walk(WHMFirewallCLIError)

    assert set(seen) == {WHMFirewallCLIError, CSFCLIError, ImunifyCLIError}
    for klass in seen:
        error = klass(code="probe", message="probe")
        assert error.status_code == status.HTTP_502_BAD_GATEWAY
        assert error.status_code != NoaError.status_code
    assert WHMFirewallCLIError.status_code == status.HTTP_502_BAD_GATEWAY


def test_firewall_errors_are_noa_errors_and_shape_a_clean_body() -> None:
    error = CSFCLIError(code="csf_command_failed", message="csf: not running")

    assert isinstance(error, NoaError)
    assert error_body(error) == {
        "error_code": "csf_command_failed",
        "message": "csf: not running",
    }
