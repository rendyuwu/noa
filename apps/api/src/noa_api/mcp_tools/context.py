"""What a tool needs to reach the database, built once at startup (T19).

The twin of `McpAuthContext` (T12), and deliberately its twin: a frozen value object with a
session factory plus repository factories that production never passes. Same reasons.

- **One world, not two configured alike.** `create_app` builds this off the same
  `AppRuntime.session_factory` the verifier and the admin routes use, so an operator
  disabled through `/admin` is refused on their next tool call by the row that write
  touched (V1) — not by a second pool that will agree eventually.
- **Injectable repositories are the test seam**, and the only one. The real
  `AuthorizationService`, the real resolver and the real tool functions run in the suite;
  only the SQL is doubled. A seam at the service level would let a test pass against a
  policy the production wiring does not have.

Separate from `McpAuthContext` rather than folded into it because the two answer different
questions and are consulted at different times: authentication resolves once per request,
inside `verify_token`, and knows nothing about tools; this is read per tool call and per
`tools/list`. Merging them would put the tool repositories behind the auth path, where a
bug in either becomes a 401.

The cipher (T21) is the one field that is neither a session nor a repository, and it is here
for the same reason the rest is: `SecretCipher` needs `Settings`, `noa_api.main.build_runtime`
is the single `get_settings()` caller, and a tool reaching for its own would be a second copy
of the world (C7, T15). It arrives already constructed, so a bad `NOA_SECRET_ENCRYPTION_KEY`
stops startup rather than surfacing as a failed tool call.

Session lifetime is per operation, not per request: each tool, each RBAC check, each of the
two `tool_runs` writes (T73) and the CHANGE gate's INSERT (T33) opens one, uses it, closes
it. Those last three are the only ones that commit, and they commit *separately* on
purpose — see `core.audit.tool_runs` and `core.approvals.repository`.

`pending_ttl_seconds` and `embed_base_url` are the two plain scalars here, and they are here
for the same reason the cipher is: `APPROVAL_PENDING_TTL_SECONDS` and `NOA_EMBED_BASE_URL` are
settings, `noa_api.main.build_runtime` is the single `get_settings()` caller, and a gate
reaching for its own copy would be a second world (T15, C7). They arrive resolved so the
deadline on every pending request — and the address on every approval card — are assertable
from a test without patching configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.approvals.expiry import (
    ActionRequestExpiryRepository,
    ActionRequestExpiryService,
    SQLActionRequestExpiryRepository,
)
from core.approvals.repository import ActionRequestRepository, SQLActionRequestRepository
from core.approvals.results import (
    ActionResultRepository,
    ActionResultService,
    SQLActionResultRepository,
)
from core.audit.admin_events import AdminAuditSink, StructlogAdminAuditSink
from core.audit.tool_runs import SQLToolRunRepository, ToolRunRepository
from core.auth.authorization_repository import SQLAuthorizationRepository
from core.auth.authorization_service import AuthorizationService
from core.auth.authorization_types import AuthorizationRepository
from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.integrations.proxmox.client import ProxmoxClientFactory, build_proxmox_client
from core.integrations.whm.ssh import WHMClientFactory, build_whm_client
from core.results.tables import SQLToolResultTableWriter, ToolResultTableWriter
from core.secrets.crypto import SecretCipher
from core.secrets.delivery import SecretDelivery
from core.servers.pmg_repository import PMGServerReadRepository, SQLPMGServerRepository
from core.servers.proxmox_repository import (
    ProxmoxServerReadRepository,
    SQLProxmoxServerRepository,
)
from core.servers.whm_repository import SQLWHMServerRepository, WHMServerReadRepository
from noa_api.mcp_request_auth import McpSessionFactory


@dataclass(frozen=True)
class McpToolContext:
    """Everything the MCP tool path needs, built once at startup."""

    session_factory: McpSessionFactory
    # One cipher per app, held rather than built per call: `Fernet` is stateless after
    # construction, and a per-call build would re-validate the key on every tool call and
    # turn a misconfigured key into an intermittent tool failure instead of a boot failure
    # (T15 — settings are injected here, there is no cipher singleton to reach for).
    secret_cipher: SecretCipher
    # `APPROVAL_PENDING_TTL_SECONDS` (V32). Required rather than defaulted: a default here
    # would be a second answer to "how long may a request stay pending", and the one that
    # drifts silently is always the copy nobody edits.
    pending_ttl_seconds: int
    # `NOA_EMBED_BASE_URL` — the origin the approval card is served from (T32, V26). Required
    # rather than defaulted for the reason above and one more: a default here would be a URL
    # that works on a developer's laptop, so a deployment that forgot the variable would hand
    # every operator a `localhost` address instead of failing at startup. Normalized by
    # `core.config` (trailing slashes stripped), so callers join a path onto it directly.
    embed_base_url: str
    # `RESULT_TABLE_TTL_SECONDS` (V64) — how long a parked table stays readable behind its
    # URL. Required rather than defaulted, for `pending_ttl_seconds`' reason one field up: a
    # default here would be a second answer to a question settings already answer, and the
    # copy that drifts is always the one nobody edits.
    result_table_ttl_seconds: int
    # `RESULT_TABLE_MAX_ROWS` (V85) — how many rows one parked table may hold. Required for
    # the same reason, and with one of its own: a cap that could be forgotten at a wiring site
    # is a cap that silently becomes "all of them" for whichever tool wired it last.
    result_table_max_rows: int
    # How a generated credential reaches the operator (T27, C15, V49). Required rather than
    # defaulted, and it is the one field here that could not be a scalar: `_yopass_store` needs
    # three settings, `McpToolContext` holds no `Settings` (T15 deleted the singleton
    # `noa-old` imported), and `noa_api.main.build_runtime` is the single `get_settings()`
    # caller (C7). So the configuration is bound into a callable once, at startup, and a tool
    # asks for delivery rather than for a URL. `core.secrets.delivery` carries the argument.
    secret_delivery: SecretDelivery
    # `SECRET_PASSWORD_LENGTH` (C15, V49) — how long a server-side generated password is.
    # Required for `pending_ttl_seconds`' reason: a default here would be a second answer to a
    # question settings already answer, and the copy that drifts is the one nobody edits.
    secret_password_length: int
    authorization_repository_factory: Callable[[AsyncSession], AuthorizationRepository] = (
        SQLAuthorizationRepository
    )
    # Typed to the concrete row, not to `WHMServerRowLike`: a tool that resolves a server then
    # calls it needs the credentials off *that* row, and the repository Protocol is generic
    # precisely so this can say so without widening the narrow view `core.servers.whm_ref`
    # matches against (T21).
    whm_server_repository_factory: Callable[[AsyncSession], WHMServerReadRepository[WHMServer]] = (
        SQLWHMServerRepository
    )
    # Same construction, one system over (T31). Typed to `PMGServer` rather than to
    # `PMGServerRowLike` because the whitelist tools resolve a node and then *connect* to it,
    # which needs the SSH columns off the row that won the resolution.
    pmg_server_repository_factory: Callable[[AsyncSession], PMGServerReadRepository[PMGServer]] = (
        SQLPMGServerRepository
    )
    # Same construction, third system (T27). Typed to `ProxmoxServer` rather than to
    # `ProxmoxServerRowLike` for the reason the two above are: `proxmox_reset_vm_password`
    # resolves an endpoint and then *calls* it, which needs the API token off the row that won
    # the resolution — not a second read by id that could disagree with the list a tie was
    # judged against.
    proxmox_server_repository_factory: Callable[
        [AsyncSession], ProxmoxServerReadRepository[ProxmoxServer]
    ] = SQLProxmoxServerRepository
    tool_run_repository_factory: Callable[[AsyncSession], ToolRunRepository] = SQLToolRunRepository
    # The CHANGE gate's writer (T33). Beside the audit one and not folded into it: they write
    # different tables at different moments — this one before a change is authorised, that one
    # around a READ that already ran — and a single repository would invite a caller to reach
    # the wrong write from the wrong side of the approval boundary.
    action_request_repository_factory: Callable[[AsyncSession], ActionRequestRepository] = (
        SQLActionRequestRepository
    )
    # T63's reader, and it is only a reader: `SQLActionResultRepository` holds no statement
    # that is not a `SELECT`. Beside the writer above rather than folded into it for the
    # reason `core.approvals` splits its classes by who can reach which write — a reader that
    # shared a class with the PENDING writer would be a reason to hand the read path one.
    action_result_repository_factory: Callable[[AsyncSession], ActionResultRepository] = (
        SQLActionResultRepository
    )
    # The one write the read path may make (T39): a PENDING request past its deadline becomes
    # EXPIRED before it is served, so no GET shows a state nobody may act on (V32). This
    # repository can write that status and no other.
    action_request_expiry_repository_factory: Callable[
        [AsyncSession], ActionRequestExpiryRepository
    ] = SQLActionRequestExpiryRepository
    # T56's writer: where a large READ parks its rows so the answer costs no tokens (V64).
    # A writer and nothing else — `SQLToolResultTableWriter` has no read method, and the
    # surface that reads a table back hangs off a cookie on the far side of V22's boundary.
    result_table_writer_factory: Callable[[AsyncSession], ToolResultTableWriter] = (
        SQLToolResultTableWriter
    )
    # The WHM API seam. Production builds a real client over a real socket; a tool test swaps
    # in the same factory with an `httpx` transport, so the client, the cipher and the one
    # decrypt site all stay in the path and only the socket is doubled.
    whm_client_factory: WHMClientFactory = build_whm_client
    # The Proxmox seam, one system over and on the same terms (T27): production builds a real
    # client over a real socket, a tool test swaps in the same factory with an `httpx` transport,
    # so the client, the cipher and the one decrypt site all stay in the path.
    proxmox_client_factory: ProxmoxClientFactory = build_proxmox_client
    # A sink, even though the read path records nothing: `AuthorizationService` takes one,
    # and handing it a working sink rather than a stub means the day a tool records an event
    # it goes somewhere real instead of into a placeholder nobody re-checked.
    audit_sink: AdminAuditSink = field(default_factory=StructlogAdminAuditSink)


def build_mcp_tool_context(
    *,
    session_factory: McpSessionFactory,
    secret_cipher: SecretCipher,
    pending_ttl_seconds: int,
    embed_base_url: str,
    result_table_ttl_seconds: int,
    result_table_max_rows: int,
    secret_delivery: SecretDelivery,
    secret_password_length: int,
) -> McpToolContext:
    """Production wiring (T13's `create_app` calls this beside `build_mcp_auth_context`)."""
    return McpToolContext(
        session_factory=session_factory,
        secret_cipher=secret_cipher,
        pending_ttl_seconds=pending_ttl_seconds,
        embed_base_url=embed_base_url,
        result_table_ttl_seconds=result_table_ttl_seconds,
        result_table_max_rows=result_table_max_rows,
        secret_delivery=secret_delivery,
        secret_password_length=secret_password_length,
    )


def build_authorization_service(
    context: McpToolContext, session: AsyncSession
) -> AuthorizationService:
    """The RBAC engine over one session (T9).

    Constructed per check rather than held on the context, because `AuthorizationService`
    snapshots its repository and the repository holds a session. A long-lived service would
    pin one connection for the life of the process and answer every later question through
    it.

    **No `tool_list_notifier`** (T66), so this instance takes the null one. The MCP path asks
    this service questions and writes no permission — a notification is something a *write*
    emits, and there is no write here to emit one. Handing the real notifier in anyway would
    put the emit within reach of the bearer-token side of the app, which is the boundary V22
    draws for the decision path and worth respecting here for free.
    """
    return AuthorizationService(
        repository=context.authorization_repository_factory(session),
        audit_sink=context.audit_sink,
    )


def build_tool_run_repository(context: McpToolContext, session: AsyncSession) -> ToolRunRepository:
    """The `tool_runs` writer over one session (T73, V45).

    A function rather than a bare factory call so `noa_api.mcp_audit` names one thing for
    both of its writes, and so this reads the same way as `build_authorization_service`
    beside it. Constructed per write for the same reason: the repository holds the session,
    and a long-lived one would pin a connection for the life of the process.
    """
    return context.tool_run_repository_factory(session)


def build_action_request_repository(
    context: McpToolContext, session: AsyncSession
) -> ActionRequestRepository:
    """The `action_requests` writer over one session (T33, V23).

    Reads the same way as the two functions above it, and is constructed per write for the
    same reason: the repository holds the session, and a long-lived one would pin a
    connection for the life of the process.
    """
    return context.action_request_repository_factory(session)


def build_result_table_writer(
    context: McpToolContext, session: AsyncSession
) -> ToolResultTableWriter:
    """The `tool_result_tables` writer over one session (T56, V64).

    Constructed per write like the three above it, and for the same reason: the repository
    holds the session, and a long-lived one would pin a connection for the life of the
    process.
    """
    return context.result_table_writer_factory(session)


def build_action_result_service(
    context: McpToolContext, session: AsyncSession
) -> ActionResultService:
    """The approval-result read path over one session (T63, V27, V32).

    Both collaborators are built here rather than on the context, for the reason
    `build_authorization_service` gives: each holds the session, and a long-lived one would
    pin a connection for the life of the process. Constructing them together is also what
    puts the read and the expiry write in the *same* session, so the row this answers about
    is the row the deadline was judged against.
    """
    return ActionResultService(
        repository=context.action_result_repository_factory(session),
        expiry=ActionRequestExpiryService(
            context.action_request_expiry_repository_factory(session)
        ),
    )


__all__ = [
    "McpToolContext",
    "build_action_request_repository",
    "build_action_result_service",
    "build_authorization_service",
    "build_mcp_tool_context",
    "build_result_table_writer",
    "build_tool_run_repository",
]
