"""Admin server management: WHM / Proxmox / PMG CRUD + validate (T54, I.admin-api).

**Fifteen routes and no policy.** Every rule lives in `core.servers.admin_service` (the
case-insensitive name check, the one encryption site, the audit event, the commit V100
requires) or in `core.servers.validation` (the trust-on-first-use rule, V82). A handler here
resolves the actor, calls one service method, and shapes the answer — the split
`routes/admin_users.py` and `routes/mcp_tokens.py` both state, and for their reason: these are
properties of a row, so a future CLI or fixture gets them too.

**Three routers, one module**, the argument `routes/mcp_tokens.py` makes for its two. The three
verticals are three tables with three column sets, but they share the two things that must not
drift: `ValidateServerResponse`, so an operator reads the same shape whichever page they are on,
and the field validators below, so `name` means one thing everywhere.

**Request shapes are the ported panel's, field for field**
(`apps/admin-web/src/lib/admin/*/[system]-form.ts::build*Payload`). Three asymmetries are
inherited rather than invented, and each is what its form sends:

- WHM's create carries no `ssh_host_key_fingerprint` and its update carries only the *clear*
  flag — the WHM form has no fingerprint input, it captures the pin by validating.
- PMG's create and update both carry the value, because its form does expose the field: an
  operator who already knows a node's key may pin it before the first connection.
- Proxmox has no SSH block at all (I.ext).

**Secrets are write-only.** They appear in request bodies and in no response — the response
models carry `has_api_token`, `has_ssh_password`, `has_ssh_private_key` instead, which is what
drives the panel's "keep or replace" copy. The models are built field by field off
`to_safe_dict()`, which is the row's one sanctioned outward serialization
(`core.servers.whm_repository`): the dict cannot contain a credential, and the explicit mapping
means a column added later is not published until somebody decides to publish it (V2, V8 —
`routes/admin_users.py::_to_user_response`'s reason).

**`POST …/validate` answers 200 even when the server is unreachable.** The operator asked
whether it answers; "no, `ssh_timeout`" is that question's answer, and the panel reads
`result.ok` to draw a status chip. Only an absent row is a refusal (404). See
`core.servers.validation` and the `SSHExecutionError` note in `noa_api.api.errors`.

**Nothing here builds an `HTTPException`.** Refusals are `ServerInventoryError` subclasses and
the shared handler owns status, body and `request_id`.

**`admin` is checked per handler, not once on a router.** `AdminUserDep` is a parameter on all
fifteen, so the actor whose email lands in the audit event and the gate that authorises the call
are the same read, and V6's row re-read comes with it: a demoted admin loses these routes
on their next request.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.servers.admin_repository import (
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    SSHCredentials,
    SSHCredentialsPatch,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.naming import (
    normalize_https_base_url,
    normalize_ssh_host,
    validate_server_name,
)
from core.servers.validation import ServerValidationResult
from noa_api.api.deps import (
    AdminUserDep,
    PMGServerAdminServiceDep,
    PMGServerValidationServiceDep,
    ProxmoxServerAdminServiceDep,
    ProxmoxServerValidationServiceDep,
    WHMServerAdminServiceDep,
    WHMServerValidationServiceDep,
)
from noa_api.api.serialization import iso_or_none

whm_router = APIRouter(prefix="/admin/whm/servers", tags=["admin"])
proxmox_router = APIRouter(prefix="/admin/proxmox/servers", tags=["admin"])
pmg_router = APIRouter(prefix="/admin/pmg/servers", tags=["admin"])

# WHM API tokens are issued to a cPanel user, so the username follows cPanel's own rule.
# `noa-old`'s expression verbatim.
WHM_API_USERNAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


# --- Field validators, shared by all three verticals ---


def _stripped(value: object) -> object:
    """Trim a string; blank becomes `None`.

    Blank-to-`None` is what makes V21 fall out of the type rather than out of a check: on a
    create the field is required, so `None` is a 422; on a patch `None` already means "leave
    the stored value alone", which is exactly what an emptied input should mean.
    """
    if isinstance(value, str):
        return value.strip() or None
    return value


def _required(check: Callable[..., str], label: str) -> Callable[[str], str]:
    """Bind `check` to one system's label. A closure rather than `functools.partial` so
    pydantic sees an ordinary one-argument validator."""

    def validator(value: str) -> str:
        return check(value, label=label)

    return validator


def _optional(check: Callable[..., str], label: str) -> Callable[[str | None], str | None]:
    """`_required`, but `None` passes through — the patch case."""

    def validator(value: str | None) -> str | None:
        return None if value is None else check(value, label=label)

    return validator


def _whm_api_username(value: str) -> str:
    if WHM_API_USERNAME_PATTERN.fullmatch(value) is None:
        raise ValueError("String should be a valid WHM API username")
    return value


def _optional_whm_api_username(value: str | None) -> str | None:
    return None if value is None else _whm_api_username(value)


WHMName = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(validate_server_name, "WHM"))
]
WHMNameOpt = Annotated[
    str | None, BeforeValidator(_stripped), AfterValidator(_optional(validate_server_name, "WHM"))
]
WHMBaseUrl = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(normalize_https_base_url, "WHM"))
]
WHMBaseUrlOpt = Annotated[
    str | None,
    BeforeValidator(_stripped),
    AfterValidator(_optional(normalize_https_base_url, "WHM")),
]
WHMApiUsername = Annotated[str, BeforeValidator(_stripped), AfterValidator(_whm_api_username)]
WHMApiUsernameOpt = Annotated[
    str | None, BeforeValidator(_stripped), AfterValidator(_optional_whm_api_username)
]

ProxmoxName = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(validate_server_name, "Proxmox"))
]
ProxmoxNameOpt = Annotated[
    str | None,
    BeforeValidator(_stripped),
    AfterValidator(_optional(validate_server_name, "Proxmox")),
]
ProxmoxBaseUrl = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(normalize_https_base_url, "Proxmox"))
]
ProxmoxBaseUrlOpt = Annotated[
    str | None,
    BeforeValidator(_stripped),
    AfterValidator(_optional(normalize_https_base_url, "Proxmox")),
]

PMGName = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(validate_server_name, "PMG"))
]
PMGNameOpt = Annotated[
    str | None, BeforeValidator(_stripped), AfterValidator(_optional(validate_server_name, "PMG"))
]
PMGSshHost = Annotated[
    str, BeforeValidator(_stripped), AfterValidator(_required(normalize_ssh_host, "PMG"))
]
PMGSshHostOpt = Annotated[
    str | None, BeforeValidator(_stripped), AfterValidator(_optional(normalize_ssh_host, "PMG"))
]

# Free-text fields that only need trimming: secrets, the SSH username, the fingerprint. A
# secret is not normalised beyond that — a password may legitimately contain anything.
Trimmed = Annotated[str | None, BeforeValidator(_stripped)]
# The required version, for a secret a create cannot do without. `_stripped` turns a
# whitespace-only value into `None`, which a required field refuses — so V21's rule reaches the
# secrets too, and `min_length=1` alone (which " " satisfies) is not the guard.
TrimmedRequired = Annotated[str, BeforeValidator(_stripped)]
# 1-65535, refused at the schema so a bad port never reaches `asyncssh`.
SshPort = Annotated[int | None, Field(ge=1, le=65535)]


# --- Shared response models ---


class ValidateServerResponse(BaseModel):
    """`POST …/validate` on any of the three. 200 whatever the answer (module docstring).

    `error_code` is `null` on success, never `""`: an empty string is a value a client could
    mistake for a code it does not recognise.
    """

    ok: bool
    message: str
    error_code: str | None = None


class DeleteServerResponse(BaseModel):
    """`{ok: true}`. The row is gone, so there is nothing to return."""

    ok: bool


def _validation_response(result: ServerValidationResult) -> ValidateServerResponse:
    return ValidateServerResponse(
        ok=result.ok, message=result.message, error_code=result.error_code
    )


def _ssh_view(safe: dict[str, Any]) -> dict[str, Any]:
    """The SSH half of a safe view, as both SSH-bearing response models publish it."""
    return {
        "ssh_username": safe["ssh_username"],
        "ssh_port": safe["ssh_port"],
        "ssh_host_key_fingerprint": safe["ssh_host_key_fingerprint"],
        "has_ssh_password": safe["has_ssh_password"],
        "has_ssh_private_key": safe["has_ssh_private_key"],
    }


def _ssh_credentials(payload: WHMServerCreateRequest | PMGServerCreateRequest) -> SSHCredentials:
    """A create's SSH block. Secrets are plaintext here — the service encrypts."""
    return SSHCredentials(
        ssh_username=payload.ssh_username,
        ssh_port=payload.ssh_port,
        ssh_password=payload.ssh_password,
        ssh_private_key=payload.ssh_private_key,
        ssh_private_key_passphrase=payload.ssh_private_key_passphrase,
        ssh_host_key_fingerprint=getattr(payload, "ssh_host_key_fingerprint", None),
    )


