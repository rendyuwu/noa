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

from typing import Final, TypeVar

from core.servers.proxmox_repository import ProxmoxServerReadRepository, ProxmoxServerRowLike
from core.servers.reference import (
    # Re-exported rather than re-declared — see `whm_ref` for why.
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    ServerRefResolution,
    hostname_of,
    required_message,
    resolve_server_ref,
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
#
# The four sibling `SERVER_REF_DESCRIPTION` constants stay where they are
# (`whm_firewall_allowlist.py`, `whm_firewall_change.py`, `whm_account_change.py`,
# `pmg_whitelist.py`): each is one tool's own string, so there is nothing to share and moving them
# would buy an import. Proxmox is the only system whose two tools published the same two strings
# twice, which is what makes this a deduplication rather than a relocation.
#
# It names the cluster/node distinction because Proxmox is the one system here with **no discovery
# tool**: `TOOL_CATALOG` exposes `whm_list_servers` but nothing that lists Proxmox servers, so a
# model handed a node name has no read to recover with. A node sent as a `server_ref` matches no
# id, no name and no `base_url` host, and `resolve_server_ref` answers `host_not_found` carrying no
# `choices` — `choices` is populated on a tie, and a miss is not a tie. Hence the last sentence:
# the way out of that is the operator, not another spelling.
#
# Generic on purpose. That a NOA row is a cluster and `node` is one member of it is a fact about
# Proxmox; that a given fleet names its nodes `<cluster>pve<NN>`, so the cluster is the text before
# the suffix, is that deployment's convention and belongs in the operator's own agent prompt
# (`docs/integrations/librechat.md`, "Agent system prompt (example)"), which is where it is stated.
#
# So this states the *relationship* and leaves the fleet's spelling of it to the operator's prompt.
# What it must not do is leave the relationship unusable, and the first live run showed it doing
# exactly that: the model read "the server is the cluster that node is part of", found no tool that
# maps one to the other, weighed the cluster name it had already derived against the standing
# "never guess an identifier" rule, classed its own correct derivation as a guess, and went looking
# for a registry — landing on `whm_list_servers`, which is the wrong system. Stating a relationship
# while every other rule in earshot says *do not act on one* is not neutrality; it is a dead end
# with extra steps.
#
# Hence the safety sentence. It is the fact that makes the derivation legitimate rather than a
# guess, and it is true of the resolver rather than reassurance: `resolve_server_ref` matches an
# id, a `name` or a `base_url` host **exactly** (case-insensitively) and has no fuzzy branch, so a
# derived reference either is a server or answers `host_not_found` having touched nothing. The
# residue it does not cover is a derivation that lands on a *different real* row; that one is
# caught by the operator, who sees server, node and vmid together on the card before approving.
SERVER_REF_DESCRIPTION: Final = (
    "Which Proxmox server: its id, its name in NOA, or its hostname. A cluster node name is not "
    "this value: a node such as `examplepve09` is one member of a cluster, and it belongs in "
    "`node`. When an operator names only a node, the server is the cluster that node is part of, "
    "and a node name usually carries its cluster's name — send that rather than asking first. It "
    "is matched exactly or refused, never approximately, so a reference that is wrong answers "
    "`host_not_found` and changes nothing. If that happens, ask the operator which server they "
    "mean and send what they answer."
)

# The other half of the pair, and the example is `examplepve09` rather than a bare `pve1` for one
# reason: it sits one parameter away from the string above, and two node shapes in one schema make
# the cluster/node distinction unreadable at exactly the site that has to teach it.
NODE_DESCRIPTION: Final = (
    "The Proxmox node the VM runs on, exactly as Proxmox names it (for example `examplepve09`). "
    "It is the cluster member: not the VM, and not the `server_ref`."
)

# One name for the shared resolution type, as in `whm_ref` and `pmg_ref`.
ProxmoxServerRefResolution = ServerRefResolution


def describe(server: ProxmoxServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — enough for an operator to recognise an endpoint and then name it
    unambiguously. No `api_token_id` and no secret: this text goes into an LLM transcript.
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


async def resolve_proxmox_server_ref(
    server_ref: str, *, repository: ProxmoxServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one Proxmox server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not. It matters more here than on a READ path — the caller is
    a CHANGE tool, and a resolver that guessed would open an approval card for a machine the
    operator never named.
    """
    return await resolve_server_ref(
        server_ref,
        repository=repository,
        subject=SUBJECT,
        host_of=lambda server: hostname_of(server.base_url),
        describe=describe,
    )


__all__ = [
    "ERROR_AMBIGUOUS",
    "ERROR_NOT_FOUND",
    "ERROR_REQUIRED",
    "MAX_CHOICES",
    "MESSAGE_REQUIRED",
    "NODE_DESCRIPTION",
    "SERVER_REF_DESCRIPTION",
    "SUBJECT",
    "ProxmoxServerRefResolution",
    "describe",
    "resolve_proxmox_server_ref",
]
