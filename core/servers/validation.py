"""`POST …/servers/{id}/validate`: does this server answer, and what is its host key (T54).

Three services, one per system, over **one** trust-on-first-use rule. The rule is the reason
this module exists separately from `core.servers.admin_service`, and it has two halves that
are easy to state and were easy to get wrong:

**Pin once. A mismatch refuses.**

- No pin stored → connect unpinned, capture the key the host presents
  (`core.remote_exec.ssh.ssh_get_host_fingerprint`, the only unpinned path in the repo), probe
  with the captured value, and store it **only if that probe passed**.
- Pin stored → probe with it. `ssh_exec` compares mid-handshake, before user auth, so a host
  answering with a different key gets `ssh_host_key_mismatch` and never receives the
  credential (V82). NOA does **not** re-pin it.

`noa-old`'s WHM service did re-pin, unconditionally, on every validate — it captured whatever
key answered and overwrote the stored one before probing. That makes the pin worth nothing:
any admin pressing Validate silently re-trusts whatever is on the other end of the address,
which is the exact event a pin exists to surface. Its PMG service already had the tighter
shape (`if not pinned_fingerprint:` plus a rollback), so the port takes PMG's and V69 is why:
provenance is not evidence a security control works, and a ported control lands with a test
against the real mechanism (`test_server_host_key_validation.py`, a live `asyncssh` server).

**Refresh is deliberate, and it is two operator actions**: `PATCH` with
`clear_ssh_host_key_fingerprint` (or edit the host, which invalidates the pin —
`core.servers.admin_repository`), then Validate. A key rotation therefore costs one extra
click and a MITM costs an operator a decision, which is the trade this task chose.

**Store after the probe, never before.** The pinned probe reads the fingerprint out of its
`SSHConnectionConfig`, not out of the database, so nothing needs to be written for it to run.
That removes `noa-old`'s rollback branch entirely: a capture whose probe fails leaves no pin
because none was ever stored. The rollback path was the one that could strand a bad pin if the
process died between the write and the failure.

**No database connection is held across a hop.** Each service reads its row in one short
session, closes it, does the network work, and opens a second session only when there is a
fingerprint to store. Holding a pooled connection while waiting on somebody else's SSH daemon
is how a slow server becomes a database outage — T21's rule, and `core.approvals.expiry`
already draws its own sessions for the same reason.

**Unreachable is an answer, not an error.** Every one of these returns
`ServerValidationResult(ok=False, error_code=…)` with a 200 behind it. The operator asked "does
this server answer?"; "no, `ssh_timeout`" *is* that question's answer, and the panel reads
`result.ok` to draw a status chip (`apps/admin-web/.../whm-status.ts`). A 502 would throw at
the transport and render as an unhandled error. `SecretCryptoError` is the deliberate
exception: a row whose ciphertext will not decrypt is NOA's own fault, not the remote's, so it
propagates and takes its 500 (`noa_api.api.errors`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.admin_events import (
    EVENT_PMG_SERVER_VALIDATED,
    EVENT_PROXMOX_SERVER_VALIDATED,
    EVENT_WHM_SERVER_VALIDATED,
    AdminAuditEvent,
    AdminAuditSink,
)
from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.db.session import SessionFactory
from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.pmg.pmgsh_cli import run_pmg_mynetworks_probe, run_pmg_version_probe
from core.integrations.pmg.ssh import resolve_pmg_ssh_config
from core.integrations.proxmox.client import ProxmoxClientFactory, build_proxmox_client
from core.integrations.whm.client import WHMClient
from core.integrations.whm.errors import WHMFirewallCLIError
from core.integrations.whm.ssh import (
    WHMClientFactory,
    build_whm_client,
    has_ssh_credentials,
    resolve_whm_ssh_config,
)
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.ssh import ssh_exec, ssh_get_host_fingerprint
from core.remote_exec.types import SSHConnectionConfig
from core.secrets.crypto import SecretCipher
from core.servers.errors import (
    PMGServerNotFoundError,
    ProxmoxServerNotFoundError,
    WHMServerNotFoundError,
)

# The failures that mean "the remote did not answer usably", as opposed to "NOA is broken".
# All three carry `error_code` and `message` (they are `NoaError`s), which is what lets one
# `except` shape a result. `SecretCryptoError` is deliberately absent — see the docstring.
REMOTE_FAILURES = (SSHExecutionError, PMGSHCLIError, WHMFirewallCLIError)

# The cheapest thing that proves auth *and* the host key: `true` exits 0 everywhere, needs no
# sudo, and reads nothing. `noa-old` used it too.
SSH_PROBE_COMMAND = "true"

MESSAGE_OK = "ok"


@dataclass(frozen=True)
class ServerValidationResult:
    """One validate answer. Three fields, matching the shape the panel already reads.

    `error_code` is `None` on success rather than `""`, so a client branching on it cannot
    read the empty string as a code it does not recognise.
    """

    ok: bool
    message: str
    error_code: str | None = None


RowT_co = TypeVar("RowT_co", covariant=True)


class HostKeyPinRepository(Protocol[RowT_co]):
    """Read one server row; write only its host-key fingerprint.

    The narrowest thing a reachability probe can be handed. `SQLWHMHostKeyPinRepository` and
    `SQLPMGHostKeyPinRepository` are the implementations, and neither can change a credential
    or delete a row — the split `noa_api.api.deps` makes for the approval card, spelled against
    inventory (V82: the pin is the thing being protected here).
    """

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...

    async def set_host_key_fingerprint(self, server_id: UUID, fingerprint: str | None) -> bool: ...

    async def commit(self) -> None: ...


class ProxmoxRowReader(Protocol):
    """`SELECT`-only, because a Proxmox validate writes nothing (no SSH path, I.ext)."""

    async def get_by_id(self, server_id: UUID) -> ProxmoxServer | None: ...


# The two network seams, as types rather than `Callable[..., Any]`, so a double must have the
# production signature. Both default to the real thing at every construction site.
HostKeyCapture = Callable[[SSHConnectionConfig], Awaitable[str]]
SSHProbe = Callable[[SSHConnectionConfig], Awaitable[None]]


async def probe_ssh_reachable(config: SSHConnectionConfig) -> None:
    """WHM's SSH probe: authenticate, run `true`, done."""
    await ssh_exec(config, command=SSH_PROBE_COMMAND)


