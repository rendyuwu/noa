"""Doubles for the server-inventory read path.

Both systems' inventory lives here, because inventory is one subject: a row of the mapped
class, an in-memory repository that answers the way the SQL one does, and the `McpToolContext`
that holds them. The system-specific doubles stay in their own modules — `support/whm.py` and
`support/pmg.py` own the `Protocol`-shaped rows their integration layers take, and
`support/whm_firewall.py` and `support/pmg.py` own the tool-path helpers that wrap what is here.

Two decisions here carry the weight of the tests that use this module.

**Rows are real `WHMServer` instances, not stand-ins.** The assertion is that nothing a
tool emits carries credential material, and the thing that decides that is
`WHMServer.to_safe_dict`. A hand-written fake with a hand-written `to_safe_dict` would test
the fake. So `whm_server()` constructs the mapped class and fills the three defaulted columns
(`id`, `created_at`, `updated_at`) by hand — an unsaved instance has them as `None`, and the
resolver's UUID branch needs a real id. `None` until a flush whichever kind of default it is:
`id` is Postgres's (`gen_random_uuid()`), while the two timestamps are stamped by the
application clock and keep a server default only as the non-ORM fallback (`core/db/columns.py`).
A default of either kind runs at flush and not at construction.

Every row is built **with** an API token and SSH credentials, even where the test does not
care. A row with no secrets cannot fail a "no secrets leaked" assertion, which would make
that test pass for the wrong reason.

**`SQLWHMServerRepository` is not doubled away entirely** — `test_whm_server_repository.py`
runs it against a scratch Postgres. This double covers policy (resolution order, ties,
the tool's shape); the SQL has its own coverage. Same split as `support.rbac`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.integrations.proxmox.client import (
    ProxmoxClient,
    ProxmoxServerSecretLike,
    build_proxmox_client,
)
from core.integrations.whm.client import WHMClient
from core.integrations.whm.ssh import WHMServerSecretLike, build_whm_client
from core.secrets.crypto import SecretCipher
from noa_api.mcp_tools.context import McpToolContext
from support.action_expiry import FakeActionRequestExpiryRepository
from support.action_requests import FakeActionRequestRepository
from support.action_results import FakeActionResultRepository
from support.mcp_identity import StubSession
from support.rbac import FakeAuthorizationRepository, RecordingAuditSink
from support.result_tables import FakeToolResultTableWriter
from support.secrets import build_cipher
from support.tool_runs import FakeToolRunRepository

# Fixed, so `to_safe_dict` output is comparable across runs.
CREATED_AT = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

# Ciphertext-shaped, because that is what the column holds. The point of these
# literals is that they must not appear in any tool result.
API_TOKEN = "enc:v1:fernet:whm-api-token"
SSH_PASSWORD = "enc:v1:fernet:whm-ssh-password"
SSH_PRIVATE_KEY = "enc:v1:fernet:whm-ssh-private-key"
# Proxmox's own, distinct from WHM's so a leak assertion says which table it came off.
PROXMOX_API_TOKEN_SECRET = "enc:v1:fernet:proxmox-api-token-secret"

# Every credential literal above, for a single "none of these leaked" assertion.
SECRETS = (API_TOKEN, SSH_PASSWORD, SSH_PRIVATE_KEY, PROXMOX_API_TOKEN_SECRET)

FINGERPRINT = "SHA256:l3Rz6cS0nOtASecret+ItIsAPublicKeyDigest"

# The pending-request TTL these fixtures hand the tool path. Not `Settings`'
# 3600: a deadline asserted against the production default cannot separate a gate that read
# the configured value from one that hardcoded it.
PENDING_TTL_SECONDS = 900

# The embed origin these fixtures hand the tool path. Not `Settings`'
# `http://localhost:3001`, and for the same reason as the TTL above: an approval URL asserted
# against the production default cannot separate a gate that read the configured value from
# one that hardcoded a laptop address.
EMBED_BASE_URL = "https://embed.noa.test"

# The parked-table lifetime and row cap these fixtures hand the tool path.
# Neither is `Settings`' value (86400 and 5000), for the reason the two above are not: a
# deadline or a cap asserted against the production default cannot separate a tool that read
# the configured value from one that hardcoded it. The cap is small enough that a truncation
# is reachable in a test without building five thousand rows.
RESULT_TABLE_TTL_SECONDS = 1800
RESULT_TABLE_MAX_ROWS = 25

# The generated-password length these fixtures hand the tool path. Not
# `Settings`' 24, for the reason the four values above are not their production defaults: a
# length asserted against the configured number cannot separate a generator that read the setting
# from one that fell back to `_DEFAULT_PASSWORD_LENGTH`, which is also 24.
SECRET_PASSWORD_LENGTH = 31

# What the delivery double answers with. Fragment-shaped like a real yopass URL so a test
# asserting the link reaches the operator is asserting the thing that is actually handed over.
YOPASS_URL = "https://yopass.noa.test/#/s/2f1c0b4a-0000-4000-8000-00000000beef/PassPhrase123"

# How long a delivered link lives, and whether opening it spends it, as these fixtures hand them
# to the tool path. Two days rather than `Settings`' seven, for the reason none of the values above
# is its production default: a sentence asserted against the configured number cannot separate a
# runner that read it from one that spelled today's deployment into a string — which is the bug
# this pair exists to make catchable, since the sentence it feeds used to claim the link opens
# once against a deployment whose links are reusable for a week.
SECRET_DELIVERY_ONE_TIME = False
SECRET_DELIVERY_EXPIRATION_SECONDS = 172800


def whm_server(
    name: str,
    *,
    base_url: str | None = None,
    server_id: UUID | None = None,
    ssh_username: str | None = "noa",
    api_token: str = API_TOKEN,
    ssh_password: str | None = SSH_PASSWORD,
    ssh_private_key: str | None = SSH_PRIVATE_KEY,
    ssh_host_key_fingerprint: str | None = FINGERPRINT,
    api_username: str = "root",
    is_reseller_credential: bool = False,
) -> WHMServer:
    """One `whm_servers` row, credentials included, ready to read.

    `api_token` and `ssh_password` are overridable for the same reason: the defaults are
    ciphertext-*shaped* rather than real ciphertext, so they exist to be asserted absent from a
    result and they do not decrypt. A test that actually reaches WHM over HTTP or over
    SSH passes `cipher.encrypt_text(...)` so the real decrypt site runs — see
    `build_tool_context`.

    `ssh_private_key` and `ssh_host_key_fingerprint` are overridable to `None` so a test can
    build the two rows `resolve_whm_ssh_config` refuses: no credentials at all, and no pin.

    `api_username` and `is_reseller_credential` are overridable together, because the pair is
    what the name-equals-api_username rule constrains: a reseller row is named after its API
    username. The flag is set
    explicitly rather than left to the column's server default, which never runs on an instance
    that has not reached Postgres — a `None` here would read as `false` at every call site while
    being neither.
    """
    server = WHMServer(
        name=name,
        base_url=base_url or f"https://{name}.example.net:2087",
        api_username=api_username,
        api_token=api_token,
        verify_ssl=True,
        is_reseller_credential=is_reseller_credential,
        ssh_username=ssh_username,
        ssh_port=22,
        ssh_password=ssh_password,
        ssh_private_key=ssh_private_key,
        ssh_host_key_fingerprint=ssh_host_key_fingerprint,
    )
    # Server-side defaults (`gen_random_uuid()`, `now()`) are not applied to an instance
    # that never reached Postgres.
    server.id = server_id or uuid4()
    server.created_at = CREATED_AT
    server.updated_at = CREATED_AT
    return server


def pmg_server(
    name: str,
    *,
    ssh_host: str | None = None,
    server_id: UUID | None = None,
    ssh_username: str | None = None,
    ssh_password: str | None = SSH_PASSWORD,
    ssh_private_key: str | None = SSH_PRIVATE_KEY,
    ssh_host_key_fingerprint: str | None = FINGERPRINT,
) -> PMGServer:
    """One `pmg_servers` row, credentials included, ready to read.

    The same construction as `whm_server` and for the same reasons — a real mapped instance
    with the three defaulted columns filled by hand — over a narrower row: PMG is
    SSH-only, so there is no `base_url`, no API token and no `verify_ssl`.

    The credential defaults are shared with the WHM row on purpose. They are ciphertext-*shaped*
    rather than real ciphertext, they exist to be asserted absent from a result, and one set of
    literals means one `SECRETS` tuple covers both systems. A test that actually connects passes
    `cipher.encrypt_text(...)` — see `support.pmg.whitelist_server`.

    `ssh_private_key` and `ssh_host_key_fingerprint` are overridable to `None` so a test can
    build the two rows `resolve_pmg_ssh_config` refuses: no credentials at all, and no pin.
    """
    server = PMGServer(
        name=name,
        ssh_host=ssh_host or f"{name}.example.net",
        ssh_username=ssh_username,
        ssh_port=22,
        ssh_password=ssh_password,
        ssh_private_key=ssh_private_key,
        ssh_host_key_fingerprint=ssh_host_key_fingerprint,
    )
    server.id = server_id or uuid4()
    server.created_at = CREATED_AT
    server.updated_at = CREATED_AT
    return server


def proxmox_server(
    name: str,
    *,
    base_url: str | None = None,
    server_id: UUID | None = None,
    api_token_id: str = "root@pam!noa",  # noqa: S107 — a token *id*, never the secret
    api_token_secret: str = PROXMOX_API_TOKEN_SECRET,
    verify_ssl: bool = True,
) -> ProxmoxServer:
    """One `proxmox_servers` row, credentials included, ready to read.

    The same construction as `whm_server` and for the same reasons — a real mapped instance with
    the three defaulted columns filled by hand — over the narrowest row of the three:
    Proxmox is HTTP-only (I.ext), so there are no SSH columns and no host-key pin.

    `api_token_secret` defaults to a ciphertext-*shaped* literal that does not decrypt. It exists
    to be asserted absent from a tool result; a test that actually reaches Proxmox over HTTP
    passes `cipher.encrypt_text(...)` so the real decrypt site runs — see `build_tool_context`.

    `verify_ssl` defaults to `True` here and to `False` on the column (Proxmox ships a self-signed
    cert), deliberately: a fixture that matched the server default could not tell a client that
    read the row from one that fell back to it.
    """
    server = ProxmoxServer(
        name=name,
        base_url=base_url or f"https://{name}.example.net:8006",
        api_token_id=api_token_id,
        api_token_secret=api_token_secret,
        verify_ssl=verify_ssl,
    )
    # Server-side defaults (`gen_random_uuid()`, `now()`) are not applied to an instance that
    # never reached Postgres.
    server.id = server_id or uuid4()
    server.created_at = CREATED_AT
    server.updated_at = CREATED_AT
    return server


class FakeWHMServerRepository:
    """In-memory `WHMServerReadRepository`.

    `list_servers` sorts by name, mirroring the `ORDER BY` in the SQL: the resolver's tie
    handling and the tool's output order are both asserted, and a double that returned
    insertion order would let a test pass against a repository that does not.
    """

    def __init__(self, servers: Iterable[WHMServer] = ()) -> None:
        self.servers: list[WHMServer] = list(servers)
        # Read counter: permission resolution cares that inventory is read per call, never memoized.
        self.reads = 0

    async def list_servers(self) -> Sequence[WHMServer]:
        self.reads += 1
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> WHMServer | None:
        self.reads += 1
        return next((server for server in self.servers if server.id == server_id), None)


class FakePMGServerRepository:
    """In-memory `PMGServerReadRepository`.

    Sorted by name for the reason its WHM twin is: the resolver's tie handling is asserted
    against this order, and a double that returned insertion order would let a test pass
    against a repository that does not.

    Kept as a separate class rather than one generic double, matching the two production
    repositories: `SQLPMGServerRepository` selects a different table, and a shared double would
    stop being evidence that the tool asked PMG's inventory rather than WHM's.
    """

    def __init__(self, servers: Iterable[PMGServer] = ()) -> None:
        self.servers: list[PMGServer] = list(servers)
        self.reads = 0

    async def list_servers(self) -> Sequence[PMGServer]:
        self.reads += 1
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> PMGServer | None:
        self.reads += 1
        return next((server for server in self.servers if server.id == server_id), None)


class FakeProxmoxServerRepository:
    """In-memory `ProxmoxServerReadRepository`.

    Sorted by name for the reason its two siblings are: the resolver's tie handling is asserted
    against this order, and a double that returned insertion order would let a test pass against
    a repository that does not.

    Kept as a separate class rather than one generic double, matching the three production
    repositories: `SQLProxmoxServerRepository` selects a different table, and a shared double
    would stop being evidence that the tool asked Proxmox's inventory rather than WHM's.
    """

    def __init__(self, servers: Iterable[ProxmoxServer] = ()) -> None:
        self.servers: list[ProxmoxServer] = list(servers)
        self.reads = 0

    async def list_servers(self) -> Sequence[ProxmoxServer]:
        self.reads += 1
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> ProxmoxServer | None:
        self.reads += 1
        return next((server for server in self.servers if server.id == server_id), None)


class RecordingSecretDelivery:
    """A `SecretDelivery` that records what it was handed and answers a fixed URL.

    **A double of the delivery hop, and the one place these tests do not keep production code in
    the path.** `test_yopass_store.py` covers `_yopass_store` itself — the PGPy encrypt, the
    passphrase staying out of the request body, the fragment assembly — against an `httpx`
    transport, so what is left for a runner test is *ordering*: was the secret stored before the
    VM was touched, and was it stored at all when the change failed early.

    It records the password so a test can assert the plaintext is absent from every payload,
    receipt and summary the change produced — an assertion that needs the value and must
    not get it from the production code under test.
    """

    def __init__(self, *, url: str = YOPASS_URL, error: Exception | None = None) -> None:
        self.url = url
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def __call__(self, *, username: str, password: str) -> str:
        self.calls.append({"username": username, "password": password})
        if self.error is not None:
            raise self.error
        return self.url

    @property
    def delivered_password(self) -> str | None:
        """The password handed to delivery, or `None` if it was never reached."""
        return self.calls[-1]["password"] if self.calls else None


@dataclass
class ToolFixture:
    """An `McpToolContext` over doubles, plus the doubles behind it."""

    context: McpToolContext
    servers: FakeWHMServerRepository
    pmg_servers: FakePMGServerRepository
    proxmox_servers: FakeProxmoxServerRepository
    secret_delivery: RecordingSecretDelivery
    authorization: FakeAuthorizationRepository
    audit: RecordingAuditSink
    tool_runs: FakeToolRunRepository
    action_requests: FakeActionRequestRepository
    action_results: FakeActionResultRepository
    action_expiry: FakeActionRequestExpiryRepository
    result_tables: FakeToolResultTableWriter
    cipher: SecretCipher


def build_tool_context(
    *,
    servers: Iterable[WHMServer] = (),
    pmg_servers: Iterable[PMGServer] = (),
    proxmox_servers: Iterable[ProxmoxServer] = (),
    authorization: FakeAuthorizationRepository | None = None,
    tool_runs: FakeToolRunRepository | None = None,
    action_requests: FakeActionRequestRepository | None = None,
    action_results: FakeActionResultRepository | None = None,
    action_expiry: FakeActionRequestExpiryRepository | None = None,
    result_tables: FakeToolResultTableWriter | None = None,
    pending_ttl_seconds: int = PENDING_TTL_SECONDS,
    embed_base_url: str = EMBED_BASE_URL,
    result_table_ttl_seconds: int = RESULT_TABLE_TTL_SECONDS,
    result_table_max_rows: int = RESULT_TABLE_MAX_ROWS,
    secret_password_length: int = SECRET_PASSWORD_LENGTH,
    secret_delivery_one_time: bool = SECRET_DELIVERY_ONE_TIME,
    secret_delivery_expiration_seconds: int = SECRET_DELIVERY_EXPIRATION_SECONDS,
    secret_delivery: RecordingSecretDelivery | None = None,
    cipher: SecretCipher | None = None,
    whm_transport: httpx.AsyncBaseTransport | None = None,
    proxmox_transport: httpx.AsyncBaseTransport | None = None,
) -> ToolFixture:
    """A production `McpToolContext` whose repositories are in-memory.

    The context, the RBAC engine, the audit middleware and the tool functions are all
    production code; the session is a stub every repository ignores, which is what lets
    these tests run without Postgres.

    The `tool_runs` writer is a double here by default and in *every* test that mounts the
    app, not only the audit ones. That is deliberate: the middleware refuses a call it
    cannot record, so without a working writer in the shared fixture the RBAC tests would
    start failing for an unrelated reason — and a fixture that quietly disabled the audit
    path would let the whole suite pass with the tool-run-trail rule unheld.

    The `action_requests` writer is a double for the same reason and on the same terms: it is
    the only thing standing between a CHANGE gate call and Postgres, and the
    live SQL has its own coverage in `test_mcp_change_gate.py`.

    `action_results` and `action_expiry` are the action-result tool's pair, and they are two
    doubles rather than
    one because production has two classes: a reader that holds only `SELECT`s and a writer
    whose one reachable status is `EXPIRED`. Both are handed the same journal, so a test can
    assert the *order* the read path took them in — which is what pins "a request that is not
    the caller's is never expired by this read". The SQL behind the reader has its own
    coverage in `test_action_results_live.py`, where a NULL requester is a claim about the
    statement rather than about a Python comparison.

    `pending_ttl_seconds` is deliberately *not* the production default. `Settings` says 3600
    (`core.config`), so a test asserting a deadline against that number could not tell a gate
    that read the setting from one that hardcoded it; this value is a number nothing else in
    the tree holds. `embed_base_url` is the same trick one field over.

    `cipher` and `whm_transport` are the account search's two seams, and neither replaces
    production code.
    The cipher is a real `SecretCipher` on a throwaway key, so a tool that decrypts an API
    token runs the real decrypt (pass the same instance a row's `api_token` was encrypted
    with). `whm_transport` reaches `build_whm_client` — the production factory — so the real
    `WHMClient` and the real cipher stay in the path and only the socket is doubled.

    `proxmox_transport` is the same seam one system over, reaching `build_proxmox_client`.
    `secret_delivery` is the one exception to "only the socket is doubled", and
    `RecordingSecretDelivery` says why: `_yopass_store` has its own coverage against a transport,
    and what a CHANGE runner test needs from delivery is the *order* it happened in and the value
    it was handed.
    """
    server_repository = FakeWHMServerRepository(servers)
    pmg_repository = FakePMGServerRepository(pmg_servers)
    proxmox_repository = FakeProxmoxServerRepository(proxmox_servers)
    delivery = secret_delivery or RecordingSecretDelivery()
    authorization_repository = authorization or FakeAuthorizationRepository()
    tool_run_repository = tool_runs or FakeToolRunRepository()
    action_request_repository = action_requests or FakeActionRequestRepository()
    # One journal across both, so the read path's order is assertable.
    read_path_journal: list[str] = []
    action_result_repository = action_results or FakeActionResultRepository(read_path_journal)
    action_expiry_repository = action_expiry or FakeActionRequestExpiryRepository(read_path_journal)
    result_table_writer = result_tables or FakeToolResultTableWriter()
    audit = RecordingAuditSink()
    resolved_cipher = cipher or build_cipher()

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast("AsyncSession", StubSession())

    def whm_client_factory(server: WHMServerSecretLike, *, cipher: SecretCipher) -> WHMClient:
        return build_whm_client(server, cipher=cipher, transport=whm_transport)

    def proxmox_client_factory(
        server: ProxmoxServerSecretLike, *, cipher: SecretCipher
    ) -> ProxmoxClient:
        return build_proxmox_client(server, cipher=cipher, transport=proxmox_transport)

    return ToolFixture(
        context=McpToolContext(
            session_factory=session_factory,
            secret_cipher=resolved_cipher,
            pending_ttl_seconds=pending_ttl_seconds,
            embed_base_url=embed_base_url,
            result_table_ttl_seconds=result_table_ttl_seconds,
            result_table_max_rows=result_table_max_rows,
            secret_delivery=delivery,
            secret_password_length=secret_password_length,
            secret_delivery_one_time=secret_delivery_one_time,
            secret_delivery_expiration_seconds=secret_delivery_expiration_seconds,
            authorization_repository_factory=lambda _session: authorization_repository,
            whm_server_repository_factory=lambda _session: server_repository,
            pmg_server_repository_factory=lambda _session: pmg_repository,
            proxmox_server_repository_factory=lambda _session: proxmox_repository,
            tool_run_repository_factory=lambda _session: tool_run_repository,
            action_request_repository_factory=lambda _session: action_request_repository,
            action_result_repository_factory=lambda _session: action_result_repository,
            action_request_expiry_repository_factory=lambda _session: action_expiry_repository,
            result_table_writer_factory=lambda _session: result_table_writer,
            whm_client_factory=whm_client_factory,
            proxmox_client_factory=proxmox_client_factory,
            audit_sink=audit,
        ),
        servers=server_repository,
        pmg_servers=pmg_repository,
        proxmox_servers=proxmox_repository,
        secret_delivery=delivery,
        authorization=authorization_repository,
        audit=audit,
        tool_runs=tool_run_repository,
        action_requests=action_request_repository,
        action_results=action_result_repository,
        action_expiry=action_expiry_repository,
        result_tables=result_table_writer,
        cipher=resolved_cipher,
    )


__all__ = [
    "API_TOKEN",
    "CREATED_AT",
    "EMBED_BASE_URL",
    "FINGERPRINT",
    "PENDING_TTL_SECONDS",
    "PROXMOX_API_TOKEN_SECRET",
    "RESULT_TABLE_MAX_ROWS",
    "RESULT_TABLE_TTL_SECONDS",
    "SECRETS",
    "SECRET_DELIVERY_EXPIRATION_SECONDS",
    "SECRET_DELIVERY_ONE_TIME",
    "SECRET_PASSWORD_LENGTH",
    "SSH_PASSWORD",
    "SSH_PRIVATE_KEY",
    "YOPASS_URL",
    "FakePMGServerRepository",
    "FakeProxmoxServerRepository",
    "FakeWHMServerRepository",
    "RecordingSecretDelivery",
    "ToolFixture",
    "build_tool_context",
    "pmg_server",
    "proxmox_server",
    "whm_server",
]