def _ssh_patch(payload: WHMServerUpdateRequest | PMGServerUpdateRequest) -> SSHCredentialsPatch:
    """An update's SSH block, clear flags included."""
    return SSHCredentialsPatch(
        ssh_username=payload.ssh_username,
        ssh_port=payload.ssh_port,
        ssh_password=payload.ssh_password,
        ssh_private_key=payload.ssh_private_key,
        ssh_private_key_passphrase=payload.ssh_private_key_passphrase,
        ssh_host_key_fingerprint=getattr(payload, "ssh_host_key_fingerprint", None),
        clear_ssh_configuration=payload.clear_ssh_configuration,
        clear_ssh_username=payload.clear_ssh_username,
        clear_ssh_port=payload.clear_ssh_port,
        clear_ssh_password=payload.clear_ssh_password,
        clear_ssh_private_key=payload.clear_ssh_private_key,
        clear_ssh_private_key_passphrase=payload.clear_ssh_private_key_passphrase,
        clear_ssh_host_key_fingerprint=payload.clear_ssh_host_key_fingerprint,
    )


class SSHClearFlags(BaseModel):
    """The seven ways a patch sets an SSH column back to NULL.

    A base rather than a repeated block: WHM's form sends `clear_ssh_configuration` when an
    admin turns SSH off, PMG's sends the specific ones, and both send the rest. Defaults are
    `False`, so a patch that mentions none of them changes none of them.
    """

    clear_ssh_configuration: bool = False
    clear_ssh_username: bool = False
    clear_ssh_port: bool = False
    clear_ssh_password: bool = False
    clear_ssh_private_key: bool = False
    clear_ssh_private_key_passphrase: bool = False
    clear_ssh_host_key_fingerprint: bool = False


