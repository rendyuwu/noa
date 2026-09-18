"""Is CSF usable here? Is Imunify?

Copied from `noa-old` branch `MCP`, where all of it — including the CSF probe and the
`asyncio.gather` — sat inside `whm/integrations/imunify_cli.py`. Its own module here (a
deviation from the WHM port): the probe is about *both* backends, and the split keeps
`csf_cli`/`imunify_cli` symmetric with a one-directional import graph (`availability` → the two
CLI modules, never back).

**This is what the zero-backend rule is built on.** Every firewall tool asks this first and
then acts on whatever came back true, both backends in parallel. The invariant — zero backends
available → error `no_firewall_backend`, never a success that changed nothing — is enforced one
module over, in `firewall_gate.run_on_usable_backends`, which is the single door from this
answer to acting on it. What this module owes that door is an *honest* answer, because a false
positive here becomes exactly the silent no-op the zero-backend rule forbids, and no gate
downstream can catch it.

**Two probe strategies, chosen by the resolved SSH user.** This is the part that took an
incident to get right (`noa-old` GH #82):

- **root** — `command -v <binary>`. Presence is usability; there is nothing to escalate.
- **non-root** — run the *real* binary under `sudo -n`, with **an argv NOA already uses for
  work**: `csf -g <loopback>` and `imunify360-agent ip-list local list --by-ip <loopback>
  --json`. Not `command -v`, and not `sudo -l`:
  - `command -v` lies for a root-owned `0700` binary. The user cannot see `/usr/sbin/csf`, but
    `sudo -n /usr/sbin/csf` runs it perfectly well, so presence-checking as the unprivileged
    user reports "not installed" for a working server.
  - `sudo -l` needs the `listpw` sudoers setting to be permissive, which is a second
    configuration dependency for a question the direct probe already answers.

**Why the probe argv has to come from the working set.** A correctly scoped sudoers grant is
*per-argument*, so a probe outside the granted set reports "no firewall tools" on a server that
works. This probe used to send `csf -v` / `imunify360-agent version`, the two commands NOA
never uses for anything else — and on `web16-cpn` (read 2026-09-14) both were denied
(`sudo: a password is required`) while every argv NOA sends for work was granted:

    (root) NOPASSWD: /usr/sbin/csf -g *, /usr/sbin/csf -tr *, /usr/sbin/csf -dr *,
                     /usr/sbin/csf -ta *, /usr/sbin/csf -tra *, /usr/sbin/csf -ar *,
                     /usr/bin/imunify360-agent ip-list *

Measured on the same box, same day, same account: `TERM=dumb sudo -n /usr/sbin/csf -g
127.0.0.1` and `sudo -n imunify360-agent ip-list local list --by-ip 127.0.0.1 --json` both exit
0. So NOA asks its capability question with commands drawn from its own working set: any
sudoers policy that lets NOA work then lets NOA probe, by construction. That the same grant is
issued fleet-wide is owner-stated, not measured here. The published grant lives in
`docs/integrations/whm.md`, and `test_whm_firewall_gate.py` fails if a production argv escapes
it.

`sudo_required` is why the return type is a struct rather than two booleans. A binary that is
present but whose escalation was denied comes back `usable=False`, which on its own is
indistinguishable from "not installed" — and telling an operator "no firewall tools on this
server" when the truth is a missing sudoers line sends them hunting an install that is already
there. The flag lets the tool layer raise `ssh_sudo_required` instead.

A **transport** failure resolves to "not usable", never an exception: an unreachable host is a
legitimate answer to "can I run csf here?", and a probe that raises cannot report on the other
backend. A **row** failure no longer reaches here at all — as of the firewall-preflight tool the
caller resolves the
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
from typing import Final

from core.integrations.whm.csf_cli import CSF_BINARY, build_csf_command
from core.integrations.whm.imunify_cli import IMUNIFY_BINARY, build_imunify_command
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import ssh_exec
from core.remote_exec.sudo import is_sudo_rights_failure, requires_escalation
from core.remote_exec.types import SSHConnectionConfig

# The two backend names, in the order every merged result reads in. Here rather than in the
# gate or a tool because `as_tools_dict` below is what spells them, and one home is the whole
# point; `firewall_gate` imports them, so the graph stays one-directional.
BACKEND_CSF = "csf"
BACKEND_IMUNIFY = "imunify"

# The address the non-root probes ask about. Its content is never read — only the exit status
# is — so a loopback address no firewall holds is the cheapest honest question, and it cannot
# be confused with an operator's target. The test support layer imports this rather than
# repeating the literal, because a probe is recognised there by exact command string.
FIREWALL_PROBE_TARGET: Final[str] = "127.0.0.1"


@dataclass(frozen=True, slots=True)
class BinaryCheck:
    """One backend's verdict. `sudo_required` distinguishes denied from absent."""

    usable: bool
    sudo_required: bool


@dataclass(frozen=True, slots=True)
class FirewallAvailability:
    """Which firewall backends this server can actually be driven through.

    `csf` / `imunify` mean *usable* — present AND runnable, directly as root or via `sudo -n`
    when not. `sudo_required` is True when either binary was present but its escalation was
    denied, so callers can surface `ssh_sudo_required` rather than a misleading
    "no firewall tools" (`noa-old` GH #82).
    """

    csf: bool
    imunify: bool
    sudo_required: bool

    def as_tools_dict(self) -> dict[str, bool]:
        """The `available["csf"]` / `available["imunify"]` shape the tools branch on."""
        return {BACKEND_CSF: self.csf, BACKEND_IMUNIFY: self.imunify}


async def _check_binary(
    config: SSHConnectionConfig,
    *,
    which_command: str,
    build_probe: Callable[[SSHConnectionConfig], str],
) -> BinaryCheck:
    """Resolve whether one firewall binary is usable for the resolved SSH user.

    `build_probe` is a callable, not a prebuilt string, because `build_*_command` reads the
    escalation decision off the config — and keeping it lazy means the two probes share
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
        build_probe=lambda probe_config: build_csf_command(
            ["-g", FIREWALL_PROBE_TARGET], config=probe_config
        ),
    )


async def check_imunify_binary(config: SSHConnectionConfig) -> BinaryCheck:
    """Is `imunify360-agent` runnable here (escalating via `sudo -n` when non-root)?"""
    return await _check_binary(
        config,
        which_command=f"command -v {IMUNIFY_BINARY}",
        build_probe=lambda probe_config: build_imunify_command(
            ["ip-list", "local", "list", "--by-ip", FIREWALL_PROBE_TARGET, "--json"],
            config=probe_config,
        ),
    )


async def check_firewall_binaries(config: SSHConnectionConfig) -> FirewallAvailability:
    """Probe both backends in parallel and combine the verdicts.

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
