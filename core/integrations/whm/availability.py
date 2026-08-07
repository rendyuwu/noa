"""Is CSF usable here? Is Imunify? (T16, V55, V57, V69)

Copied from `noa-old` branch `MCP`, where all of it — including the CSF probe and the
`asyncio.gather` — sat inside `whm/integrations/imunify_cli.py`. Its own module here (T16
deviation (d)): the probe is about *both* backends, and the split keeps `csf_cli`/`imunify_cli`
symmetric with a one-directional import graph (`availability` → the two CLI modules, never
back).

**This is what V57 is built on.** Every firewall tool asks this first and then acts on whatever
came back true, both backends in parallel. The invariant the tools carry — zero backends
available → error `no_firewall_backend`, ⊥ a success that changed nothing — is enforced at the
tool layer (T24 for the preflight READ; T25/T26 and T68 for the CHANGE side, where a silent
no-op is the actual harm); what this module owes them is an *honest* answer, because a
false-positive here becomes exactly the silent no-op V57 forbids.

**Two probe strategies, chosen by the resolved SSH user.** This is the part that took an
incident to get right (`noa-old` GH #82, V55):

- **root** — `command -v <binary>`. Presence is usability; there is nothing to escalate.
- **non-root** — run the *real* binary under `sudo -n` with a benign read-only flag
  (`csf -v`, `imunify360-agent version`). Not `command -v`, and not `sudo -l`:
  - `command -v` lies for a root-owned `0700` binary. The user cannot see `/usr/sbin/csf`, but
    `sudo -n /usr/sbin/csf` runs it perfectly well, so presence-checking as the unprivileged
    user reports "not installed" for a working server.
  - `sudo -l` needs the `listpw` sudoers setting to be permissive, which is a second
    configuration dependency for a question the direct probe already answers.

`sudo_required` is why the return type is a struct rather than two booleans. A binary that is
present but whose escalation was denied comes back `usable=False`, which on its own is
indistinguishable from "not installed" — and telling an operator "no firewall tools on this
server" when the truth is a missing sudoers line sends them hunting an install that is already
there. The flag lets the tool layer raise `ssh_sudo_required` instead.

A **transport** failure resolves to "not usable", never an exception: an unreachable host is a
legitimate answer to "can I run csf here?", and a probe that raises cannot report on the other
backend. A **row** failure no longer reaches here at all — as of T24 the caller resolves the
`SSHConnectionConfig` itself, so an unpinned server or one with no stored credentials refuses
with `ssh_host_key_not_validated` / `ssh_not_configured` before either probe starts. That is the
better answer and it costs nothing: those failures were never per-backend anyway, since both
probes read the same row and would both have come back "not usable" — reported as "no firewall
tools on this server", which is the same misdiagnosis `sudo_required` exists to prevent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from core.integrations.whm.csf_cli import CSF_BINARY, build_csf_command
from core.integrations.whm.imunify_cli import IMUNIFY_BINARY, build_imunify_command
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import ssh_exec
from core.remote_exec.sudo import is_sudo_rights_failure, requires_escalation
from core.remote_exec.types import SSHConnectionConfig


@dataclass(frozen=True, slots=True)
class BinaryCheck:
    """One backend's verdict. `sudo_required` distinguishes denied from absent."""

    usable: bool
    sudo_required: bool


@dataclass(frozen=True, slots=True)
class FirewallAvailability:
    """Which firewall backends this server can actually be driven through (V57).

    `csf` / `imunify` mean *usable* — present AND runnable, directly as root or via `sudo -n`
    when not. `sudo_required` is True when either binary was present but its escalation was
    denied, so callers can surface `ssh_sudo_required` rather than a misleading
    "no firewall tools" (`noa-old` GH #82).
    """

    csf: bool
    imunify: bool
    sudo_required: bool

    def as_tools_dict(self) -> dict[str, bool]:
        """The `available["csf"]` / `available["imunify"]` shape the tools branch on (V57)."""
        return {"csf": self.csf, "imunify": self.imunify}


async def _check_binary(
    config: SSHConnectionConfig,
    *,
    which_command: str,
    build_probe: Callable[[SSHConnectionConfig], str],
) -> BinaryCheck:
    """Resolve whether one firewall binary is usable for the resolved SSH user.

    `build_probe` is a callable, not a prebuilt string, because `build_*_command` reads the
    escalation decision off the config (V55) — and keeping it lazy means the two probes share
    one shape while composing different commands.
    """
    try:
        if not requires_escalation(config):
            present = await ssh_exec(config, command=which_command)
            usable = present.exit_code == 0 and bool(present.stdout.strip())
            return BinaryCheck(usable=usable, sudo_required=False)

        probe = await ssh_exec(config, command=build_probe(config))
        if probe.exit_code == 0:
            return BinaryCheck(usable=True, sudo_required=False)
        if is_sudo_rights_failure(probe):
            return BinaryCheck(usable=False, sudo_required=True)
        # Command-missing or other non-rights failure → treat as not present.
        return BinaryCheck(usable=False, sudo_required=False)
    except SSHExecutionError:
        # Unreachable host. Not usable, and not this layer's error to raise — see the module
        # docstring.
        return BinaryCheck(usable=False, sudo_required=False)


async def check_csf_binary(config: SSHConnectionConfig) -> BinaryCheck:
    """Is `/usr/sbin/csf` runnable here (escalating via `sudo -n` when non-root)?"""
    return await _check_binary(
        config,
        which_command=f"command -v {CSF_BINARY}",
        build_probe=lambda probe_config: build_csf_command(["-v"], config=probe_config),
    )


async def check_imunify_binary(config: SSHConnectionConfig) -> BinaryCheck:
    """Is `imunify360-agent` runnable here (escalating via `sudo -n` when non-root)?"""
    return await _check_binary(
        config,
        which_command=f"command -v {IMUNIFY_BINARY}",
        build_probe=lambda probe_config: build_imunify_command(["version"], config=probe_config),
    )


async def check_firewall_binaries(config: SSHConnectionConfig) -> FirewallAvailability:
    """Probe both backends in parallel and combine the verdicts (V57).

    `asyncio.gather` rather than two awaits: each probe is a full SSH handshake against the
    same host, and a firewall preflight runs while an operator waits on a chat turn.
    """
    csf_check, imunify_check = await asyncio.gather(
        check_csf_binary(config),
        check_imunify_binary(config),
    )

    return FirewallAvailability(
        csf=csf_check.usable,
        imunify=imunify_check.usable,
        sudo_required=csf_check.sudo_required or imunify_check.sudo_required,
    )


__all__ = [
    "BinaryCheck",
    "FirewallAvailability",
    "check_csf_binary",
    "check_firewall_binaries",
    "check_imunify_binary",
]
