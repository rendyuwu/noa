"""Firewall backend availability probing (T16, V55, V57, V69).

This is the module V57 stands on: every firewall tool asks "which backends work here?" and
then acts on whatever came back true. A false positive becomes the silent no-op V57 forbids —
a CHANGE that reports success having changed nothing — so the probe's honesty is the thing
under test.

Four behaviours, each an assertion of its own:

- both backends are probed, and **in parallel** (`asyncio.gather`, not two sequential awaits);
- each backend's verdict is independent — one missing must ⊥ drag the other down;
- **present-but-denied ≠ absent.** `sudo_required` is what lets the tool layer answer
  `ssh_sudo_required` instead of "no firewall tools on this server" (`noa-old` GH #82);
- a broken server resolves to "not usable" rather than raising, because a preflight that
  raises cannot report on the other backend.

The root/non-root split is asserted through the probe *commands*, since that is where V55
lands here: root gets `command -v`, non-root gets the real binary under `sudo -n` — never
`command -v` as the unprivileged user, which lies about a root-owned `0700` binary.
"""

from __future__ import annotations

import asyncio

import core.integrations.whm.availability as availability_mod
from core.integrations.whm.availability import (
    check_csf_binary,
    check_firewall_binaries,
    check_imunify_binary,
)
from core.integrations.whm.csf_cli import CSF_BINARY
from core.integrations.whm.imunify_cli import IMUNIFY_BINARY
from support.remote_exec import (
    SUDO_DENIED_STDERR,
    command_result,
    install_fake_ssh_exec,
)
from support.secrets import build_cipher
from support.whm import FakeWHMServer


def _present(command: str):  # type: ignore[no-untyped-def]
    """Both binaries answer successfully."""
    return command_result(command=command, exit_code=0, stdout="ok")


# --- V57: both backends, in parallel ---