# --- WHM ---


class WHMServerResponse(BaseModel):
    """One `whm_servers` row as every read path returns it.

    No `api_token` and no SSH secret — absent, not redacted. `has_api_token` and the two SSH
    booleans are what the panel needs: they say whether a stored value exists so the form can
    offer "keep" instead of demanding a re-entry.
    """

    id: str
    name: str
    base_url: str
    api_username: str
    has_api_token: bool
    verify_ssl: bool
    # Published because the form draws a checkbox from it, and because an operator who cannot
    # see the flag cannot tell why a rename was refused.
    is_reseller_credential: bool
    ssh_username: str | None
    ssh_port: int | None
    ssh_host_key_fingerprint: str | None
    has_ssh_password: bool
    has_ssh_private_key: bool
    created_at: str | None
    updated_at: str | None


class WHMServersResponse(BaseModel):
    """`GET /admin/whm/servers`. Ordered by name in SQL, so the list is stable."""

    servers: list[WHMServerResponse]


class WHMServerDetailResponse(BaseModel):
    """`{server: …}` — what create and update answer, and what the panel threads back in."""

    server: WHMServerResponse


class WHMServerCreateRequest(BaseModel):
    """`POST /admin/whm/servers`.

    `api_token` is required: a WHM row with no token can answer no account read, and the
    validate route would have nothing to probe. SSH is optional — a WHM server used only for
    account reads is a legitimate configuration (`core.servers.validation`).
    """

    name: WHMName
    base_url: WHMBaseUrl
    api_username: WHMApiUsername
    api_token: TrimmedRequired = Field(min_length=1)
    verify_ssl: bool = True
    # Defaulted rather than required: a root credential is the common row and the column's
    # default is `false`. A `true` value obliges `name` == `api_username`, which
    # `core.servers.admin_service` refuses with 409 `whm_reseller_credential_name_mismatch`.
    is_reseller_credential: bool = False
    ssh_username: Trimmed = None
    ssh_port: SshPort = None
    ssh_password: Trimmed = None
    ssh_private_key: Trimmed = None
    ssh_private_key_passphrase: Trimmed = None


