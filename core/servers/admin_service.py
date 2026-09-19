"""Server-inventory CRUD policy: names, encryption, audit, commit.

One service over three policy constants — one per table — and between them they hold every
rule the fifteen admin routes have. The routes (`noa_api.api.routes.admin_servers`) resolve the
actor, call one method and shape the answer — the split `noa_api.api.routes.mcp_tokens` states
one file over, and the reason is the same: these rules are properties of the *row*, so a future
CLI, fixture or bootstrap script gets them too, not only whoever calls the HTTP path.

Five rules, and each is here rather than in a route:

1. **The name is checked before the write, case-insensitively.** `core.servers.reference`
   records why the case matters: Postgres uniqueness is case-sensitive, so `Node1` and `node1`
   can both exist while `NODE1` matches both, which is a permanent `host_ambiguous` for a
   reference an operator will keep typing. The write side is the only place that can
   prevent it. The unique index stays the backstop for the exact-case race.
2. **Secrets are encrypted here and nowhere else on this path.** A service method takes
   **plaintext** in the secret fields of its spec and hands the repository a copy whose secrets
   are `enc:v1:fernet:…`. `core.servers.admin_repository` cannot encrypt — it has no
   cipher — so there is exactly one encryption site, and `test_server_admin_repository.py`
   asserts the stored columns rather than trusting this sentence.
3. **One audit event per mutation**, carrying ids, names, hosts, ports and presence
   booleans. Never a token, never an SSH credential.
4. **`commit()` is the last statement, after every guard**. `noa_api.api.deps`'s
   session dependency does not commit, so a service that only reached `flush()` would answer
   200 over a rollback — the same flush-only rollback hole found on the RBAC engine and again
   on the token service, and the found-one-owes-a-sweep rule makes finding one an obligation to
   sweep the rest. This is the third service on that dependency and it
   lands with the boundary rather than acquiring it later.
5. **A WHM row marked `is_reseller_credential` is named after its `api_username`**,
   checked on the row the write *results in* rather than on the request body, so a PATCH that
   only flips the flag is refused too. `false` rows are not bound — the sixteen root rows
   cannot all be named `root`. It is the first cross-field rule on this surface, and it is here
   for the reason the name check is: it is a property of the row, so a fixture or a bootstrap
   script that inserts a reseller credential owes it as well. It reaches the shared flow through
   `ServerAdminPolicy.check`, which the other two tables fill with `_no_check`.

Deliberately NOT here: the reachability probe. `POST …/validate` opens a socket to somebody else's
host, and holding a pooled connection across that hop is how a slow server becomes a database outage
(the session-before-hop rule). `core.servers.validation` owns it, draws its own short sessions from
the session factory, and can write exactly one column.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Generic, TypeVar
from uuid import UUID

from core.audit.admin_events import (
    EVENT_PMG_SERVER_CREATED,
    EVENT_PMG_SERVER_DELETED,
    EVENT_PMG_SERVER_UPDATED,
    EVENT_PROXMOX_SERVER_CREATED,
    EVENT_PROXMOX_SERVER_DELETED,
    EVENT_PROXMOX_SERVER_UPDATED,
    EVENT_WHM_SERVER_CREATED,
    EVENT_WHM_SERVER_DELETED,
    EVENT_WHM_SERVER_UPDATED,
    AdminAuditEvent,
    AdminAuditSink,
)
from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.secrets.crypto import SecretCipher
from core.servers.admin_repository import (
    CreateT,
    PatchT,
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    RowT,
    ServerAdminRepository,
    SSHCredentials,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.errors import (
    PMGServerNameExistsError,
    PMGServerNotFoundError,
    ProxmoxServerNameExistsError,
    ProxmoxServerNotFoundError,
    ServerInventoryError,
    WHMResellerCredentialNameMismatchError,
    WHMServerNameExistsError,
    WHMServerNotFoundError,
)
from core.servers.naming import normalize_whm_identity

SSHCredentialsT = TypeVar("SSHCredentialsT", bound=SSHCredentials)

# Constrained to the create-or-update pair of one table, so `replace` keeps the concrete type
# and the encrypt functions below cannot be handed another vertical's spec.
WHMSpecT = TypeVar("WHMSpecT", WHMServerCreate, WHMServerUpdate)
ProxmoxSpecT = TypeVar("ProxmoxSpecT", ProxmoxServerCreate, ProxmoxServerUpdate)
PMGSpecT = TypeVar("PMGSpecT", PMGServerCreate, PMGServerUpdate)


def encrypt_ssh_credentials(values: SSHCredentialsT, *, cipher: SecretCipher) -> SSHCredentialsT:
    """Copy `values` with its three secrets encrypted, everything else untouched.

    `dataclasses.replace` keeps the concrete type, so an `SSHCredentialsPatch` stays a patch
    with its clear flags intact.

    `None` is preserved rather than encrypted, because on an update `None` means "leave the
    stored value alone" — encrypting it would turn every PATCH that does not re-send a
    password into one that wipes it.

    `ssh_host_key_fingerprint` is **not** encrypted, and that is not an oversight: a host key
    fingerprint is a public digest. The whole point of storing it is comparing it to what a
    host presents mid-handshake, and `WHMServer.to_safe_dict` returns it to the panel so
    an operator can read it — a value shown in a UI is not one to hold ciphertext.
    """
    return replace(
        values,
        ssh_password=_maybe_encrypt(values.ssh_password, cipher=cipher),
        ssh_private_key=_maybe_encrypt(values.ssh_private_key, cipher=cipher),
        ssh_private_key_passphrase=_maybe_encrypt(values.ssh_private_key_passphrase, cipher=cipher),
    )


def _maybe_encrypt(plaintext: str | None, *, cipher: SecretCipher) -> str | None:
    """`cipher.encrypt_text(plaintext)`, or `None` for an absent value."""
    if plaintext is None:
        return None
    return cipher.encrypt_text(plaintext)


def _require_reseller_name_matches(*, name: str, api_username: str) -> None:
    """Raise unless a reseller row's `name` is its `api_username`, normalised.

    Called with the values the row will *hold*, never with the patch: an update that flips the
    flag on and touches nothing else is exactly the write this rule exists to stop, and reading
    the request body alone would let it through. Both sides go through
    `normalize_whm_identity`, which is the same comparison an account CHANGE makes against the
    account's `owner` — two spellings of "equal" here would mean a row this accepts is
    one that path still refuses.
    """
    if normalize_whm_identity(name) != normalize_whm_identity(api_username):
        raise WHMResellerCredentialNameMismatchError(
            f"reseller `whm_servers` row `{name}` does not match `api_username` `{api_username}`"
        )


def _ssh_metadata(server: WHMServer | PMGServer) -> dict[str, Any]:
    """The SSH facts an audit event may carry: shape, never material."""
    return {
        "ssh_username": server.ssh_username,
        "ssh_port": server.ssh_port,
        "has_ssh_password": bool(server.ssh_password),
        "has_ssh_private_key": bool(server.ssh_private_key),
        "host_key_pinned": bool(server.ssh_host_key_fingerprint),
    }


# --- What differed between the three tables ---


def _encrypt_whm(spec: WHMSpecT, cipher: SecretCipher) -> WHMSpecT:
    """A WHM create or patch with its secrets encrypted.

    `_maybe_encrypt` covers both: on a create `api_token` is always set, and on a patch `None`
    means "leave the stored value alone", which encrypting would turn into "wipe it".
    """
    return replace(
        spec,
        api_token=_maybe_encrypt(spec.api_token, cipher=cipher),
        ssh=encrypt_ssh_credentials(spec.ssh, cipher=cipher),
    )


def _encrypt_proxmox(spec: ProxmoxSpecT, cipher: SecretCipher) -> ProxmoxSpecT:
    return replace(spec, api_token_secret=_maybe_encrypt(spec.api_token_secret, cipher=cipher))


def _encrypt_pmg(spec: PMGSpecT, cipher: SecretCipher) -> PMGSpecT:
    return replace(spec, ssh=encrypt_ssh_credentials(spec.ssh, cipher=cipher))


def _whm_metadata(server: WHMServer) -> dict[str, Any]:
    return {
        "base_url": server.base_url,
        "api_username": server.api_username,
        "verify_ssl": server.verify_ssl,
        # Which credential class this row now holds. An account CHANGE is refused or allowed
        # by the owner compare, so "when did this row become a reseller credential, and who
        # said so" is a question the trail must answer.
        "is_reseller_credential": server.is_reseller_credential,
        **_ssh_metadata(server),
    }


def _proxmox_metadata(server: ProxmoxServer) -> dict[str, Any]:
    return {
        "base_url": server.base_url,
        "api_token_id": server.api_token_id,
        "verify_ssl": server.verify_ssl,
    }


def _pmg_metadata(server: PMGServer) -> dict[str, Any]:
    return {"ssh_host": server.ssh_host, **_ssh_metadata(server)}


def _no_check(current: Any, spec: Any) -> None:
    """Proxmox and PMG have no cross-field rule on the row a write produces."""


def _check_whm_reseller_name(
    current: WHMServer | None, spec: WHMServerCreate | WHMServerUpdate
) -> None:
    """The owner-name-match rule, against the row the write *produces*.

    `current` is `None` on create, where every field the rule reads is required, so the
    fallbacks below only ever run on update — which is the path a patch that flips the flag on
    and touches nothing else arrives by, and the one reading the request body alone would let
    through.
    """
    is_reseller = spec.is_reseller_credential
    if is_reseller is None:
        is_reseller = current.is_reseller_credential  # update only; `current` is set there
    if not is_reseller:
        return
    _require_reseller_name_matches(
        name=spec.name if spec.name is not None else current.name,
        api_username=(spec.api_username if spec.api_username is not None else current.api_username),
    )


# --- One policy per table, one service over all three ---


@dataclass(frozen=True)
class ServerAdminPolicy(Generic[RowT, CreateT, PatchT]):
    """Everything that differed between the three deleted CRUD services.

    A frozen constant per vertical rather than six constructor keywords at three wiring sites:
    the point of one service is that the three verticals' differences sit side by side, and
    that only happens if they are written in one file.
    """

    table: str
    not_found: type[ServerInventoryError]
    name_exists: type[ServerInventoryError]
    event_created: str
    event_updated: str
    event_deleted: str
    encrypt: Callable[[Any, SecretCipher], Any]
    metadata: Callable[[Any], dict[str, Any]]
    # Runs before the name query on both writes, with the stored row (`None` on create) and the
    # incoming spec. WHM's owner-name-match rule is the only user.
    check: Callable[[Any, Any], None]


WHM_ADMIN_POLICY: Final = ServerAdminPolicy(
    table="whm_servers",
    not_found=WHMServerNotFoundError,
    name_exists=WHMServerNameExistsError,
    event_created=EVENT_WHM_SERVER_CREATED,
    event_updated=EVENT_WHM_SERVER_UPDATED,
    event_deleted=EVENT_WHM_SERVER_DELETED,
    encrypt=_encrypt_whm,
    metadata=_whm_metadata,
    check=_check_whm_reseller_name,
)

PROXMOX_ADMIN_POLICY: Final = ServerAdminPolicy(
    table="proxmox_servers",
    not_found=ProxmoxServerNotFoundError,
    name_exists=ProxmoxServerNameExistsError,
    event_created=EVENT_PROXMOX_SERVER_CREATED,
    event_updated=EVENT_PROXMOX_SERVER_UPDATED,
    event_deleted=EVENT_PROXMOX_SERVER_DELETED,
    encrypt=_encrypt_proxmox,
    metadata=_proxmox_metadata,
    check=_no_check,
)

PMG_ADMIN_POLICY: Final = ServerAdminPolicy(
    table="pmg_servers",
    not_found=PMGServerNotFoundError,
    name_exists=PMGServerNameExistsError,
    event_created=EVENT_PMG_SERVER_CREATED,
    event_updated=EVENT_PMG_SERVER_UPDATED,
    event_deleted=EVENT_PMG_SERVER_DELETED,
    encrypt=_encrypt_pmg,
    metadata=_pmg_metadata,
    check=_no_check,
)


class ServerAdminService(Generic[RowT, CreateT, PatchT]):
    """List / create / update / delete for one server-inventory table.

    A row with no SSH credentials is still creatable on every table that has them: an admin
    filling in a host before the key arrives is a legitimate order of operations, and
    `resolve_*_ssh_config` answers `ssh_not_configured` in the meantime, which names the
    remedy. Refusing here would add a rule no invariant asks for; the same call
    `core.auth.mcp_token_service` makes about minting for an inactive operator.
    """

    def __init__(
        self,
        *,
        repository: ServerAdminRepository[RowT, CreateT, PatchT],
        policy: ServerAdminPolicy[RowT, CreateT, PatchT],
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._cipher = cipher
        self._audit = audit_sink

    async def list_servers(self) -> Sequence[RowT]:
        """Every server of this table, ordered by name in SQL (see the read repository)."""
        return await self._repository.list_servers()

    async def create(self, spec: CreateT, *, actor_email: str | None = None) -> RowT:
        """Insert one server. `spec`'s secrets are **plaintext** on the way in."""
        # Before the name query: the cross-field rule needs no round trip, and a row it
        # refuses is refused whether or not the name is also taken.
        self._policy.check(None, spec)
        if await self._repository.name_taken(spec.name):
            raise self._policy.name_exists(f"`{self._policy.table}.name` `{spec.name}` is taken")

        server = await self._repository.create(self._policy.encrypt(spec, self._cipher))
        await self._record(self._policy.event_created, actor_email, server)
        await self._repository.commit()
        return server

    async def update(
        self, server_id: UUID, patch: PatchT, *, actor_email: str | None = None
    ) -> RowT:
        """Apply a partial edit. Absent row → 404 before the name check, not after."""
        current = await self._repository.get_by_id(server_id)
        if current is None:
            raise self._policy.not_found(f"no `{self._policy.table}` row `{server_id}`")

        self._policy.check(current, patch)
        if patch.name is not None and await self._repository.name_taken(
            patch.name, exclude_id=server_id
        ):
            raise self._policy.name_exists(f"`{self._policy.table}.name` `{patch.name}` is taken")

        server = await self._repository.update(server_id, self._policy.encrypt(patch, self._cipher))
        if server is None:
            # Reachable only if the row was deleted between the guard above and here. The
            # answer is still 404, and raising beats returning `None` from a `-> RowT`.
            raise self._policy.not_found(f"no `{self._policy.table}` row `{server_id}`")

        await self._record(self._policy.event_updated, actor_email, server)
        await self._repository.commit()
        return server

    async def delete(self, server_id: UUID, *, actor_email: str | None = None) -> None:
        """Delete one server. Its tool grants are unaffected — grants name tools, not hosts."""
        server = await self._repository.get_by_id(server_id)
        if server is None or not await self._repository.delete(server_id):
            raise self._policy.not_found(f"no `{self._policy.table}` row `{server_id}`")

        await self._record(self._policy.event_deleted, actor_email, server)
        await self._repository.commit()

    async def _record(self, event_type: str, actor_email: str | None, server: RowT) -> None:
        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=server.name,
                metadata={
                    "server_id": str(server.id),
                    "server_name": server.name,
                    **self._policy.metadata(server),
                },
            )
        )
