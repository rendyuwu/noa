"""Doubles for the server-inventory read path (T19).

Two decisions here carry the weight of the tests that use this module.

**Rows are real `WHMServer` instances, not stand-ins.** The V8 assertion is that nothing a
tool emits carries credential material, and the thing that decides that is
`WHMServer.to_safe_dict`. A hand-written fake with a hand-written `to_safe_dict` would test
the fake. So `whm_server()` constructs the mapped class and fills the three server-defaulted
columns (`id`, `created_at`, `updated_at`) by hand — an unsaved instance has them as `None`,
and the resolver's UUID branch needs a real id.

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

from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import WHMServer
from noa_api.mcp_tools.context import McpToolContext
from support.mcp_identity import StubSession
from support.rbac import FakeAuthorizationRepository, RecordingAuditSink

# Fixed, so `to_safe_dict` output is comparable across runs.
CREATED_AT = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

# Ciphertext-shaped, because that is what the column holds (C7, V48). The point of these
# literals is that they must not appear in any tool result.
API_TOKEN = "enc:v1:fernet:whm-api-token"
SSH_PASSWORD = "enc:v1:fernet:whm-ssh-password"
SSH_PRIVATE_KEY = "enc:v1:fernet:whm-ssh-private-key"

# Every credential literal above, for a single "none of these leaked" assertion.
SECRETS = (API_TOKEN, SSH_PASSWORD, SSH_PRIVATE_KEY)

FINGERPRINT = "SHA256:l3Rz6cS0nOtASecret+ItIsAPublicKeyDigest"


def whm_server(
    name: str,
    *,
    base_url: str | None = None,
    server_id: UUID | None = None,
    ssh_username: str | None = "noa",
) -> WHMServer:
    """One `whm_servers` row, credentials included, ready to read."""
    server = WHMServer(
        name=name,
        base_url=base_url or f"https://{name}.example.net:2087",
        api_username="root",
        api_token=API_TOKEN,
        verify_ssl=True,
        ssh_username=ssh_username,
        ssh_port=22,
        ssh_password=SSH_PASSWORD,
        ssh_private_key=SSH_PRIVATE_KEY,
        ssh_host_key_fingerprint=FINGERPRINT,
    )
    # Server-side defaults (`gen_random_uuid()`, `now()`) are not applied to an instance
    # that never reached Postgres.
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
        # Read counter: V1/V14 care that inventory is read per call, never memoized.
        self.reads = 0

    async def list_servers(self) -> Sequence[WHMServer]:
        self.reads += 1
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> WHMServer | None:
        self.reads += 1
        return next((server for server in self.servers if server.id == server_id), None)


@dataclass
class ToolFixture:
    """An `McpToolContext` over doubles, plus the doubles behind it."""

    context: McpToolContext
    servers: FakeWHMServerRepository
    authorization: FakeAuthorizationRepository
    audit: RecordingAuditSink


def build_tool_context(
    *,
    servers: Iterable[WHMServer] = (),
    authorization: FakeAuthorizationRepository | None = None,
) -> ToolFixture:
    """A production `McpToolContext` whose repositories are in-memory.

    The context, the RBAC engine and the tool functions are all production code; the session
    is a stub both repositories ignore, which is what lets these tests run without Postgres.
    """
    server_repository = FakeWHMServerRepository(servers)
    authorization_repository = authorization or FakeAuthorizationRepository()
    audit = RecordingAuditSink()

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast("AsyncSession", StubSession())

    return ToolFixture(
        context=McpToolContext(
            session_factory=session_factory,
            authorization_repository_factory=lambda _session: authorization_repository,
            whm_server_repository_factory=lambda _session: server_repository,
            audit_sink=audit,
        ),
        servers=server_repository,
        authorization=authorization_repository,
        audit=audit,
    )


__all__ = [
    "API_TOKEN",
    "CREATED_AT",
    "FINGERPRINT",
    "SECRETS",
    "SSH_PASSWORD",
    "SSH_PRIVATE_KEY",
    "FakeWHMServerRepository",
    "ToolFixture",
    "build_tool_context",
    "whm_server",
]
