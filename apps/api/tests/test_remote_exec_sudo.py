"""`sudo -n` escalation.

The sudo-prefix rule is a biconditional: the prefix appears **iff** the resolved SSH user is
not root. So both directions are asserted, and the exact command strings are pinned —
`noa-old`'s csf/imunify call sites produced `TERM=dumb sudo -n /usr/sbin/csf …`, and the WHM
port's firewall tools inherit that shape. Token order is not cosmetic: the environment
assignment sits ahead of `sudo`.

`is_sudo_rights_failure` gets its own table because the interesting cases are the *negatives*
— a missing binary and a benign `sudo:` warning must not be reported as a rights problem
(`noa-old` GH #82).
"""

from __future__ import annotations

import pytest

from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.sudo import (
    SSH_SUDO_REQUIRED_CODE,
    build_remote_command,
    is_sudo_rights_failure,
    requires_escalation,
)
from core.remote_exec.types import CommandResult, SSHConnectionConfig

_CSF = "/usr/sbin/csf"


def _config(username: str) -> SSHConnectionConfig:
    return SSHConnectionConfig(
        host="example.test",
        port=22,
        username=username,
        host_key_fingerprint="SHA256:pinned",
    )


def _result(*, exit_code: int, stderr: str = "", stdout: str = "") -> CommandResult:
    return CommandResult(
        command=_CSF,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=1,
    )


# --- Sudo-prefix rule: prefix iff non-root ---


def test_root_user_gets_no_sudo_prefix() -> None:
    out = build_remote_command(
        [_CSF, "-g", "1.2.3.4"], config=_config("root"), env={"TERM": "dumb"}
    )

    assert out == "TERM=dumb /usr/sbin/csf -g 1.2.3.4"
    assert "sudo" not in out


def test_non_root_user_gets_sudo_n_after_env() -> None:
    out = build_remote_command(
        [_CSF, "-g", "1.2.3.4"], config=_config("noa-ops"), env={"TERM": "dumb"}
    )

    # Exact string from `noa-old`'s csf builder — the WHM port's tools assert this shape too.
    assert out == "TERM=dumb sudo -n /usr/sbin/csf -g 1.2.3.4"


def test_non_root_without_env_starts_with_sudo_n() -> None:
    out = build_remote_command(["imunify360-agent", "version"], config=_config("noa-ops"))

    assert out == "sudo -n imunify360-agent version"


@pytest.mark.parametrize(
    ("username", "expected"),
    [("root", False), ("noa-ops", True), ("Root", True), ("toor", True)],
)
def test_escalation_decision_is_username_only(username: str, expected: bool) -> None:
    """Nothing else can flip it — there is no caller-supplied `escalate` flag."""
    assert requires_escalation(_config(username)) is expected


def test_argv_tokens_are_quoted_under_escalation() -> None:
    out = build_remote_command(
        [_CSF, "-g", "1.2.3.4; rm -rf /"], config=_config("noa-ops"), env={"TERM": "dumb"}
    )

    assert out == "TERM=dumb sudo -n /usr/sbin/csf -g '1.2.3.4; rm -rf /'"


def test_env_value_with_spaces_stays_one_assignment() -> None:
    out = build_remote_command(["true"], config=_config("root"), env={"MSG": "a b"})

    # Value quoted, name bare: `'MSG=a b' true` would make the whole token a command name.
    assert out == "MSG='a b' true"


@pytest.mark.parametrize("name", ["BAD NAME", "1TERM", "TE-RM", "", "TERM=x", "TERM;rm"])
def test_invalid_env_name_rejected(name: str) -> None:
    with pytest.raises(SSHExecutionError) as excinfo:
        build_remote_command(["true"], config=_config("root"), env={name: "x"})

    assert excinfo.value.error_code == "ssh_command_invalid"


def test_empty_argv_rejected() -> None:
    with pytest.raises(SSHExecutionError) as excinfo:
        build_remote_command([], config=_config("noa-ops"), env={"TERM": "dumb"})

    assert excinfo.value.error_code == "ssh_command_invalid"


# --- rights-failure classification ---


@pytest.mark.parametrize(
    ("exit_code", "stderr", "expected"),
    [
        # Success is never a rights failure, whatever stderr says.
        (0, "sudo: a password is required", False),
        (1, "sudo: a password is required", True),
        (1, "sudo: a terminal is required to read the password", True),
        (1, "sudo: no tty present and no askpass program specified", True),
        (1, "user noa-ops is not allowed to execute '/usr/sbin/csf'", True),
        (1, "noa-ops is not in the sudoers file. This incident will be reported.", True),
        (1, "Sorry, user noa-ops may not run sudo on web16.", True),
        (1, "sudo: no valid sudoers sources found, quitting", True),
        (1, "sudo: sorry, you must have a tty to run sudo", True),
        (1, "sudo: pam_authenticate: Authentication failure", True),
        # Missing binary — the exclusion that keeps this from swallowing the zero-backend
        # error's case.
        (1, "sudo: /usr/sbin/csf: command not found", False),
        (127, "bash: csf: no such file or directory", False),
        # Benign warning carrying the `sudo:` prefix, with the real failure elsewhere.
        (2, "sudo: unable to resolve host web16\ncsf: invalid argument", False),
        (1, "", False),
    ],
)
def test_is_sudo_rights_failure(exit_code: int, stderr: str, expected: bool) -> None:
    assert is_sudo_rights_failure(_result(exit_code=exit_code, stderr=stderr)) is expected


def test_sudo_required_code_is_the_stable_string() -> None:
    """The WHM port's firewall tools surface this verbatim; renaming it breaks their contract."""
    assert SSH_SUDO_REQUIRED_CODE == "ssh_sudo_required"