class WHMServerUpdateRequest(SSHClearFlags):
    """`PATCH /admin/whm/servers/{id}`. Every value optional; `None` means "leave alone".

    No `ssh_host_key_fingerprint` value field, only the clear flag, because the WHM form has no
    fingerprint input: the pin arrives from a validate capture. Clearing it here is how an
    operator asks for a re-capture after a legitimate key rotation.
    """

    name: WHMNameOpt = None
    base_url: WHMBaseUrlOpt = None
    api_username: WHMApiUsernameOpt = None
    api_token: Trimmed = None
    verify_ssl: bool | None = None
    # `None` means "leave alone", so a PATCH that renames a row cannot silently unmark it. The
    # service checks V109(b) against the row the patch produces, which is why flipping this to
    # `true` alone can be refused (409 `whm_reseller_credential_name_mismatch`).
    is_reseller_credential: bool | None = None
    ssh_username: Trimmed = None
    ssh_port: SshPort = None
    ssh_password: Trimmed = None
    ssh_private_key: Trimmed = None
    ssh_private_key_passphrase: Trimmed = None
    # No `ssh_host_key_fingerprint` field at all — `_ssh_patch` reads it through `getattr` with
    # a `None` default, so the value simply cannot be set from this surface (see the docstring).


def _to_whm_response(server: WHMServer) -> WHMServerResponse:
    """Shape one row for the wire off its safe view (module docstring)."""
    safe = server.to_safe_dict()
    ssh = _ssh_view(safe)
    return WHMServerResponse(
        id=safe["id"],
        name=safe["name"],
        base_url=safe["base_url"],
        api_username=safe["api_username"],
        has_api_token=safe["has_api_token"],
        verify_ssl=safe["verify_ssl"],
        is_reseller_credential=safe["is_reseller_credential"],
        created_at=iso_or_none(safe["created_at"]),
        updated_at=iso_or_none(safe["updated_at"]),
        **ssh,
    )


@whm_router.get("", response_model=WHMServersResponse)
async def list_whm_servers(
    admin_user: AdminUserDep,
    servers: WHMServerAdminServiceDep,
) -> WHMServersResponse:
    """Every WHM server."""
    rows = await servers.list_servers()
    return WHMServersResponse(servers=[_to_whm_response(row) for row in rows])


@whm_router.post("", response_model=WHMServerDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_whm_server(
    payload: WHMServerCreateRequest,
    admin_user: AdminUserDep,
    servers: WHMServerAdminServiceDep,
) -> WHMServerDetailResponse:
    """Add a WHM server.

    201, unlike the token mint one file over: there *is* a canonical address for the created
    row — `PATCH`/`DELETE`/`validate` all take its id — so the status that says "created" is
    the honest one. Duplicate name → 409 `whm_server_name_exists`. A reseller row whose `name`
    is not its `api_username` → 409 `whm_reseller_credential_name_mismatch`.
    """
    server = await servers.create(
        WHMServerCreate(
            name=payload.name,
            base_url=payload.base_url,
            api_username=payload.api_username,
            api_token=payload.api_token,
            verify_ssl=payload.verify_ssl,
            is_reseller_credential=payload.is_reseller_credential,
            ssh=_ssh_credentials(payload),
        ),
        actor_email=admin_user.email,
    )
    return WHMServerDetailResponse(server=_to_whm_response(server))


@whm_router.patch("/{server_id}", response_model=WHMServerDetailResponse)
async def update_whm_server(
    server_id: UUID,
    payload: WHMServerUpdateRequest,
    admin_user: AdminUserDep,
    servers: WHMServerAdminServiceDep,
) -> WHMServerDetailResponse:
    """Edit a WHM server.

    Moving `base_url` or `ssh_port` drops the stored host-key pin, because a pin belongs to one
    `(host, port)` pair — `core.servers.admin_repository` does it where the old row is loaded,
    so no caller can forget. The next validate re-captures.

    404 `whm_server_not_found`, 409 `whm_server_name_exists`, 409
    `whm_reseller_credential_name_mismatch` when the *resulting* row would be a reseller
    credential whose `name` is not its `api_username` — which includes a patch that carries
    nothing but the flag.
    """
    server = await servers.update(
        server_id,
        WHMServerUpdate(
            name=payload.name,
            base_url=payload.base_url,
            api_username=payload.api_username,
            api_token=payload.api_token,
            verify_ssl=payload.verify_ssl,
            is_reseller_credential=payload.is_reseller_credential,
            ssh=_ssh_patch(payload),
        ),
        actor_email=admin_user.email,
    )
    return WHMServerDetailResponse(server=_to_whm_response(server))


@whm_router.delete("/{server_id}", response_model=DeleteServerResponse)
async def delete_whm_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    servers: WHMServerAdminServiceDep,
) -> DeleteServerResponse:
    """Remove a WHM server.

    Tool grants are untouched: a grant names a tool, never a host, so deleting a server narrows
    what those tools can reach rather than who may call them. A tool pointed at the deleted name
    afterwards answers `host_not_found`.
    """
    await servers.delete(server_id, actor_email=admin_user.email)
    return DeleteServerResponse(ok=True)