async def probe_pmg_reachable(config: SSHConnectionConfig) -> None:
    """PMG's SSH probe: `pmgsh get /version`, then read `/config/mynetworks` (V58).

    Two commands rather than one, and `noa-old` had both: `/version` proves the credential and
    `sudo -n` where it is needed (V55), while `mynetworks` proves the endpoint every PMG tool
    actually touches is readable. A node that authenticates but refuses `mynetworks` would
    otherwise validate green and fail on the first whitelist call.
    """
    await run_pmg_version_probe(config)
    await run_pmg_mynetworks_probe(config)


async def probe_with_trust_on_first_use(
    *,
    config: SSHConnectionConfig,
    capture: HostKeyCapture,
    probe: SSHProbe,
) -> str | None:
    """Probe `config`, capturing a host key first when none is pinned.

    Returns the fingerprint the **caller must store**, or `None` when the row already carries
    a usable pin. Raises whatever the probe raises — the caller shapes the result, because the
    exception trees differ per system.

    The shared half of the rule, so WHM and PMG cannot drift apart on it (V66). This is
    deliberately the only place in NOA that decides whether a pin gets written.
    """
    if config.host_key_fingerprint:
        # Pinned: `ssh_exec` compares the presented key mid-handshake and raises
        # `ssh_host_key_mismatch` before user auth (V82). Nothing is stored either way — a
        # mismatch is reported, never re-trusted.
        await probe(config)
        return None

    captured = await capture(config)
    # Probe with the captured value, not with the stored one — there is no stored one. If this
    # raises, the caller stores nothing, so a bad capture leaves no trace by construction.
    await probe(replace(config, host_key_fingerprint=captured))
    return captured


def _remote_failure(error: SSHExecutionError | PMGSHCLIError | WHMFirewallCLIError):  # type: ignore[no-untyped-def]
    """Shape a remote refusal as an answer (V8: `message` is already credential-free)."""
    return ServerValidationResult(ok=False, message=error.message, error_code=error.error_code)


def _client_answer(payload: dict[str, object], *, default_message: str) -> ServerValidationResult:
    """Shape a `WHMClient`/`ProxmoxClient` dict answer.

    Those clients normalise every outcome into `{ok, error_code, message}` rather than raising
    (that is what they exist for — a WHM `200` with `result: 0` is a failure), so this is a
    translation, not an error path.
    """
    if payload.get("ok") is True:
        return ServerValidationResult(ok=True, message=str(payload.get("message") or MESSAGE_OK))
    return ServerValidationResult(
        ok=False,
        message=str(payload.get("message") or default_message),
        error_code=str(payload.get("error_code") or "unknown"),
    )


