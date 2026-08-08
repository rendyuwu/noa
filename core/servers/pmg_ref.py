"""Operator word → one PMG server, or a structured refusal (T31, V18, V21).

Ported from `noa-old` branch `MCP` (`pmg/server_ref.py`, C13). Error codes are verbatim
(`host_required`, `host_not_found`, `host_ambiguous`) because they are the strings the model
and the admin panel already branch on.

**V18 is the whole point.** Three match kinds are tried in a fixed order — id, then name, then
`ssh_host` — and *any* tie produces `host_ambiguous` with a `choices` list rather than a pick.
A resolver that guessed would answer "is this address whitelisted?" about a machine the
operator never named, and at T29 it would *whitelist* on one.

Order matters and is not cosmetic:

- **UUID first**, because it is the only unambiguous form and it is what a `choices` list tells
  the caller to come back with. Tried by parse, so a name that happens to look like a UUID
  cannot shadow it.
- **Name before host**, because a name is what an admin typed into NOA on purpose.

Both fallbacks compare case-insensitively, which is what makes the name tie *reachable*:
`pmg_servers.name` is `unique=True` (T4), but Postgres uniqueness is case-sensitive, so `Pmg1`
and `pmg1` can both exist and both match `PMG1`. Dropping the ambiguity branch because "the
column is unique" would be wrong for exactly that case.

**Deliberately a sibling of `core.servers.whm_ref`, not a shared abstraction.** The two are the
same shape over different rows — WHM matches a hostname parsed out of `base_url`, PMG matches
the bare `ssh_host` column it stores, and their `choices` entries carry different fields. The
package docstring recorded the call at T19: `noa-old` had one `server_ref.py` per system, and
Proxmox (T27/T28) is the third, which is where a shared resolver stops being a guess about
what varies (V66).

**The resolved row keeps its own type**, as at T21: matching only ever reads id, name and
`ssh_host` — the `PMGServerRowLike` bound — but the tool then has to *connect* with the row it
resolved, and re-reading it by id would let a second query disagree with the list the tie was
judged against. So `resolve_pmg_server_ref` is generic in the row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar
from uuid import UUID

from core.servers.pmg_repository import PMGServerReadRepository, PMGServerRowLike

# Invariant: `PMGServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=PMGServerRowLike)

# How many candidates a refusal carries. Verbatim from `noa-old`: enough to recognise the one
# you meant, few enough that a tie does not paste the whole inventory into a transcript.
MAX_CHOICES = 10

ERROR_REQUIRED = "host_required"
ERROR_NOT_FOUND = "host_not_found"
ERROR_AMBIGUOUS = "host_ambiguous"

MESSAGE_REQUIRED = "PMG server reference is required"


@dataclass(frozen=True)
class PMGServerRefResolution(Generic[RowT]):
    """The answer to "which PMG server did they mean?".

    `ok` is the only field a caller has to branch on. On success `server` is set; on failure
    `error_code` and `message` are, and `choices` is non-empty exactly when the failure was a
    tie. One type rather than a union, because every caller does the same two things with it —
    use the row, or hand the refusal to `tool_failure()`.
    """

    ok: bool
    server: RowT | None = None
    error_code: str | None = None
    message: str = ""
    choices: list[dict[str, str]] = field(default_factory=list)

    @property
    def server_id(self) -> UUID | None:
        """The resolved id, or `None`. Derived, so it cannot disagree with `server`."""
        return None if self.server is None else self.server.id


def describe(server: PMGServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `ssh_host` — what lets an operator recognise a node and then name it
    unambiguously. No credentials: this text goes into an LLM transcript (V8, V26).
    """
    return {"id": str(server.id), "name": server.name, "ssh_host": server.ssh_host}


async def resolve_pmg_server_ref(
    server_ref: str, *, repository: PMGServerReadRepository[RowT]
) -> PMGServerRefResolution[RowT]:
    """Resolve `server_ref` to one PMG server, or refuse with a named code (V18, V21).

    Never raises for a bad reference: every outcome is a `PMGServerRefResolution` the tool layer
    turns into a structured result, because "I could not tell which server" is information the
    model can act on, while an exception is not (V19).
    """
    reference = server_ref.strip()
    if not reference:
        # V21: a whitespace-only required string is a bad call, not a wildcard.
        return PMGServerRefResolution(ok=False, error_code=ERROR_REQUIRED, message=MESSAGE_REQUIRED)

    by_id = await _resolve_by_id(reference, repository=repository)
    if by_id is not None:
        return by_id

    servers = list(await repository.list_servers())

    by_name = _resolve_matches(
        [server for server in servers if server.name.lower() == reference.lower()],
        message=f"Multiple PMG servers match '{reference}'. Use the server id.",
    )
    if by_name is not None:
        return by_name

    by_host = _resolve_matches(
        [server for server in servers if server.ssh_host.lower() == reference.lower()],
        message=f"Multiple PMG servers match host '{reference}'. Use the server id.",
    )
    if by_host is not None:
        return by_host

    return PMGServerRefResolution(
        ok=False,
        error_code=ERROR_NOT_FOUND,
        message=f"No PMG server found matching '{reference}'",
    )


# --- Internals ---


async def _resolve_by_id(
    reference: str, *, repository: PMGServerReadRepository[RowT]
) -> PMGServerRefResolution[RowT] | None:
    """The UUID branch, or `None` when `reference` is not a UUID at all.

    A well-formed id that matches nothing is `host_not_found` and stops there — it does not fall
    through to the name and host passes. Falling through would mean a mistyped id could resolve
    to a *different* server whose name happened to be that string, which is the one outcome an
    id is supposed to rule out.
    """
    try:
        server_id = UUID(reference)
    except ValueError:
        return None

    server = await repository.get_by_id(server_id)
    if server is None:
        return PMGServerRefResolution(
            ok=False,
            error_code=ERROR_NOT_FOUND,
            message=f"No PMG server found for id {server_id}",
        )
    return PMGServerRefResolution(ok=True, server=server)


def _resolve_matches(matches: list[RowT], *, message: str) -> PMGServerRefResolution[RowT] | None:
    """One match wins, several tie, none falls through to the next pass."""
    if not matches:
        return None
    if len(matches) == 1:
        return PMGServerRefResolution(ok=True, server=matches[0])
    return PMGServerRefResolution(
        ok=False,
        error_code=ERROR_AMBIGUOUS,
        message=message,
        choices=[describe(server) for server in matches[:MAX_CHOICES]],
    )


__all__ = [
    "ERROR_AMBIGUOUS",
    "ERROR_NOT_FOUND",
    "ERROR_REQUIRED",
    "MAX_CHOICES",
    "MESSAGE_REQUIRED",
    "PMGServerRefResolution",
    "describe",
    "resolve_pmg_server_ref",
]