@whm_router.post("/{server_id}/validate", response_model=ValidateServerResponse)
async def validate_whm_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    validation: WHMServerValidationServiceDep,
) -> ValidateServerResponse:
    """Probe a WHM server: the API token, then SSH if credentials are stored.

    Captures and stores the host key on a first validate, and only if the probe that follows
    it passes. A stored pin that no longer matches answers `ok:false` /
    `ssh_host_key_mismatch` — NOA does not re-pin (`core.servers.validation`).
    """
    return _validation_response(await validation.validate(server_id, actor_email=admin_user.email))


# --- Proxmox ---


class ProxmoxServerResponse(BaseModel):
    """One `proxmox_servers` row. No `api_token_secret`, and no SSH block at all."""

    id: str
    name: str
    base_url: str
    api_token_id: str
    has_api_token_secret: bool
    verify_ssl: bool
    created_at: str | None
    updated_at: str | None


class ProxmoxServersResponse(BaseModel):
    """`GET /admin/proxmox/servers`."""

    servers: list[ProxmoxServerResponse]


class ProxmoxServerDetailResponse(BaseModel):
    """`{server: …}`."""

    server: ProxmoxServerResponse


class ProxmoxServerCreateRequest(BaseModel):
    """`POST /admin/proxmox/servers`.

    `verify_ssl` defaults **off**, unlike WHM's, and the column's server default agrees:
    Proxmox ships a self-signed certificate, so defaulting on would make every fresh row fail
    validation for a reason that is not a misconfiguration.
    """

    name: ProxmoxName
    base_url: ProxmoxBaseUrl
    api_token_id: TrimmedRequired = Field(min_length=1, max_length=255)
    api_token_secret: TrimmedRequired = Field(min_length=1)
    verify_ssl: bool = False


class ProxmoxServerUpdateRequest(BaseModel):
    """`PATCH /admin/proxmox/servers/{id}`."""

    name: ProxmoxNameOpt = None
    base_url: ProxmoxBaseUrlOpt = None
    api_token_id: Trimmed = None
    api_token_secret: Trimmed = None
    verify_ssl: bool | None = None


def _to_proxmox_response(server: ProxmoxServer) -> ProxmoxServerResponse:
    safe = server.to_safe_dict()
    return ProxmoxServerResponse(
        id=safe["id"],
        name=safe["name"],
        base_url=safe["base_url"],
        api_token_id=safe["api_token_id"],
        has_api_token_secret=safe["has_api_token_secret"],
        verify_ssl=safe["verify_ssl"],
        created_at=iso_or_none(safe["created_at"]),
        updated_at=iso_or_none(safe["updated_at"]),
    )


@proxmox_router.get("", response_model=ProxmoxServersResponse)
async def list_proxmox_servers(
    admin_user: AdminUserDep,
    servers: ProxmoxServerAdminServiceDep,
) -> ProxmoxServersResponse:
    """Every Proxmox server."""
    rows = await servers.list_servers()
    return ProxmoxServersResponse(servers=[_to_proxmox_response(row) for row in rows])


