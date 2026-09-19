"""SQL behind server-inventory *writes*.

The half `core.servers.whm_repository`, `pmg_repository` and `proxmox_repository` each
deferred to this task in prose: `create`, `update`, `delete`, and the one-column write the
validate flow makes when it captures a host key.

**Separate classes from the read repositories, not extra methods on them.** The MCP tool path
holds `SQLServerRepository` to resolve a server reference, and it must not hold
an object that can delete one. That is the split `noa_api.api.deps` already makes twice —
`SQLApprovalCardRepository` has no `commit` and issues no statement that is not a `SELECT`, so
a render path cannot grant an authorization — spelled here against inventory. The reads are
not re-implemented either: each class **composes** its read repository, so `ORDER BY name`
lives in one place and the admin list, the tool result and the ambiguity `choices` cannot
disagree about row three.

**Ciphertext comes in.** Every secret field on `WHMServerCreate`, `ProxmoxServerUpdate` and
the rest is already `enc:v1:fernet:…` — `core.servers.admin_service` holds the `SecretCipher`
and encrypts before building these value objects. Nothing in this module can
encrypt, which is the point: a future caller cannot reach a write path that stores a
plaintext, because there is none.

**Partial update, spelled as `None` plus explicit clear flags.** `noa-old`'s shape, kept
verbatim because the ported panel already sends it
(`apps/admin-web/src/lib/admin/*/[system]-form.ts::build*UpdatePayload`): a field left `None`
means "leave the stored value alone", and setting it to NULL takes a `clear_*` flag. The
alternative — `None` means NULL — makes it impossible to PATCH a name without re-sending
every credential, which is the whole reason the panel's secret fields are write-only.

**The host-key pin is invalidated here, not by the caller.** A pin belongs to one
`(host, port)` pair, so a PATCH that moves either has invalidated it. Doing that where the
old row is already loaded makes it a fact of construction rather than an obligation a second
caller can forget — the same construction-not-obligation argument made one table over, twice.
Two consequences worth stating:

- the comparison is on the whole `base_url` (WHM) / `ssh_host` (PMG), not on the SSH hostname
  derived from it. Re-deriving that hostname here would be a second implementation of
  `resolve_whm_ssh_config`'s `urlsplit(...).hostname`, and the cost of the looser test
  is one extra Validate press when only an API port moved;
- an explicit `ssh_host_key_fingerprint` in the same patch **wins**. An operator who changed
  the host and pasted the new host's key in one save meant both (PMG's form offers that
  field; WHM's does not).

`commit()` sits on every Protocol here rather than being left to the route, because
`noa_api.api.deps.get_db_session` does not commit and a repository that only `flush()`es
answers 200 over a rollback — the same flush-only rollback hole, one table apart, twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from typing import Any, Final, Generic, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.servers.repository import ServerModelT, SQLServerRepository

# The `SSHCredentialsMixin` columns (`core.db.models`), in one tuple so the value walk and the
# clear walk cannot drift apart. Each has a matching `clear_<name>` flag on the patch below.
SSH_VALUE_FIELDS: Final[tuple[str, ...]] = (
    "ssh_username",
    "ssh_port",
    "ssh_password",
    "ssh_private_key",
    "ssh_private_key_passphrase",
    "ssh_host_key_fingerprint",
)


@dataclass(frozen=True)
class SSHCredentials:
    """The SSH columns WHM and PMG share, as a create carries them.

    Every secret is **already encrypted** (see the module docstring). `ssh_username` `None` means
    "connect as root", which is coupled to the sudo-prefix rule: `resolve_*_ssh_config` resolves a
    blank column to `root` and therefore adds no `sudo -n`.
    """

    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = None


@dataclass(frozen=True)
class SSHCredentialsPatch(SSHCredentials):
    """`SSHCredentials` plus the seven ways to set one of them back to NULL.

    `clear_ssh_configuration` is the whole-block clear the panel sends when an admin turns SSH
    off on a WHM server; the six specific flags are what it sends when one field is emptied.
    Clears are applied **before** values, `noa-old`'s order, so a patch carrying both a clear
    and a replacement for one field ends up with the replacement.
    """

    clear_ssh_configuration: bool = False
    clear_ssh_username: bool = False
    clear_ssh_port: bool = False
    clear_ssh_password: bool = False
    clear_ssh_private_key: bool = False
    clear_ssh_private_key_passphrase: bool = False
    clear_ssh_host_key_fingerprint: bool = False


@dataclass(frozen=True)
class WHMServerCreate:
    """A new `whm_servers` row. `api_token` is ciphertext.

    `is_reseller_credential` defaults `false`, which is the column's own default and the
    truthful value for a root token: a caller written before the flag existed keeps
    inserting root rows.
    """

    name: str
    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    is_reseller_credential: bool = False
    ssh: SSHCredentials = SSHCredentials()


@dataclass(frozen=True)
class WHMServerUpdate:
    """A partial `whm_servers` edit. `None` means "leave alone" (module docstring)."""

    name: str | None = None
    base_url: str | None = None
    api_username: str | None = None
    api_token: str | None = None
    verify_ssl: bool | None = None
    # `None` means "leave alone" here too, so a PATCH that renames a row does not silently
    # turn its reseller flag off. The service is what refuses the combinations the
    # owner-name-match rule bans.
    is_reseller_credential: bool | None = None
    ssh: SSHCredentialsPatch = SSHCredentialsPatch()


@dataclass(frozen=True)
class ProxmoxServerCreate:
    """A new `proxmox_servers` row. `api_token_secret` is ciphertext.

    No SSH block: Proxmox is an HTTP API and nothing else, so the table has no SSH
    columns to write and no host key to pin.
    """

    name: str
    base_url: str
    api_token_id: str
    api_token_secret: str
    verify_ssl: bool


@dataclass(frozen=True)
class ProxmoxServerUpdate:
    """A partial `proxmox_servers` edit."""

    name: str | None = None
    base_url: str | None = None
    api_token_id: str | None = None
    api_token_secret: str | None = None
    verify_ssl: bool | None = None


@dataclass(frozen=True)
class PMGServerCreate:
    """A new `pmg_servers` row.

    `ssh_host` is required and there is no `base_url` or `verify_ssl`: PMG is reached over SSH
    + `pmgsh` only.
    """

    name: str
    ssh_host: str
    ssh: SSHCredentials = SSHCredentials()


@dataclass(frozen=True)
class PMGServerUpdate:
    """A partial `pmg_servers` edit."""

    name: str | None = None
    ssh_host: str | None = None
    ssh: SSHCredentialsPatch = SSHCredentialsPatch()


# --- Shared field application ---


def apply_ssh_fields(row: WHMServer | PMGServer, patch: SSHCredentials) -> None:
    """Write a patch's SSH block onto `row`: clears first, then values.

    Takes `SSHCredentials` so a create can pass one, and reads the clear flags off the
    subclass when they are there — a create has nothing to clear, and asking for the flags it
    does not carry would be the only reason to have two of this function.
    """
    if getattr(patch, "clear_ssh_configuration", False):
        for field_name in SSH_VALUE_FIELDS:
            setattr(row, field_name, None)

    for field_name in SSH_VALUE_FIELDS:
        if getattr(patch, f"clear_{field_name}", False):
            setattr(row, field_name, None)

    for field_name in SSH_VALUE_FIELDS:
        value = getattr(patch, field_name)
        if value is not None:
            setattr(row, field_name, value)


def _pin_invalidated(
    row: WHMServer | PMGServer, *, host_changed: bool, patch: SSHCredentialsPatch
) -> bool:
    """True when this patch moves the `(host, port)` the stored pin belongs to.

    Evaluated against the row **before** the patch is applied, which is why it takes both.
    `clear_ssh_configuration` counts because it drops the credentials the pin was captured
    with; a port set to the value it already had does not.
    """
    if host_changed:
        return True
    if patch.clear_ssh_configuration or patch.clear_ssh_port:
        return True
    return patch.ssh_port is not None and patch.ssh_port != row.ssh_port


def apply_ssh_fields_with_pin_rule(
    row: WHMServer | PMGServer, *, host_changed: bool, patch: SSHCredentialsPatch
) -> None:
    """`apply_ssh_fields`, plus "a moved host loses its pin unless the patch names a new one"."""
    invalidated = _pin_invalidated(row, host_changed=host_changed, patch=patch)
    apply_ssh_fields(row, patch)
    if invalidated and patch.ssh_host_key_fingerprint is None:
        row.ssh_host_key_fingerprint = None


# --- One admin repository, three tables ---

RowT = TypeVar("RowT")
CreateT = TypeVar("CreateT")
PatchT = TypeVar("PatchT")


class ServerAdminRepository(Protocol[RowT, CreateT, PatchT]):
    """What `ServerAdminService` needs from one inventory table.

    `name_taken` takes `exclude_id` rather than being two methods: an update has to allow a
    row to keep its own name, and a caller passing the wrong id would otherwise refuse every
    save that did not rename.

    Three parameters rather than three Protocols: the methods were byte-identical across WHM,
    Proxmox and PMG and only the row and spec types differed, so the difference is the
    parameter list.
    """

    async def list_servers(self) -> Sequence[RowT]: ...

    async def get_by_id(self, server_id: UUID) -> RowT | None: ...

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool: ...

    async def create(self, spec: CreateT) -> RowT: ...

    async def update(self, server_id: UUID, patch: PatchT) -> RowT | None: ...

    async def delete(self, server_id: UUID) -> bool: ...

    async def commit(self) -> None: ...


def _column_values(spec: Any, *, skip_none: bool) -> dict[str, Any]:
    """A create/update dataclass as column keywords. `ssh` is the one field that is not one.

    Every other field name on the six spec dataclasses is its column name, which is what lets
    one loop replace three hand-written `if patch.X is not None: row.X = patch.X` chains.
    `skip_none=True` is the update contract — `None` means "leave alone" — and
    `skip_none=False` is create, where no field is ever `None` (the three `*Create` dataclasses
    have no optional scalar).
    """
    values: dict[str, Any] = {}
    for spec_field in fields(spec):
        if spec_field.name == "ssh":
            continue
        value = getattr(spec, spec_field.name)
        if skip_none and value is None:
            continue
        values[spec_field.name] = value
    return values


class SQLServerAdminRepository(Generic[ServerModelT, CreateT, PatchT]):
    """`ServerAdminRepository` over one `AsyncSession`, for one inventory table.

    `host_field` names the column a moved host is detected on — `base_url` for WHM,
    `ssh_host` for PMG — and `None` means the table has no SSH block at all, which is
    Proxmox: an HTTP API and nothing else, so no credentials to apply and no pin to invalidate.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        model: type[ServerModelT],
        host_field: str | None = None,
    ) -> None:
        self._session = session
        self._model = model
        self._host_field = host_field
        # Composed, not re-implemented: `ORDER BY name` and the `None`-for-absent contract
        # live in the read repository and are covered by its own live test.
        self._reads = SQLServerRepository(session, model=model)

    async def list_servers(self) -> Sequence[ServerModelT]:
        return await self._reads.list_servers()

    async def get_by_id(self, server_id: UUID) -> ServerModelT | None:
        return await self._reads.get_by_id(server_id)

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        return await _name_taken(self._session, self._model, name=name, exclude_id=exclude_id)

    async def create(self, spec: CreateT) -> ServerModelT:
        server = self._model(**_column_values(spec, skip_none=False))
        if self._host_field is not None:
            apply_ssh_fields(server, spec.ssh)  # type: ignore[attr-defined]
        return await _insert(self._session, server)

    async def update(self, server_id: UUID, patch: PatchT) -> ServerModelT | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None

        # Before the scalar walk, and reading the row's *pre-patch* host: the pin rule asks
        # whether this patch moves the `(host, port)` the stored fingerprint belongs to.
        if self._host_field is not None:
            new_host = getattr(patch, self._host_field)
            apply_ssh_fields_with_pin_rule(
                server,
                host_changed=new_host is not None and new_host != getattr(server, self._host_field),
                patch=patch.ssh,  # type: ignore[attr-defined]
            )
        for column, value in _column_values(patch, skip_none=True).items():
            setattr(server, column, value)

        return await _refresh(self._session, server)

    async def delete(self, server_id: UUID) -> bool:
        return await _delete(self._session, await self.get_by_id(server_id))

    async def commit(self) -> None:
        await self._session.commit()