def _validation_metadata(
    server: WHMServer | ProxmoxServer | PMGServer,
    *,
    result: ServerValidationResult,
    host_key_was_pinned: bool,
    fingerprint_captured: bool,
) -> dict[str, Any]:
    """What a validate event records (V14, V8).

    `host_key_was_pinned` and `fingerprint_captured` are both here on purpose: together they
    answer "who first trusted this host key, and when", which is the question a
    `ssh_host_key_mismatch` six months from now turns into. Never the fingerprint's
    surrounding credential, and never the key material.
    """
    return {
        "server_id": str(server.id),
        "server_name": server.name,
        "ok": result.ok,
        "error_code": result.error_code,
        "host_key_was_pinned": host_key_was_pinned,
        "fingerprint_captured": fingerprint_captured,
    }


class WHMServerValidationService:
    """Validate one WHM server: the API token, then SSH if the row carries credentials (T54).

    Both transports, because a WHM row has both (I.ext) and either can be the broken one. The
    API check runs first: it is cheaper, and an operator whose token is wrong should not have
    to read an SSH error to find that out.

    A row with **no** SSH credentials validates green on the API alone, and that is right
    rather than lenient: SSH is what the firewall tools need (T24-T26), and a WHM server used
    only for account reads is a legitimate configuration. `resolve_whm_ssh_config` answers
    `ssh_not_configured` if a firewall tool is ever pointed at it, which names the remedy.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        repository_factory: Callable[[AsyncSession], HostKeyPinRepository[WHMServer]],
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
        client_factory: WHMClientFactory = build_whm_client,
        capture_host_key: HostKeyCapture = ssh_get_host_fingerprint,
        probe: SSHProbe = probe_ssh_reachable,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._cipher = cipher
        self._audit = audit_sink
        self._client_factory = client_factory
        self._capture_host_key = capture_host_key
        self._probe = probe

    async def validate(
        self, server_id: UUID, *, actor_email: str | None = None
    ) -> ServerValidationResult:
        """Probe the server. Absent row → `WHMServerNotFoundError`; anything else → a result."""
        async with self._session_factory() as session:
            server = await self._repository_factory(session).get_by_id(server_id)
            if server is None:
                raise WHMServerNotFoundError(f"no `whm_servers` row `{server_id}`")

            # Everything that needs the row happens here, before the session closes: an ORM
            # instance cannot be read after that, and `SSHConnectionConfig` is frozen and holds
            # the decrypted credentials it needs (T21's rule, `core.remote_exec.types`).
            was_pinned = bool((server.ssh_host_key_fingerprint or "").strip())
            client = self._client_factory(server, cipher=self._cipher)
            ssh_wanted = has_ssh_credentials(server)
            ssh_setup = self._resolve_ssh(server) if ssh_wanted else None
            audit_row: WHMServer = server

        result, captured = await self._run_probes(client, ssh_wanted=ssh_wanted, setup=ssh_setup)
        if captured is not None:
            await self._store_fingerprint(server_id, captured)

        await self._audit.record(
            AdminAuditEvent(
                event_type=EVENT_WHM_SERVER_VALIDATED,
                actor_email=actor_email,
                target=audit_row.name,
                metadata=_validation_metadata(
                    audit_row,
                    result=result,
                    host_key_was_pinned=was_pinned,
                    fingerprint_captured=captured is not None,
                ),
            )
        )
        return result

    def _resolve_ssh(self, server: WHMServer) -> SSHConnectionConfig | ServerValidationResult:
        """Row → config, or the answer for a row SSH cannot be built from.

        `require_host_key_fingerprint=False` is what makes a first validate possible at all:
        the pin is what this call is here to capture. Every *tool* passes `True`.
        """
        try:
            return resolve_whm_ssh_config(
                server, cipher=self._cipher, require_host_key_fingerprint=False
            )
        except SSHExecutionError as error:
            return _remote_failure(error)

    async def _run_probes(
        self,
        client: WHMClient,
        *,
        ssh_wanted: bool,
        setup: SSHConnectionConfig | ServerValidationResult | None,
    ) -> tuple[ServerValidationResult, str | None]:
        api_answer = _client_answer(await client.applist(), default_message="WHM validation failed")
        if not api_answer.ok or not ssh_wanted:
            return api_answer, None
        if isinstance(setup, ServerValidationResult):
            return setup, None
        if setup is None:  # pragma: no cover — `ssh_wanted` implies a resolved config
            return api_answer, None

        try:
            captured = await probe_with_trust_on_first_use(
                config=setup, capture=self._capture_host_key, probe=self._probe
            )
        except REMOTE_FAILURES as error:
            return _remote_failure(error), None
        return ServerValidationResult(ok=True, message=MESSAGE_OK), captured

    async def _store_fingerprint(self, server_id: UUID, fingerprint: str) -> None:
        """The one column this service may write, in its own committed transaction (V100)."""
        async with self._session_factory() as session:
            repository = self._repository_factory(session)
            await repository.set_host_key_fingerprint(server_id, fingerprint)
            await repository.commit()


class ProxmoxServerValidationService:
    """Validate one Proxmox server: the API token, and nothing else (T54).

    One transport, one probe, no write. Proxmox is reached over HTTP only (I.ext), so there is
    no host key to pin and this service's repository is `SELECT`-only — the absence of a write
    path is the guarantee, not a rule it follows.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        repository_factory: Callable[[AsyncSession], ProxmoxRowReader],
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
        client_factory: ProxmoxClientFactory = build_proxmox_client,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._cipher = cipher
        self._audit = audit_sink
        self._client_factory = client_factory

    async def validate(
        self, server_id: UUID, *, actor_email: str | None = None
    ) -> ServerValidationResult:
        async with self._session_factory() as session:
            server = await self._repository_factory(session).get_by_id(server_id)
            if server is None:
                raise ProxmoxServerNotFoundError(f"no `proxmox_servers` row `{server_id}`")
            client = self._client_factory(server, cipher=self._cipher)
            audit_row: ProxmoxServer = server

        # `async with`: `ProxmoxClient` holds an `httpx.AsyncClient`, and T17 deviation (c)
        # records that `noa-old` leaked one per call.
        async with client:
            result = _client_answer(
                await client.get_version(), default_message="Proxmox validation failed"
            )

        await self._audit.record(
            AdminAuditEvent(
                event_type=EVENT_PROXMOX_SERVER_VALIDATED,
                actor_email=actor_email,
                target=audit_row.name,
                metadata=_validation_metadata(
                    audit_row,
                    result=result,
                    host_key_was_pinned=False,
                    fingerprint_captured=False,
                ),
            )
        )
        return result


