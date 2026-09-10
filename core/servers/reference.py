"""Operator word → one server of any system, or a structured refusal.

**This module is the answer to a question the server-list tool parked and the whitelist search
restated.** `noa-old` had one
`server_ref.py` per system; this repo ported two of them and left a note in the package
docstring saying the third would show whether a shared resolver was worth having, "or whether
that difference is two parameters or a third shape". Proxmox arrived with the password-reset
tool and it is WHM's shape *exactly* — a hostname parsed out of `base_url`, a `choices` entry
of id/name/base_url — so the answer is two parameters, and a third verbatim copy of ~130 lines
is what reuse over duplication exists to refuse.

What varies between the three is small enough to name:

- **how a row yields its host string** — `urlsplit(base_url).hostname` for WHM and Proxmox, the
  bare `ssh_host` column for PMG;
- **what a `choices` entry carries** — the extra field an operator recognises a machine by;
- **the subject noun** in the four messages, which is the only reason they read differently.

Everything else is policy, and the policy is what must not drift: id, then name, then host;
**any** tie is `choices` rather than a pick; a well-formed id that matches nothing stops rather
than falling through; both fallbacks compare case-insensitively; a whitespace-only reference is
a bad call rather than a wildcard.

**Order matters and is not cosmetic.**

- **UUID first**, because it is the only unambiguous form and it is what a `choices` list tells
  the caller to come back with. Tried by parse, so a name that happens to look like a UUID
  cannot shadow it. A well-formed id matching nothing is `host_not_found` and stops there —
  falling through would let a mistyped id resolve to a *different* server whose name happened to
  be that string, which is the one outcome an id is supposed to rule out.
- **Name before host**, because a name is what an admin typed into NOA on purpose while a host
  is derived from a connection field. A server deliberately named after another server's host is
  a naming problem for a human, not a reason to prefer the derived value.

Case-insensitive comparison is what makes the name tie *reachable*: every `name` column is
`unique=True`, but Postgres uniqueness is case-sensitive, so `Node1` and `node1` can both
exist and both match `NODE1`. Dropping the ambiguity branch because "the column is unique" would
be wrong for exactly that case.

**Error codes are `noa-old`'s, verbatim** (`host_required`, `host_not_found`, `host_ambiguous`)
because they are the strings the model and the admin panel already branch on. They live here now
rather than three times; `whm_ref`, `pmg_ref` and `proxmox_ref` re-export them so no caller's
import path changed when this module landed.

**The resolved row keeps its own type**. Matching only ever reads `id`, `name` and the host
accessor's output — the `ServerRowLike` bound — but a tool that resolves a server then *calls* it
needs the credentials off the row it resolved, and re-reading it by id would let a second query
disagree with the list the tie was judged against. So this is generic in the row, and a caller
declaring `ServerRefRepository[WHMServer]` reaches the credentials with no cast and without
widening the narrow view this module works against.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar
from urllib.parse import urlsplit
from uuid import UUID


class ServerRowLike(Protocol):
    """The two columns every reference resolution matches on, whatever the system.

    Deliberately not the host: WHM and Proxmox derive one from `base_url` while PMG stores
    `ssh_host`, and a Protocol that named either would push one system into pretending it has
    the other's column. The host arrives as an accessor instead (`host_of`).
    """

    id: UUID
    name: str


# Invariant: a resolution both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=ServerRowLike)

# Covariant: both repository methods return rows and neither accepts one.
RowT_co = TypeVar("RowT_co", bound=ServerRowLike, covariant=True)

# How many candidates a refusal carries. Verbatim from `noa-old`: enough to recognise the one you
# meant, few enough that a tie between forty servers does not paste forty rows into the
# transcript (summary-plus-URL is the answer for genuinely large results, not this).
MAX_CHOICES = 10

ERROR_REQUIRED = "host_required"
ERROR_NOT_FOUND = "host_not_found"
ERROR_AMBIGUOUS = "host_ambiguous"


class ServerRefRepository(Protocol[RowT_co]):
    """The inventory surface resolution needs, parametrised by the row it yields.

    Structurally what `WHMServerReadRepository`, `PMGServerReadRepository` and
    `ProxmoxServerReadRepository` already are, named here so this module depends on none of
    them — the per-system repositories keep their own names because each also states which
    `to_safe_dict` renders it outward, which is a claim about that table and not about
    resolution.
    """

    async def list_servers(self) -> Sequence[RowT_co]: ...

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...


@dataclass(frozen=True)
class ServerRefResolution(Generic[RowT]):
    """The answer to "which server did they mean?".

    `ok` is the only field a caller has to branch on. On success `server` is set; on failure
    `error_code` and `message` are, and `choices` is non-empty exactly when the failure was a
    tie. Kept as one type rather than a union because every caller does the same two things with
    it — use the row, or hand the refusal to `tool_failure()` — and a union would make that two
    code paths per tool instead of one.
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