@proxmox_router.post(
    "", response_model=ProxmoxServerDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_proxmox_server(
    payload: ProxmoxServerCreateRequest,
    admin_user: AdminUserDep,
    servers: ProxmoxServerAdminServiceDep,
) -> ProxmoxServerDetailResponse:
    """Add a Proxmox server. Duplicate name → 409."""
    server = await servers.create(
        ProxmoxServerCreate(
            name=payload.name,
            base_url=payload.base_url,
            api_token_id=payload.api_token_id,
            api_token_secret=payload.api_token_secret,
            verify_ssl=payload.verify_ssl,
        ),
        actor_email=admin_user.email,
    )
    return ProxmoxServerDetailResponse(server=_to_proxmox_response(server))


@proxmox_router.patch("/{server_id}", response_model=ProxmoxServerDetailResponse)
async def update_proxmox_server(
    server_id: UUID,
    payload: ProxmoxServerUpdateRequest,
    admin_user: AdminUserDep,
    servers: ProxmoxServerAdminServiceDep,
) -> ProxmoxServerDetailResponse:
    """Edit a Proxmox server. 404 / 409 as elsewhere."""
    server = await servers.update(
        server_id,
        ProxmoxServerUpdate(
            name=payload.name,
            base_url=payload.base_url,
            api_token_id=payload.api_token_id,
            api_token_secret=payload.api_token_secret,
            verify_ssl=payload.verify_ssl,
        ),
        actor_email=admin_user.email,
    )
    return ProxmoxServerDetailResponse(server=_to_proxmox_response(server))


@proxmox_router.delete("/{server_id}", response_model=DeleteServerResponse)
async def delete_proxmox_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    servers: ProxmoxServerAdminServiceDep,
) -> DeleteServerResponse:
    """Remove a Proxmox server."""
    await servers.delete(server_id, actor_email=admin_user.email)
    return DeleteServerResponse(ok=True)


@proxmox_router.post("/{server_id}/validate", response_model=ValidateServerResponse)
async def validate_proxmox_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    validation: ProxmoxServerValidationServiceDep,
) -> ValidateServerResponse:
    """Probe a Proxmox server's API token.

    One transport and no write: there is no SSH path here and therefore no host key to pin
    (I.ext), which is why the service behind this route reads through a `SELECT`-only
    repository.
    """
    return _validation_response(await validation.validate(server_id, actor_email=admin_user.email))


# --- PMG ---


class PMGServerResponse(BaseModel):
    """One `pmg_servers` row.

    No `base_url` and no `verify_ssl`: PMG is reached over SSH + `pmgsh` only (V58, I.ext), so
    the pinned host key is its whole transport-security story.
    """

    id: str
    name: str
    ssh_host: str
    ssh_username: str | None
    ssh_port: int | None
    ssh_host_key_fingerprint: str | None
    has_ssh_password: bool
    has_ssh_private_key: bool
    created_at: str | None
    updated_at: str | None


class PMGServersResponse(BaseModel):
    """`GET /admin/pmg/servers`."""

    servers: list[PMGServerResponse]


class PMGServerDetailResponse(BaseModel):
    """`{server: …}`."""

    server: PMGServerResponse


class PMGServerCreateRequest(BaseModel):
    """`POST /admin/pmg/servers`.

    `ssh_host_key_fingerprint` is accepted here and not on WHM's create, because PMG's form
    offers the field: an operator who already holds a node's key may pin it before NOA has ever
    connected. Nothing validates its *shape* — a wrong value fails loudly at the next
    connection with `ssh_host_key_mismatch`, which names the remedy, whereas a format rule
    would refuse a legitimate value the day `asyncssh` spells a digest differently.
    """

    name: PMGName
    ssh_host: PMGSshHost
    ssh_username: Trimmed = None
    ssh_port: SshPort = None
    ssh_password: Trimmed = None
    ssh_private_key: Trimmed = None
    ssh_private_key_passphrase: Trimmed = None
    ssh_host_key_fingerprint: Trimmed = None


