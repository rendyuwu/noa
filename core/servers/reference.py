"""Operator word → one server of any system, or a structured refusal.

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
because they are the strings the model and the admin panel already branch on. They live here
rather than three times.

**The resolved row keeps its own type**. Matching only ever reads `id`, `name` and the host
accessor's output — the `ServerRowLike` bound — but a tool that resolves a server then *calls* it
needs the credentials off the row it resolved, and re-reading it by id would let a second query
disagree with the list the tie was judged against. So this is generic in the row, and a caller
declaring `ServerRefRepository[WHMServer]` reaches the credentials with no cast and without
widening the narrow view this module works against.

**The three per-system wrappers are at the bottom of this file**, each a thin binding of the
policy above. The two *row views* they need live here too — `UrlServerRowLike` for the systems
whose host is parsed out of `base_url` and `SSHHostServerRowLike` for the one that stores a bare
`ssh_host`. Both stay narrower than `core.integrations.*`'s `*SecretLike` Protocols on purpose,
so a double built for a resolver test cannot be handed to something that decrypts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Final, Generic, Protocol, TypeVar
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


class UrlServerRowLike(ServerRowLike, Protocol):
    """A row whose host is parsed out of `base_url` — WHM and Proxmox, identically."""

    base_url: str


class SSHHostServerRowLike(ServerRowLike, Protocol):
    """A row whose host is the bare `ssh_host` column — PMG, which is SSH-only."""

    ssh_host: str


# Invariant: a resolution both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=ServerRowLike)

# Covariant: both repository methods return rows and neither accepts one.
RowT_co = TypeVar("RowT_co", bound=ServerRowLike, covariant=True)

# The same invariant as `RowT`, over the two views the wrappers below need: matching reads the
# host column as well as `id` and `name`.
UrlRowT = TypeVar("UrlRowT", bound=UrlServerRowLike)
SSHHostRowT = TypeVar("SSHHostRowT", bound=SSHHostServerRowLike)

# How many candidates a refusal carries. Verbatim from `noa-old`: enough to recognise the one you
# meant, few enough that a tie between forty servers does not paste forty rows into the
# transcript (summary-plus-URL is the answer for genuinely large results, not this).
MAX_CHOICES = 10

ERROR_REQUIRED = "host_required"
ERROR_NOT_FOUND = "host_not_found"
ERROR_AMBIGUOUS = "host_ambiguous"


class ServerRefRepository(Protocol[RowT_co]):
    """The inventory surface resolution needs, parametrised by the row it yields.

    Generic in the row, and that is what keeps the views above narrow: resolution matches on
    identity and never touches a credential, but a tool that resolves a server then *calls* it
    needs the credentials off the row it resolved — that row, not a second read by id which
    could disagree with the list a tie was judged against. A caller declares
    `ServerRefRepository[WHMServer]` and reaches them with no cast.
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


def hostname_of(base_url: str) -> str | None:
    """The host component of a `base_url`, or `None` when it does not parse.

    The WHM and Proxmox accessor. Here rather than in either module because both systems are
    reached over HTTP and both store the endpoint as a URL, so one spelling of "the host of a
    base URL" is one place for it to be wrong.
    """
    return urlsplit(base_url).hostname


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
            ok=False,
            error_code=ERROR_REQUIRED,
            message=f"{subject} server reference is required",
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


# --- WHM and Proxmox: the host is parsed out of `base_url` ---


