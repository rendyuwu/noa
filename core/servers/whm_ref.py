"""Operator word → one WHM server, or a structured refusal (T19, V18, V21).

Ported from `noa-old` branch `MCP` (`whm/server_ref.py`, C13). Error codes are verbatim
(`host_required`, `host_not_found`, `host_ambiguous`) because they are the strings the model
and the admin panel already branch on.

**V18 is the whole point.** Three match kinds are tried in a fixed order — id, then name,
then `base_url` hostname — and *any* tie produces `host_ambiguous` with a `choices` list
rather than a pick. A resolver that guessed would turn "suspend the account on cpanel" into
a change on whichever server sorted first, and the operator would see a receipt for a
machine they never named.

Order matters and is not cosmetic:

- **UUID first**, because it is the only unambiguous form and it is what a `choices` list
  tells the caller to come back with. Tried by parse, so a name that happens to look like a
  UUID cannot shadow it.
- **Name before hostname**, because a name is what an admin typed into NOA on purpose,
  while a hostname is derived from `base_url`. A server deliberately named after another
  server's host is a naming problem for a human, not a reason to prefer the derived value.

Both fallbacks compare case-insensitively, which is what makes the name tie *reachable*:
`whm_servers.name` is `unique=True` (T4), but Postgres uniqueness is case-sensitive, so
`Node1` and `node1` can both exist and both match `NODE1`. Dropping the ambiguity branch
because "the column is unique" would be wrong for exactly that case.

**The resolved row keeps its own type** (T21). Matching only ever reads id, name and
`base_url` — the `WHMServerRowLike` bound — but the tools of T20-T26 need the credentials off
the row that was resolved, and re-reading it by id would let a second query disagree with the
list the tie was judged against. So `resolve_whm_server_ref` is generic in the row: hand it a
`WHMServerReadRepository[WHMServer]` and `resolution.server` is a `WHMServer`, credentials
included, with no cast and without widening the narrow view this module works against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

from core.servers.whm_repository import WHMServerReadRepository, WHMServerRowLike

# Invariant: `WHMServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=WHMServerRowLike)

# How many candidates a refusal carries. Verbatim from `noa-old`: enough to recognise the
# one you meant, few enough that a tie between forty servers does not paste forty rows into
# the transcript (V64 is the answer for genuinely large results, not this).
MAX_CHOICES = 10

ERROR_REQUIRED = "host_required"
ERROR_NOT_FOUND = "host_not_found"
ERROR_AMBIGUOUS = "host_ambiguous"

MESSAGE_REQUIRED = "WHM server reference is required"


@dataclass(frozen=True)
class WHMServerRefResolution(Generic[RowT]):
    """The answer to "which server did they mean?".

    `ok` is the only field a caller has to branch on. On success `server` is set; on failure
    `error_code` and `message` are, and `choices` is non-empty exactly when the failure was a
    tie. Kept as one type rather than a union because every caller does the same two things
    with it — use the row, or hand the refusal to `tool_failure()` — and a union would make
    that two code paths per tool instead of one.
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


def describe(server: WHMServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — the three fields that let an operator recognise a server and
    then name it unambiguously. No credentials and no SSH fields: this text goes into an LLM
    transcript (V8, V26).
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


def hostname_of(base_url: str) -> str | None:
    """The host component of a `base_url`, or `None` when it does not parse."""
    return urlsplit(base_url).hostname


async def resolve_whm_server_ref(
    server_ref: str, *, repository: WHMServerReadRepository[RowT]
) -> WHMServerRefResolution[RowT]:
    """Resolve `server_ref` to one WHM server, or refuse with a named code (V18, V21).

    Never raises for a bad reference: every outcome is a `WHMServerRefResolution` the tool
    layer turns into a structured result, because "I could not tell which server" is
    information the model can act on, while an exception is not (V19).
    """
    reference = server_ref.strip()
    if not reference:
        # V21: a whitespace-only required string is a bad call, not an empty search.
        return WHMServerRefResolution(ok=False, error_code=ERROR_REQUIRED, message=MESSAGE_REQUIRED)

    by_id = await _resolve_by_id(reference, repository=repository)
    if by_id is not None:
        return by_id

    servers = list(await repository.list_servers())

    by_name = _resolve_matches(
        [server for server in servers if server.name.lower() == reference.lower()],
        message=f"Multiple WHM servers match '{reference}'. Use the server id.",
    )
    if by_name is not None:
        return by_name

    by_host = _resolve_matches(
        [
            server
            for server in servers
            if (host := hostname_of(server.base_url)) is not None
            and host.lower() == reference.lower()
        ],
        message=f"Multiple WHM servers match host '{reference}'. Use the server id.",
    )
    if by_host is not None:
        return by_host

    return WHMServerRefResolution(
        ok=False,
        error_code=ERROR_NOT_FOUND,
        message=f"No WHM server found matching '{reference}'",
    )


# --- Internals ---


async def _resolve_by_id(
    reference: str, *, repository: WHMServerReadRepository[RowT]
) -> WHMServerRefResolution[RowT] | None:
    """The UUID branch, or `None` when `reference` is not a UUID at all.

    A well-formed id that matches nothing is `host_not_found` and stops there — it does not
    fall through to the name and hostname passes. Falling through would mean a mistyped id
    could resolve to a *different* server whose name happened to be that string, which is
    the one outcome an id is supposed to rule out.
    """
    try:
        server_id = UUID(reference)
    except ValueError:
        return None

    server = await repository.get_by_id(server_id)
    if server is None:
        return WHMServerRefResolution(
            ok=False,
            error_code=ERROR_NOT_FOUND,
            message=f"No WHM server found for id {server_id}",
        )
    return WHMServerRefResolution(ok=True, server=server)


def _resolve_matches(matches: list[RowT], *, message: str) -> WHMServerRefResolution[RowT] | None:
    """One match wins, several tie, none falls through to the next pass."""
    if not matches:
        return None
    if len(matches) == 1:
        return WHMServerRefResolution(ok=True, server=matches[0])
    return WHMServerRefResolution(
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
    "WHMServerRefResolution",
    "describe",
    "hostname_of",
    "resolve_whm_server_ref",
]
