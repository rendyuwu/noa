"""The one door onto the firewall backends.

`availability` answers *which backends work here*. This module is what every firewall operation goes
through to act on that answer, and it is the place the zero-backend refusal lives.

**Why here and not in each tool.** `noa-old` built its task dict from the usable backends and
handed it to `asyncio.gather` unconditionally: with neither backend usable the dict was empty,
`gather()` returned `[]`, and the tool reported success having changed nothing. The zero-backend
rule forbids
that, and the preflight refused it — but with a check written *inside* the preflight tool, which
is a guard the next tool can forget. A CHANGE tool that forgets it is the harm the rule is
actually about: an approved suspension or firewall release that reports done and did nothing, on
a server NOA could not drive at all.

So the fan-out itself refuses the empty set. `run_on_usable_backends` is the only place a
backend set becomes concurrent work, and it cannot be entered with nothing to do.
`whm_firewall_release_and_allow` and `whm_firewall_allowlist_remove` inherit the guard
by construction rather than by discipline, and
`test_whm_firewall_gate.py::test_the_firewall_fan_out_lives_in_one_place` keeps a hand-rolled
`asyncio.gather` from quietly re-opening the hole — a machine-readable property is bound
by a test, not by a docstring.

**Two codes, because two remedies** (`noa-old` GH #82). A denied `sudo -n` means a
sudoers line is missing on a server that has both binaries; telling that operator "no firewall
tools here" sends them to install software that is already installed.

`WHMFirewallCLIError` rather than a class per code: `errors.py` already covers "a firewall
backend command could not be run, or could not be trusted to have run" — which is exactly the
zero-usable-backend case — already lists `ssh_sudo_required` among the codes it raises, and is
already mapped once to 502 for the routes that reach it. Which failure it is stays in
`error_code`, as it does for every other code in that tree. On the MCP tool path
`sanitize_tool_errors` turns the raise into `{"ok": false, "error_code": …, "message": …}`
with the code intact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from core.integrations.whm.availability import (
    BACKEND_CSF,
    BACKEND_IMUNIFY,
    FirewallAvailability,
)
from core.integrations.whm.errors import WHMFirewallCLIError
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE

# The zero-backend rule's name for it. `noa-old` said `no_firewall_tools`; the contract says this.
ERROR_NO_FIREWALL_BACKEND = "no_firewall_backend"

MESSAGE_NO_FIREWALL_BACKEND = (
    "Neither CSF nor Imunify360 could be run on this server, so its firewall state is unknown."
)
# Names the subcommands rather than the binaries: the grant this remedy asks for is
# per-argument, and "grant NOPASSWD for csf" is a request the infra team will not approve.
# `docs/integrations/whm.md` publishes the whole line to paste.
MESSAGE_SUDO_REQUIRED = (
    "The SSH user lacks passwordless sudo rights for csf and imunify360-agent. Grant NOPASSWD "
    "sudo for the subcommands NOA runs — csf -g, -tr, -dr, -ta, -tra, -ar and "
    "imunify360-agent ip-list — or configure a root SSH user."
)

T = TypeVar("T")


def usable_backends(availability: FirewallAvailability) -> tuple[str, ...]:
    """Which backends this server can be driven through, in a fixed order.

    Fixed rather than incidental: the order decides how merged evidence reads, and two
    identical calls have to produce the same result — the reproducibility clause of the
    capped-read rule.
    """
    available = availability.as_tools_dict()
    return tuple(name for name in (BACKEND_CSF, BACKEND_IMUNIFY) if available[name])


def require_usable_backends(availability: FirewallAvailability) -> tuple[str, ...]:
    """The zero-backend gate: zero usable backends is an error, never an empty success.

    Raises `WHMFirewallCLIError` carrying `ssh_sudo_required` when the binaries are there but
    escalation was denied, and `no_firewall_backend` otherwise — see the module docstring for
    why the distinction is not cosmetic.
    """
    backends = usable_backends(availability)
    if backends:
        return backends
    if availability.sudo_required:
        raise WHMFirewallCLIError(code=SSH_SUDO_REQUIRED_CODE, message=MESSAGE_SUDO_REQUIRED)
    raise WHMFirewallCLIError(code=ERROR_NO_FIREWALL_BACKEND, message=MESSAGE_NO_FIREWALL_BACKEND)


async def run_on_usable_backends(
    availability: FirewallAvailability,
    *,
    csf: Callable[[], Awaitable[T]],
    imunify: Callable[[], Awaitable[T]],
) -> dict[str, T]:
    """Run one operation on every usable backend at once, keyed by backend.

    The gate runs first, so this never returns an empty mapping — the caller cannot mistake
    "nothing to do" for "done".

    Callables rather than awaitables: an unusable backend's coroutine is never created, so
    there is nothing to leak and no "coroutine was never awaited" warning standing in for the
    decision not to ask it.

    `asyncio.gather` rather than sequential awaits, for the reason the availability probe
    gathers: each operation is a full SSH handshake to the same host while an operator waits on
    a chat turn.
    """
    backends = require_usable_backends(availability)
    factories: dict[str, Callable[[], Awaitable[T]]] = {
        BACKEND_CSF: csf,
        BACKEND_IMUNIFY: imunify,
    }

    results = await asyncio.gather(*(factories[name]() for name in backends))
    return dict(zip(backends, results, strict=True))
