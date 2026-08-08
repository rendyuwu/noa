"""`pmgsh` command composition, execution and failure classification
(T18, V55, V56, V58, V66, V69, V73).

V58 is the whole point of the module and most of this file: **argv-only, ⊥ shell string.** A
whitelist target arrives from an LLM tool argument (T29), so a `;` or `$(…)` in it must stay one
argument to `pmgsh`. `shlex.split` round-trips are how that is asserted rather than eyeballing a
string.

Two things the source repo had no reason to test, and this port does:

- **V55, as a biconditional.** `noa-old`'s PMG layer had no escalation path at all, so a non-root
  `ssh_username` silently failed on permissions. Both builders now read the resolved config
  (T18 deviation (1)), and both directions are asserted for both binaries.
- **`ssh_sudo_required` ≠ a generic command failure** (`noa-old` GH #82). Reachable only because
  escalation is now possible; a denied `sudo -n` and a missing binary produce different codes, so
  an operator is told to fix sudoers rather than hunting an install that is already there.

The mutation predicate gets its own attention. `pmgsh create`/`delete` report the underlying API
status in their *output*, not their exit code, so `require_pmg_mutation_success` accepts a
non-zero exit when stdout carries `200 OK` — and must keep rejecting the same text on stderr,
which is a different shape entirely. Getting that backwards turns an approved CHANGE that failed
into a receipt that says it worked (V57's reasoning, applied to PMG).

**A resolved `SSHConnectionConfig` goes in, not a row** as of T31, so the row refusals that used
to be asserted here now belong to `resolve_pmg_ssh_config` (`test_pmg_ssh_config.py`) and to the
tool that calls it (`test_pmg_tools_whitelist_search.py::test_an_unpinned_server_is_refused…`).
What is left in this file is what this module still decides: composition, success, parsing, and
the conversion of an SSH failure into one exception tree.

No host: `ssh_exec` is replaced inside the module's namespace (`support.remote_exec`).
"""

from __future__ import annotations

import shlex

import pytest
from fastapi import status

import core.integrations.pmg.pmgsh_cli as pmgsh_cli_mod
from core.errors import NoaError
from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.pmgsh_cli import (
    MYNETWORKS_PATH,
    PMGCONFIG_BINARY,
    PMGSH_BINARY,
    build_pmgconfig_command,
    build_pmgsh_command,
    parse_pmgsh_json_output,
    require_pmg_mutation_success,
    require_pmgsh_success,
    run_pmg_mynetworks_add,
    run_pmg_mynetworks_delete,
    run_pmg_mynetworks_list,
    run_pmg_mynetworks_probe,
    run_pmg_version_probe,
    run_pmgconfig_sync_restart,
    run_pmgsh_command,
    run_pmgsh_json,
)
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.output import command_output_text
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, error_body, status_for
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

_CIDR = "103.150.86.115/32"


def _pmg_ssh_config(*, username: str = "root"):  # type: ignore[no-untyped-def]
    return ssh_config(username=username, host="pmg.example.com")


# --- V55: prefix ⟺ user ≠ root ---


def test_pmgsh_command_escalates_only_for_non_root_user() -> None:
    as_root = build_pmgsh_command(["get", "/version"], config=_pmg_ssh_config())
    as_operator = build_pmgsh_command(
        ["get", "/version"], config=_pmg_ssh_config(username="noa-ops")
    )

    assert "sudo -n" not in as_root
    assert as_root == f"TERM=dumb {PMGSH_BINARY} get /version"
    assert as_operator == f"TERM=dumb sudo -n {PMGSH_BINARY} get /version"


def test_pmgconfig_command_escalates_only_for_non_root_user() -> None:
    as_root = build_pmgconfig_command(["sync", "--restart", "1"], config=_pmg_ssh_config())
    as_operator = build_pmgconfig_command(
        ["sync", "--restart", "1"], config=_pmg_ssh_config(username="noa-ops")
    )

    assert as_root == f"TERM=dumb {PMGCONFIG_BINARY} sync --restart 1"
    assert as_operator == f"TERM=dumb sudo -n {PMGCONFIG_BINARY} sync --restart 1"


def test_term_dumb_stays_ahead_of_sudo() -> None:
    """Inside the escalated command `TERM=dumb` would apply only when sudo runs, and `pmgsh`
    would paint control sequences for the root case."""
    command = build_pmgsh_command(
        ["ls", MYNETWORKS_PATH], config=_pmg_ssh_config(username="noa-ops")
    )

    assert command.index("TERM=dumb") < command.index("sudo -n")