async def test_check_firewall_binaries_probes_both_backends(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(monkeypatch, availability_mod, _present)

    availability = await check_firewall_binaries(FakeWHMServer(), cipher=build_cipher())

    assert availability.csf is True
    assert availability.imunify is True
    assert availability.sudo_required is False
    assert availability.as_tools_dict() == {"csf": True, "imunify": True}
    assert len(fake.commands) == 2
    assert any(CSF_BINARY in command for command in fake.commands)
    assert any(IMUNIFY_BINARY in command for command in fake.commands)


async def test_both_probes_are_in_flight_at_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`asyncio.gather`, ⊥ two sequential awaits: each probe is a full SSH handshake and an
    operator is waiting on a chat turn. A barrier here would double the wait."""
    started = asyncio.Event()
    both_started = asyncio.Event()
    in_flight = 0

    async def fake_ssh_exec(config, *, command: str, **_):  # type: ignore[no-untyped-def]
        nonlocal in_flight
        in_flight += 1
        if in_flight == 2:
            both_started.set()
        started.set()
        # Deadlocks unless the second probe starts before the first returns.
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return command_result(command=command, exit_code=0, stdout="ok")

    monkeypatch.setattr(availability_mod, "ssh_exec", fake_ssh_exec)

    availability = await check_firewall_binaries(FakeWHMServer(), cipher=build_cipher())

    assert both_started.is_set()
    assert availability.csf is True and availability.imunify is True


async def test_availability_reports_each_backend_independently(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """CSF-only is the common shape — plenty of cPanel boxes never install Imunify."""

    def handler(command: str):  # type: ignore[no-untyped-def]
        if IMUNIFY_BINARY in command:
            return command_result(command=command, exit_code=1, stderr="not found")
        return command_result(command=command, exit_code=0, stdout=CSF_BINARY)

    install_fake_ssh_exec(monkeypatch, availability_mod, handler)

    availability = await check_firewall_binaries(FakeWHMServer(), cipher=build_cipher())

    assert availability.csf is True
    assert availability.imunify is False
    assert availability.sudo_required is False


async def test_zero_backends_available_is_reported_honestly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The input to V57's `no_firewall_backend` refusal. This layer must ⊥ round it up."""
    install_fake_ssh_exec(
        monkeypatch,
        availability_mod,
        lambda command: command_result(command=command, exit_code=1),
    )

    availability = await check_firewall_binaries(FakeWHMServer(), cipher=build_cipher())

    assert availability.as_tools_dict() == {"csf": False, "imunify": False}


# --- GH #82: present-but-denied is not absent ---


async def test_availability_sets_sudo_required_when_escalation_denied(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    install_fake_ssh_exec(
        monkeypatch,
        availability_mod,
        lambda command: command_result(command=command, exit_code=1, stderr=SUDO_DENIED_STDERR),
    )

    availability = await check_firewall_binaries(
        FakeWHMServer(ssh_username="noa-ops"), cipher=build_cipher()
    )

    assert availability.csf is False
    assert availability.imunify is False
    # …but the operator is told to fix sudoers, ⊥ to install csf.
    assert availability.sudo_required is True


async def test_sudo_required_is_set_when_either_backend_is_denied(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def handler(command: str):  # type: ignore[no-untyped-def]
        if CSF_BINARY in command:
            return command_result(command=command, exit_code=0, stdout="csf: v14.16")
        return command_result(command=command, exit_code=1, stderr=SUDO_DENIED_STDERR)

    install_fake_ssh_exec(monkeypatch, availability_mod, handler)

    availability = await check_firewall_binaries(
        FakeWHMServer(ssh_username="noa-ops"), cipher=build_cipher()
    )

    assert availability.csf is True
    assert availability.imunify is False
    assert availability.sudo_required is True


async def test_missing_binary_under_sudo_is_not_a_sudo_problem(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    install_fake_ssh_exec(
        monkeypatch,
        availability_mod,
        lambda command: command_result(
            command=command, exit_code=127, stderr="sudo: csf: command not found"
        ),
    )

    availability = await check_firewall_binaries(
        FakeWHMServer(ssh_username="noa-ops"), cipher=build_cipher()
    )

    assert availability.sudo_required is False


# --- V55: which probe runs depends on the resolved user ---


async def test_root_probes_presence_with_command_v(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(monkeypatch, availability_mod, _present)

    await check_csf_binary(FakeWHMServer(ssh_username=None), cipher=build_cipher())

    assert fake.commands == [f"command -v {CSF_BINARY}"]


async def test_non_root_probes_the_real_binary_under_sudo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """⊥ `command -v` as the unprivileged user: it reports "not installed" for a root-owned
    `0700` binary that `sudo -n` runs perfectly well. ⊥ `sudo -l` either — that needs a
    permissive `listpw`, a second configuration dependency for a question the direct probe
    already answers."""
    fake = install_fake_ssh_exec(monkeypatch, availability_mod, _present)

    await check_csf_binary(FakeWHMServer(ssh_username="noa-ops"), cipher=build_cipher())
    await check_imunify_binary(FakeWHMServer(ssh_username="noa-ops"), cipher=build_cipher())

    assert fake.commands == [
        f"TERM=dumb sudo -n {CSF_BINARY} -v",
        f"sudo -n {IMUNIFY_BINARY} version",
    ]
    assert not any("command -v" in command for command in fake.commands)


async def test_root_presence_check_requires_non_empty_output(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Some shells exit 0 from `command -v` with nothing on stdout."""
    install_fake_ssh_exec(
        monkeypatch,
        availability_mod,
        lambda command: command_result(command=command, exit_code=0, stdout="   "),
    )

    check = await check_csf_binary(FakeWHMServer(), cipher=build_cipher())

    assert check.usable is False


# --- a broken server answers "not usable", it does not raise ---


async def test_unvalidated_host_key_yields_not_usable_rather_than_an_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = install_fake_ssh_exec(monkeypatch, availability_mod, _present)

    availability = await check_firewall_binaries(
        FakeWHMServer(ssh_host_key_fingerprint=None), cipher=build_cipher()
    )

    assert availability.as_tools_dict() == {"csf": False, "imunify": False}
    assert availability.sudo_required is False
    assert fake.runs == []


async def test_server_without_ssh_credentials_yields_not_usable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    install_fake_ssh_exec(monkeypatch, availability_mod, _present)

    availability = await check_firewall_binaries(
        FakeWHMServer(ssh_password=None, ssh_private_key=None), cipher=build_cipher()
    )

    assert availability.as_tools_dict() == {"csf": False, "imunify": False}