def describe_url_server(server: UrlServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — the three fields that let an operator recognise a server and then
    name it unambiguously. No credentials and no SSH fields, and for Proxmox no `api_token_id`
    either: that one is not a credential on its own, but it names the user NOA authenticates as,
    and this text goes into an LLM transcript.
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


async def _resolve_url_server(
    reference: str, *, repository: ServerRefRepository[UrlRowT], subject: str
) -> ServerRefResolution[UrlRowT]:
    """The shared policy with the two parameters both URL systems take.

    One binding rather than one per system, so the accessor and the `choices` shape cannot drift
    apart — and so Proxmox's cluster retry cannot drift from its own first pass.
    """
    return await resolve_server_ref(
        reference,
        repository=repository,
        subject=subject,
        host_of=lambda server: hostname_of(server.base_url),
        describe=describe_url_server,
    )


async def resolve_whm_server_ref(
    server_ref: str, *, repository: ServerRefRepository[UrlRowT]
) -> ServerRefResolution[UrlRowT]:
    """Resolve `server_ref` to one WHM server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not.
    """
    return await _resolve_url_server(server_ref, repository=repository, subject="WHM")


# --- PMG ---


def describe_pmg(server: SSHHostServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `ssh_host` — what lets an operator recognise a node and then name it
    unambiguously. No credentials: this text goes into an LLM transcript.
    """
    return {"id": str(server.id), "name": server.name, "ssh_host": server.ssh_host}


async def resolve_pmg_server_ref(
    server_ref: str, *, repository: ServerRefRepository[SSHHostRowT]
) -> ServerRefResolution[SSHHostRowT]:
    """Resolve `server_ref` to one PMG server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not.

    PMG is SSH-only (the external interface contract), so the host is the bare `ssh_host` column
    rather than something parsed out of a URL, and that column is what a candidate is recognised
    by. That difference — one accessor and one `choices` field — is the whole of what made three
    copies of this look necessary.
    """
    return await resolve_server_ref(
        server_ref,
        repository=repository,
        subject="PMG",
        host_of=lambda server: server.ssh_host,
        describe=describe_pmg,
    )


# --- Proxmox ---

# The two identity parameters both Proxmox tools publish, and the only copies of them.
#
# Here rather than in `mcp_tools/` because they are the `host_required` sentence's question asked
# in the other direction — one is what NOA says when a reference is missing, the other is
# what it asks for before one is sent — and because the password tool and the NIC tool held
# byte-identical copies of both that a longer rule would have let drift apart.
#
# Prefixed, unlike the resolvers: a bare `SERVER_REF_DESCRIPTION` in a module serving three
# systems reads as if it served all three.
PROXMOX_SERVER_REF_DESCRIPTION: Final = (
    "Which Proxmox server: its id, its name in NOA, its hostname, or the node the VM runs on "
    "(a VM lookup's `Host Node`) — NOA resolves a node name to the server that runs it. Ask the "
    "operator if they have not named one."
)

PROXMOX_NODE_DESCRIPTION: Final = (
    "The Proxmox node the VM runs on, exactly as Proxmox names it (for example `examplepve09`). "
    "It is the cluster member, not the VM."
)

# This fleet names its nodes `<cluster>pve<NN>` and names each NOA row after the cluster, so a
# node name resolves once the suffix is off: `examplepve09` is a member of `example`. Proxmox
# does not require that naming, which is why the pattern sits on the Proxmox resolver rather
# than in `resolve_server_ref`, where WHM and PMG would inherit a convention that is not theirs.
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


async def resolve_proxmox_server_ref(
    server_ref: str, *, repository: ServerRefRepository[UrlRowT]
) -> ServerRefResolution[UrlRowT]:
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
    resolution = await _resolve_url_server(server_ref, repository=repository, subject="Proxmox")
    if resolution.error_code != ERROR_NOT_FOUND:
        return resolution

    cluster = _cluster_of_node(server_ref)
    if cluster is None:
        return resolution

    retry = await _resolve_url_server(cluster, repository=repository, subject="Proxmox")
    # A miss on the derived name is not news — the operator never typed it, so the refusal that
    # goes back names what they did send. A *tie* on it is news: it carries `choices`, and with no
    # Proxmox read tool in the catalog that list is the only thing that unblocks the model.
    return retry if retry.error_code != ERROR_NOT_FOUND else resolution