# --- The one column the validate flow may write ---
#
# A separate class rather than two more methods above, and this is the same call
# `noa_api.api.deps` makes for `SQLApprovalCardRepository`: a reachability probe must not hold
# an object that can rewrite a credential or delete a row. What it may do is read the server it
# was asked about and store the host key that server presented — nothing else. Proxmox has no
# equivalent because it has no SSH path to pin; its validate reads through the
# `SELECT`-only `SQLServerRepository` and writes nothing at all.


class SQLHostKeyPinRepository(Generic[ServerModelT]):
    """Read one inventory row; write only `ssh_host_key_fingerprint`."""

    def __init__(self, session: AsyncSession, *, model: type[ServerModelT]) -> None:
        self._session = session
        self._reads = SQLServerRepository(session, model=model)

    async def get_by_id(self, server_id: UUID) -> ServerModelT | None:
        return await self._reads.get_by_id(server_id)

    async def set_host_key_fingerprint(self, server_id: UUID, fingerprint: str | None) -> bool:
        return await _set_fingerprint(self._session, await self.get_by_id(server_id), fingerprint)

    async def commit(self) -> None:
        await self._session.commit()


# --- The five statements all three share ---


async def _name_taken(
    session: AsyncSession,
    model: type[WHMServer] | type[ProxmoxServer] | type[PMGServer],
    *,
    name: str,
    exclude_id: UUID | None,
) -> bool:
    """Is `name` already held by some other row of `model`?

    **Case-insensitive, while the unique index is not**, and that asymmetry is the point.
    `core.servers.reference` records the consequence of the gap: Postgres uniqueness is
    case-sensitive, so `Node1` and `node1` can both exist and `NODE1` then matches both —
    a permanent `host_ambiguous` for a reference an admin will keep typing. The write
    side is the only place that can stop it, so it refuses here.

    A pre-check rather than only catching the constraint, for the reason
    `core.auth.mcp_token_service` states about foreign keys: an `IntegrityError` reaches the
    client as a 500 and reads like a broken server instead of a name already in use. The
    constraint stays the backstop for the exact-case race between two concurrent creates.
    """
    statement = select(model.id).where(func.lower(model.name) == name.lower()).limit(1)
    if exclude_id is not None:
        statement = statement.where(model.id != exclude_id)
    return (await session.execute(statement)).first() is not None