def test_root_composition_is_byte_identical_to_the_source_repo() -> None:
    """C13/V69: the ported command string is unchanged for the case `noa-old` actually ran."""
    assert (
        build_pmgsh_command(["delete", f"{MYNETWORKS_PATH}/{_CIDR}"], config=_pmg_ssh_config())
        == f"TERM=dumb /usr/bin/pmgsh delete /config/mynetworks/{_CIDR}"
    )


# --- V58: argv only, absolute binary, one config path ---


def test_pmgsh_binary_is_an_absolute_path() -> None:
    """Under `sudo -n` the PATH is sudoers' `secure_path`, which need not carry /usr/bin."""
    assert PMGSH_BINARY == "/usr/bin/pmgsh"


def test_mynetworks_is_the_only_pmg_config_path_this_layer_names() -> None:
    assert MYNETWORKS_PATH == "/config/mynetworks"


@pytest.mark.parametrize(
    "hostile",
    ["1.2.3.4; rm -rf /", "$(id)", "a b", "--flag", "1.2.3.4/32 && reboot", "`id`"],
)
def test_arguments_are_quoted_not_interpolated(hostile: str) -> None:
    """Targets reach here from an LLM tool argument. `command_from_argv` quotes every token, so a
    `;` or `$(…)` stays one argument to pmgsh instead of becoming shell syntax."""
    command = build_pmgsh_command(
        ["create", MYNETWORKS_PATH, "-cidr", hostile], config=_pmg_ssh_config()
    )

    assert shlex.split(command) == [
        "TERM=dumb",
        PMGSH_BINARY,
        "create",
        MYNETWORKS_PATH,
        "-cidr",
        hostile,
    ]


def test_a_cidr_path_segment_survives_as_one_argv_token() -> None:
    """`/config/mynetworks/1.2.3.4/32` is one argument, not two — the embedded `/` must not
    split it, or `pmgsh delete` receives a path it does not recognise."""
    command = build_pmgsh_command(
        ["delete", f"{MYNETWORKS_PATH}/{_CIDR}"], config=_pmg_ssh_config()
    )

    assert shlex.split(command)[-1] == f"{MYNETWORKS_PATH}/{_CIDR}"


def test_no_remote_shell_helpers_are_composed() -> None:
    """V58 forbids remote `jq`/`awk`/`pmgdb`: NOA parses stdout locally."""
    command = build_pmgsh_command(["ls", MYNETWORKS_PATH], config=_pmg_ssh_config())

    for helper in ("jq", "awk", "pmgdb", "|", ">"):
        assert helper not in command


# --- V55 / GH #82: denied sudo is not a missing binary ---


def test_sudo_rights_failure_reports_ssh_sudo_required() -> None:
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmgsh_success(
            command_result(exit_code=1, stderr=SUDO_DENIED_STDERR), default_message="pmgsh failed"
        )

    assert exc.value.error_code == "ssh_sudo_required"
    assert SUDO_DENIED_STDERR in exc.value.message


def test_missing_binary_is_not_reported_as_sudo_rights_failure() -> None:
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmgsh_success(
            command_result(exit_code=127, stderr=SUDO_MISSING_BINARY_STDERR),
            default_message="pmgsh failed",
        )

    assert exc.value.error_code == "pmgsh_command_failed"


def test_mutation_sudo_denial_also_reports_ssh_sudo_required() -> None:
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmg_mutation_success(
            command_result(exit_code=1, stderr=SUDO_DENIED_STDERR), default_message="add failed"
        )

    assert exc.value.error_code == "ssh_sudo_required"


# --- the two success predicates ---


def test_require_pmgsh_success_returns_combined_output_on_exit_zero() -> None:
    """V56: both streams, off the banner-stripped view — `pmgsh` splits status and payload."""
    result = command_result(exit_code=0, stdout="200 OK", stderr="warning: noisy")

    assert require_pmgsh_success(result, default_message="x") == "200 OK\nwarning: noisy"


def test_require_pmgsh_success_falls_back_to_the_default_message_when_silent() -> None:
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmgsh_success(command_result(exit_code=2), default_message="pmgsh refused")

    assert exc.value.message == "pmgsh refused"