def hostname_of(base_url: str) -> str | None:
    """The host component of a `base_url`, or `None` when it does not parse.

    The WHM and Proxmox accessor. Here rather than in either module because both systems are
    reached over HTTP and both store the endpoint as a URL, so one spelling of "the host of a
    base URL" is one place for it to be wrong.
    """
    return urlsplit(base_url).hostname


def required_message(subject: str) -> str:
    """The `host_required` sentence for one system."""
    return f"{subject} server reference is required"


async def resolve_server_ref(
    server_ref: str,
    *,
    repository: ServerRefRepository[RowT],
    subject: str,
    host_of: Callable[[RowT], str | None],
    describe: Callable[[RowT], dict[str, str]],
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a `ServerRefResolution` the tool layer
    turns into a structured result, because "I could not tell which server" is information the
    model can act on, while an exception is not.

    `subject` is the noun in the four messages ("WHM", "PMG", "Proxmox"). `host_of` may answer
    `None` for a row whose host does not parse, and such a row simply does not match — it is not
    an error, because the reference might still be somebody else's name.
    """
    reference = server_ref.strip()
    if not reference:
        # a whitespace-only required string is a bad call, not an empty search.
        return ServerRefResolution(
            ok=False, error_code=ERROR_REQUIRED, message=required_message(subject)
        )

    by_id = await _resolve_by_id(reference, repository=repository, subject=subject)
    if by_id is not None:
        return by_id

    servers = list(await repository.list_servers())

    by_name = _resolve_matches(
        [server for server in servers if server.name.lower() == reference.lower()],
        message=f"Multiple {subject} servers match '{reference}'. Use the server id.",
        describe=describe,
    )
    if by_name is not None:
        return by_name

    by_host = _resolve_matches(
        [
            server
            for server in servers
            if (host := host_of(server)) is not None and host.lower() == reference.lower()
        ],
        message=f"Multiple {subject} servers match host '{reference}'. Use the server id.",
        describe=describe,
    )
    if by_host is not None:
        return by_host

    return ServerRefResolution(
        ok=False,
        error_code=ERROR_NOT_FOUND,
        message=f"No {subject} server found matching '{reference}'",
    )


# --- Internals ---


async def _resolve_by_id(
    reference: str, *, repository: ServerRefRepository[RowT], subject: str
) -> ServerRefResolution[RowT] | None:
    """The UUID branch, or `None` when `reference` is not a UUID at all (see module docstring)."""
    try:
        server_id = UUID(reference)
    except ValueError:
        return None

    server = await repository.get_by_id(server_id)
    if server is None:
        return ServerRefResolution(
            ok=False,
            error_code=ERROR_NOT_FOUND,
            message=f"No {subject} server found for id {server_id}",
        )
    return ServerRefResolution(ok=True, server=server)


def _resolve_matches(
    matches: list[RowT],
    *,
    message: str,
    describe: Callable[[RowT], dict[str, str]],
) -> ServerRefResolution[RowT] | None:
    """One match wins, several tie, none falls through to the next pass."""
    if not matches:
        return None
    if len(matches) == 1:
        return ServerRefResolution(ok=True, server=matches[0])
    return ServerRefResolution(
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
    "ServerRefRepository",
    "ServerRefResolution",
    "ServerRowLike",
    "hostname_of",
    "required_message",
    "resolve_server_ref",
]