async def _insert(session: AsyncSession, server: WHMServer | ProxmoxServer | PMGServer):  # type: ignore[no-untyped-def]
    """`add` + `flush` + `refresh`, so the caller sees the server-generated columns.

    `refresh` rather than trusting the instance: `id` is generated by Postgres and
    `created_at`/`updated_at` by their column defaults — none of the three is the caller's —
    and the response shape carries all three.
    """
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def _refresh(session: AsyncSession, server: WHMServer | ProxmoxServer | PMGServer):  # type: ignore[no-untyped-def]
    """`flush` + `refresh` after an in-place edit, so `updated_at` is the stored value."""
    await session.flush()
    await session.refresh(server)
    return server


async def _delete(
    session: AsyncSession, server: WHMServer | ProxmoxServer | PMGServer | None
) -> bool:
    """Delete `server` if it is there. `False` is the caller's 404."""
    if server is None:
        return False
    await session.delete(server)
    await session.flush()
    return True


async def _set_fingerprint(
    session: AsyncSession,
    server: WHMServer | PMGServer | None,
    fingerprint: str | None,
) -> bool:
    """Write only `ssh_host_key_fingerprint`. The validate flow's one write.

    Narrow on purpose: this is the method the validation service holds, and it is the only
    column that service may change. Handing it a full `update` would let a reachability probe
    rewrite a credential.
    """
    if server is None:
        return False
    server.ssh_host_key_fingerprint = fingerprint
    await session.flush()
    return True
