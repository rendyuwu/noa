"""Operator word → one Proxmox server, or a structured refusal.

Ported from `noa-old` branch `MCP` (`proxmox/server_ref.py`). Error codes are verbatim
(`host_required`, `host_not_found`, `host_ambiguous`), which is why they live in
`core.servers.reference` and are re-exported here rather than restated.

**This is the module the package docstring was waiting for.** The WHM and PMG ports each took a
per-system resolver and left the question open: is the third one two parameters or a third shape? It
is two parameters, and Proxmox is `whm_ref`'s shape exactly — the host is parsed out of `base_url`,
and a candidate is recognised by id, name and that URL. So the shared policy was extracted into
`core.servers.reference` instead of writing a third copy of ~130 lines, and this file is what a
Proxmox row is and nothing else.

`noa-old`'s own version differed from its WHM sibling in one way that is *not* carried: it
answered a resolution whose `server` was typed `object`, so every caller cast. Here the row keeps
its type — `resolve_proxmox_server_ref` is generic, so a
`ProxmoxServerReadRepository[ProxmoxServer]` hands back a `ProxmoxServer` with its credentials,
which is what `proxmox_reset_vm_password` then builds a client from without a second read by id
that could disagree with the list a tie was judged against.
"""

from __future__ import annotations

import re
from typing import Final, TypeVar

from core.servers.proxmox_repository import ProxmoxServerReadRepository, ProxmoxServerRowLike
from core.servers.reference import (
    # Re-exported rather than re-declared — see `whm_ref` for why.
    ERROR_AMBIGUOUS as ERROR_AMBIGUOUS,
)
from core.servers.reference import (
    ERROR_NOT_FOUND,
    ServerRefResolution,
    hostname_of,
    required_message,
    resolve_server_ref,
)
from core.servers.reference import (
    ERROR_REQUIRED as ERROR_REQUIRED,
)
from core.servers.reference import (
    MAX_CHOICES as MAX_CHOICES,
)

# Invariant: `ProxmoxServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=ProxmoxServerRowLike)

# The noun in every message this resolver produces.
SUBJECT = "Proxmox"

MESSAGE_REQUIRED = required_message(SUBJECT)

# The two identity parameters both Proxmox tools publish, and the only copies of them.
#
# Here rather than in `mcp_tools/` because they are `MESSAGE_REQUIRED`'s question asked in the
# other direction — one is what NOA says when a reference is missing, the other is what it asks
# for before one is sent — and because the password tool and the NIC tool held byte-identical
# copies of both that a longer rule would have let drift apart.
SERVER_REF_DESCRIPTION: Final = (
    "Which Proxmox server: its id, its name in NOA, its hostname, or the node the VM runs on "
    "(a VM lookup's `Host Node`) — NOA resolves a node name to the server that runs it. Ask the "
    "operator if they have not named one."
)

NODE_DESCRIPTION: Final = (
    "The Proxmox node the VM runs on, exactly as Proxmox names it (for example `examplepve09`). "
    "It is the cluster member, not the VM."
)

# One name for the shared resolution type, as in `whm_ref` and `pmg_ref`.
ProxmoxServerRefResolution = ServerRefResolution


def describe(server: ProxmoxServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — enough for an operator to recognise an endpoint and then name it
    unambiguously. No `api_token_id` and no secret: this text goes into an LLM transcript.
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


# This fleet names its nodes `<cluster>pve<NN>` and names each NOA row after the cluster, so a
# node name resolves once the suffix is off: `examplepve09` is a member of `example`. Proxmox
# does not require that naming, which is why the pattern is here and not in
# `core.servers.reference`, where WHM and PMG would inherit a convention that is not theirs.
#
# ponytail: one hardcoded convention, no config. A second fleet with different node naming turns
# this into an env-read pattern; nothing else about the retry changes.
_NODE_SUFFIX: Final = re.compile(r"[-_.]?pve\d+$", re.IGNORECASE)


def _cluster_of_node(reference: str) -> str | None:
    """`examplepve09` → `example`, or `None` when there is nothing new to try.

    `None` covers two cases that must not reach a second lookup: a reference carrying no node
    suffix at all, and one that is *only* a suffix — `pve1` is a legitimate server name (this
    repo's own fixtures use it), and stripping it would search for the empty string.
    """
    reference = reference.strip()
    derived = _NODE_SUFFIX.sub("", reference).strip()
    return derived if derived and derived != reference else None


async def _resolve(
    reference: str, *, repository: ProxmoxServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """The shared policy, with Proxmox's two parameters. Written once so the retry cannot drift."""
    return await resolve_server_ref(
        reference,
        repository=repository,
        subject=SUBJECT,
        host_of=lambda server: hostname_of(server.base_url),
        describe=describe,
    )


async def resolve_proxmox_server_ref(
    server_ref: str, *, repository: ProxmoxServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one Proxmox server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not. It matters more here than on a READ path — the caller is
    a CHANGE tool, and a resolver that guessed would open an approval card for a machine the
    operator never named.

    A reference that matches nothing and ends in a node suffix is tried once more as its cluster,
    so the node name an operator pastes out of a VM lookup finds the server that runs it. The
    exact lookup runs first and wins, and the derived reference goes through the same exact,
    case-insensitive match as a typed one — so this is a second lookup, not a similarity search.
    """
    resolution = await _resolve(server_ref, repository=repository)
    if resolution.error_code != ERROR_NOT_FOUND:
        return resolution

    cluster = _cluster_of_node(server_ref)
    if cluster is None:
        return resolution

    retry = await _resolve(cluster, repository=repository)
    # A miss on the derived name is not news — the operator never typed it, so the refusal that
    # goes back names what they did send. A *tie* on it is news: it carries `choices`, and with no
    # Proxmox read tool in the catalog that list is the only thing that unblocks the model.
    return retry if retry.error_code != ERROR_NOT_FOUND else resolution