def test_require_pmgsh_success_rejects_a_200_ok_read() -> None:
    """The `200 OK` escape hatch belongs to mutations only. A read that exits non-zero failed."""
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmgsh_success(command_result(exit_code=1, stdout="200 OK"), default_message="x")

    assert exc.value.error_code == "pmgsh_command_failed"


def test_require_pmg_mutation_success_accepts_200_ok_on_nonzero_exit() -> None:
    """`pmgsh create`/`delete` report the API status in their output, not their exit code."""
    assert (
        require_pmg_mutation_success(
            command_result(exit_code=1, stdout="200 OK"), default_message="failed"
        )
        == "200 OK"
    )


def test_require_pmg_mutation_success_rejects_200_ok_only_on_stderr() -> None:
    """A different shape: the check reads `stdout`, ⊥ the combined text. Accepting this would
    turn a failed CHANGE into a receipt that says it worked."""
    with pytest.raises(PMGSHCLIError) as exc:
        require_pmg_mutation_success(
            command_result(exit_code=1, stderr="200 OK"), default_message="failed"
        )

    assert exc.value.error_code == "pmgsh_command_failed"


# --- JSON extraction: V56 / GH #83 ---


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ('{"version":"8.1"}', {"version": "8.1"}),
        ('[{"id":1}]', [{"id": 1}]),
        ('200 OK\n{"version":"8.1"}', {"version": "8.1"}),
        ('status text\n[{"id":1}]', [{"id": 1}]),
        ('{"version":"8.1"}\n200 OK', {"version": "8.1"}),
        ('noise\n[{"id":1}] trailing', [{"id": 1}]),
    ],
)
def test_parse_pmgsh_json_output_tolerates_status_noise(output: str, expected: object) -> None:
    assert parse_pmgsh_json_output(output) == expected


def test_parse_pmgsh_json_output_recovers_from_a_leading_banner() -> None:
    """`core.remote_exec.banner_strip` is the real fix (V56); scanning to the first `[`/`{` is
    what keeps a banner variant it did not recognise from failing a successful command."""
    assert parse_pmgsh_json_output(f'{_LVE_BANNER}\n{{"version":"8.1"}}') == {"version": "8.1"}


def test_parse_pmgsh_json_output_raises_when_json_absent() -> None:
    with pytest.raises(PMGSHCLIError) as exc:
        parse_pmgsh_json_output("200 OK")

    assert exc.value.error_code == "pmgsh_json_not_found"
    assert exc.value.message == "PMG command output did not contain JSON"


@pytest.mark.parametrize("output", ["200 OK\n{broken", 'noise {not json}\n[{"id":1}] trailing'])
def test_parse_pmgsh_json_output_raises_when_a_document_fails_to_decode(output: str) -> None:
    """A document that started and then broke is distinct from no document at all — the first
    `{` wins, so a malformed one is not silently skipped in favour of a later valid one."""
    with pytest.raises(PMGSHCLIError) as exc:
        parse_pmgsh_json_output(output)

    assert exc.value.error_code == "pmgsh_json_invalid"


# --- run_*: the config that connects is the config the command was built from ---