class PMGServerValidationService:
    """Validate one PMG node over SSH + `pmgsh` (T54, V58).

    One transport, and it is the SSH one, so the trust-on-first-use rule is the whole of this
    service. A row with no credentials answers `ssh_not_configured` from
    `resolve_pmg_ssh_config` — a named refusal an admin can act on, rather than a socket error.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        repository_factory: Callable[[AsyncSession], HostKeyPinRepository[PMGServer]],
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
        capture_host_key: HostKeyCapture = ssh_get_host_fingerprint,
        probe: SSHProbe = probe_pmg_reachable,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._cipher = cipher
        self._audit = audit_sink
        self._capture_host_key = capture_host_key
        self._probe = probe

    async def validate(
        self, server_id: UUID, *, actor_email: str | None = None
    ) -> ServerValidationResult:
        async with self._session_factory() as session:
            server = await self._repository_factory(session).get_by_id(server_id)
            if server is None:
                raise PMGServerNotFoundError(f"no `pmg_servers` row `{server_id}`")
            was_pinned = bool((server.ssh_host_key_fingerprint or "").strip())
            setup = self._resolve_ssh(server)
            audit_row: PMGServer = server

        captured: str | None = None
        if isinstance(setup, ServerValidationResult):
            result = setup
        else:
            try:
                captured = await probe_with_trust_on_first_use(
                    config=setup, capture=self._capture_host_key, probe=self._probe
                )
                result = ServerValidationResult(ok=True, message=MESSAGE_OK)
            except REMOTE_FAILURES as error:
                result, captured = _remote_failure(error), None

        if captured is not None:
            await self._store_fingerprint(server_id, captured)

        await self._audit.record(
            AdminAuditEvent(
                event_type=EVENT_PMG_SERVER_VALIDATED,
                actor_email=actor_email,
                target=audit_row.name,
                metadata=_validation_metadata(
                    audit_row,
                    result=result,
                    host_key_was_pinned=was_pinned,
                    fingerprint_captured=captured is not None,
                ),
            )
        )
        return result

    def _resolve_ssh(self, server: PMGServer) -> SSHConnectionConfig | ServerValidationResult:
        try:
            return resolve_pmg_ssh_config(
                server, cipher=self._cipher, require_host_key_fingerprint=False
            )
        except SSHExecutionError as error:
            return _remote_failure(error)

    async def _store_fingerprint(self, server_id: UUID, fingerprint: str) -> None:
        async with self._session_factory() as session:
            repository = self._repository_factory(session)
            await repository.set_host_key_fingerprint(server_id, fingerprint)
            await repository.commit()


__all__ = [
    "MESSAGE_OK",
    "REMOTE_FAILURES",
    "SSH_PROBE_COMMAND",
    "HostKeyCapture",
    "HostKeyPinRepository",
    "PMGServerValidationService",
    "ProxmoxRowReader",
    "ProxmoxServerValidationService",
    "SSHProbe",
    "ServerValidationResult",
    "WHMServerValidationService",
    "probe_pmg_reachable",
    "probe_ssh_reachable",
    "probe_with_trust_on_first_use",
]