class PMGServerUpdateRequest(SSHClearFlags):
    """`PATCH /admin/pmg/servers/{id}`. Moving `ssh_host` or `ssh_port` drops the pin."""

    name: PMGNameOpt = None
    ssh_host: PMGSshHostOpt = None
    ssh_username: Trimmed = None
    ssh_port: SshPort = None
    ssh_password: Trimmed = None
    ssh_private_key: Trimmed = None
    ssh_private_key_passphrase: Trimmed = None
    ssh_host_key_fingerprint: Trimmed = None


def _to_pmg_response(server: PMGServer) -> PMGServerResponse:
    safe = server.to_safe_dict()
    ssh = _ssh_view(safe)
    return PMGServerResponse(
        id=safe["id"],
        name=safe["name"],
        ssh_host=safe["ssh_host"],
        created_at=iso_or_none(safe["created_at"]),
        updated_at=iso_or_none(safe["updated_at"]),
        **ssh,
    )


@pmg_router.get("", response_model=PMGServersResponse)
async def list_pmg_servers(
    admin_user: AdminUserDep,
    servers: PMGServerAdminServiceDep,
) -> PMGServersResponse:
    """Every PMG node."""
    rows = await servers.list_servers()
    return PMGServersResponse(servers=[_to_pmg_response(row) for row in rows])


@pmg_router.post("", response_model=PMGServerDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_pmg_server(
    payload: PMGServerCreateRequest,
    admin_user: AdminUserDep,
    servers: PMGServerAdminServiceDep,
) -> PMGServerDetailResponse:
    """Add a PMG node. Duplicate name → 409."""
    server = await servers.create(
        PMGServerCreate(
            name=payload.name,
            ssh_host=payload.ssh_host,
            ssh=_ssh_credentials(payload),
        ),
        actor_email=admin_user.email,
    )
    return PMGServerDetailResponse(server=_to_pmg_response(server))


@pmg_router.patch("/{server_id}", response_model=PMGServerDetailResponse)
async def update_pmg_server(
    server_id: UUID,
    payload: PMGServerUpdateRequest,
    admin_user: AdminUserDep,
    servers: PMGServerAdminServiceDep,
) -> PMGServerDetailResponse:
    """Edit a PMG node. 404 / 409 as elsewhere."""
    server = await servers.update(
        server_id,
        PMGServerUpdate(
            name=payload.name,
            ssh_host=payload.ssh_host,
            ssh=_ssh_patch(payload),
        ),
        actor_email=admin_user.email,
    )
    return PMGServerDetailResponse(server=_to_pmg_response(server))


@pmg_router.delete("/{server_id}", response_model=DeleteServerResponse)
async def delete_pmg_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    servers: PMGServerAdminServiceDep,
) -> DeleteServerResponse:
    """Remove a PMG node."""
    await servers.delete(server_id, actor_email=admin_user.email)
    return DeleteServerResponse(ok=True)


@pmg_router.post("/{server_id}/validate", response_model=ValidateServerResponse)
async def validate_pmg_server(
    server_id: UUID,
    admin_user: AdminUserDep,
    validation: PMGServerValidationServiceDep,
) -> ValidateServerResponse:
    """Probe a PMG node: `pmgsh get /version`, then `/config/mynetworks`.

    Two commands, because a node that authenticates but cannot read `mynetworks` would validate
    green and fail on the first whitelist call. Same trust-on-first-use rule as WHM's, from the
    same function.
    """
    return _validation_response(await validation.validate(server_id, actor_email=admin_user.email))


__all__ = [
    "WHM_API_USERNAME_PATTERN",
    "DeleteServerResponse",
    "PMGServerCreateRequest",
    "PMGServerDetailResponse",
    "PMGServerResponse",
    "PMGServerUpdateRequest",
    "PMGServersResponse",
    "ProxmoxServerCreateRequest",
    "ProxmoxServerDetailResponse",
    "ProxmoxServerResponse",
    "ProxmoxServerUpdateRequest",
    "ProxmoxServersResponse",
    "SSHClearFlags",
    "ValidateServerResponse",
    "WHMServerCreateRequest",
    "WHMServerDetailResponse",
    "WHMServerResponse",
    "WHMServerUpdateRequest",
    "WHMServersResponse",
    "pmg_router",
    "proxmox_router",
    "whm_router",
]