async def test_run_pmgsh_command_converts_an_ssh_failure_into_the_pmg_tree(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """One exception tree out of the module, so a caller does not catch two.

    The transport raises here rather than a row being malformed: since T31 the row never
    reaches this module, and a mismatched host key is what the pin (V82) actually produces.
    """

    def refuse(_command: str):  # type: ignore[no-untyped-def]
        raise SSHExecutionError(code="ssh_host_key_mismatch", message="presented key ≠ pin")

    install_fake_ssh_exec(monkeypatch, pmgsh_cli_mod, refuse)

    with pytest.raises(PMGSHCLIError) as exc:
        await run_pmgsh_command(_pmg_ssh_config(), args=["get", "/version"])

    assert exc.value.error_code == "ssh_host_key_mismatch"


async def test_run_pmgsh_command_sends_the_composed_command_over_ssh(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The command is built from the config that opens the connection (V55).

    Asserted together: a `noa-ops` config both escalates and is the config the transport saw.
    Composing against one config and connecting with another is the failure a boolean
    `escalate` parameter makes possible.
    """
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(stdout="200 OK")
    )
    config = _pmg_ssh_config(username="noa-ops")

    result = await run_pmgsh_command(config, args=["get", "/version"])

    assert result.stdout == "200 OK"
    assert fake.commands == [f"TERM=dumb sudo -n {PMGSH_BINARY} get /version"]
    assert fake.runs[0].config is config


async def test_run_pmgsh_json_checks_success_before_parsing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Decodable JSON on a failed command is still a failure — parsing it would report a
    successful read of output the command disowned."""
    install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(exit_code=1, stdout='{"ok": true}')
    )

    with pytest.raises(PMGSHCLIError) as exc:
        await run_pmgsh_json(_pmg_ssh_config(), args=["get", "/version"])

    assert exc.value.error_code == "pmgsh_command_failed"


async def test_run_pmg_version_probe_uses_exact_args(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(stdout='{"version":"8.1"}')
    )

    assert await run_pmg_version_probe(_pmg_ssh_config()) == {"version": "8.1"}
    assert fake.commands == [f"TERM=dumb {PMGSH_BINARY} get /version"]


async def test_run_pmg_mynetworks_probe_returns_the_endpoint_with_its_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A validate receipt should record *what* was probed, not only that something answered."""
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(stdout="1 10.10.10.0/24")
    )

    evidence = await run_pmg_mynetworks_probe(_pmg_ssh_config())

    assert evidence == {"endpoint": MYNETWORKS_PATH, "output": "1 10.10.10.0/24"}
    assert fake.commands == [f"TERM=dumb {PMGSH_BINARY} ls {MYNETWORKS_PATH}"]


async def test_run_pmg_mynetworks_list_returns_raw_output_for_the_tool_layer(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Entry parsing and CIDR normalisation live in `core.integrations.pmg.mynetworks` (V59) —
    this returns text."""
    install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(stdout="1 10.10.10.0/24")
    )

    assert await run_pmg_mynetworks_list(_pmg_ssh_config()) == "1 10.10.10.0/24"


async def test_run_pmg_mynetworks_add_uses_exact_args(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(exit_code=1, stdout="200 OK")
    )

    output = await run_pmg_mynetworks_add(_pmg_ssh_config(), cidr=_CIDR)

    assert output == "200 OK"
    assert fake.commands == [f"TERM=dumb {PMGSH_BINARY} create {MYNETWORKS_PATH} -cidr {_CIDR}"]


async def test_run_pmg_mynetworks_delete_uses_exact_args(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(exit_code=1, stdout="200 OK")
    )

    await run_pmg_mynetworks_delete(_pmg_ssh_config(), cidr=_CIDR)

    assert fake.commands == [f"TERM=dumb {PMGSH_BINARY} delete {MYNETWORKS_PATH}/{_CIDR}"]


async def test_run_pmgconfig_sync_restart_uses_exact_args(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Required after every mutation: without it the change is written and not applied."""
    fake = install_fake_ssh_exec(
        monkeypatch, pmgsh_cli_mod, lambda _cmd: command_result(stdout="synced")
    )

    assert await run_pmgconfig_sync_restart(_pmg_ssh_config()) == "synced"
    assert fake.commands == [f"TERM=dumb {PMGCONFIG_BINARY} sync --restart 1"]


# --- V66: one home for the combined-stream helper ---


def test_command_output_text_is_the_shared_helper() -> None:
    """`noa-old` carried this function three times, and this module held the third copy. T16
    moved it to `core.remote_exec.output` naming this port as the reason."""
    assert pmgsh_cli_mod.command_output_text is command_output_text


# --- V73: one taxonomy, one handler, mapped status ---


def test_pmgsh_error_tree_is_mapped_in_status_by_error() -> None:
    """Every subclass, walked — a class added later without an entry fails here rather than
    silently answering the 503 unclassified fallback."""
    seen: list[type[PMGSHCLIError]] = []

    def walk(klass: type[PMGSHCLIError]) -> None:
        seen.append(klass)
        for child in klass.__subclasses__():
            walk(child)

    walk(PMGSHCLIError)

    assert seen == [PMGSHCLIError]
    error = PMGSHCLIError(code="probe", message="probe")
    assert status_for(error) == status.HTTP_502_BAD_GATEWAY
    assert status_for(error) != FALLBACK_STATUS
    assert STATUS_BY_ERROR[PMGSHCLIError] == status.HTTP_502_BAD_GATEWAY


def test_pmgsh_errors_are_noa_errors_and_shape_a_clean_body() -> None:
    error = PMGSHCLIError(code="pmgsh_command_failed", message="pmgsh: 501 no such option")

    assert isinstance(error, NoaError)
    assert error_body(error) == {
        "error_code": "pmgsh_command_failed",
        "message": "pmgsh: 501 no such option",
    }
