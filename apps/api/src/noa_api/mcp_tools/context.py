"""What a tool needs to reach the database, built once at startup.

The twin of `McpAuthContext`, and deliberately its twin: a frozen value object with a
session factory plus repository factories that production never passes. Same reasons.

- **One world, not two configured alike.** `create_app` builds this off the same
  `AppRuntime.session_factory` the verifier and the admin routes use, so an operator
  disabled through `/admin` is refused on their next tool call by the row that write
  touched — not by a second pool that will agree eventually.
- **Injectable repositories are the test seam**, and the only one. The real
  `AuthorizationService`, the real resolver and the real tool functions run in the suite;
  only the SQL is doubled. A seam at the service level would let a test pass against a
  policy the production wiring does not have.

Separate from `McpAuthContext` rather than folded into it because the two answer different
questions and are consulted at different times: authentication resolves once per request,
inside `verify_token`, and knows nothing about tools; this is read per tool call and per
`tools/list`. Merging them would put the tool repositories behind the auth path, where a
bug in either becomes a 401.

The cipher is the one field that is neither a session nor a repository, and it is here
for the same reason the rest is: `SecretCipher` needs `Settings`, `noa_api.main.build_runtime`
is the single `get_settings()` caller, and a tool reaching for its own would be a second copy
of the world. It arrives already constructed, so a bad `NOA_SECRET_ENCRYPTION_KEY`
stops startup rather than surfacing as a failed tool call.

Session lifetime is per operation, not per request: each tool, each RBAC check, each of the
two `tool_runs` writes and the CHANGE gate's INSERT opens one, uses it, closes
it. Those last three are the only ones that commit, and they commit *separately* on
purpose — see `core.audit.tool_runs` and `core.approvals.repository`.

`pending_ttl_seconds` and `embed_base_url` are the two plain scalars here, and they are here
for the same reason the cipher is: `APPROVAL_PENDING_TTL_SECONDS` and `NOA_EMBED_BASE_URL` are
settings, `noa_api.main.build_runtime` is the single `get_settings()` caller, and a gate
reaching for its own copy would be a second world. They arrive resolved so the
deadline on every pending request — and the address on every approval card — are assertable
from a test without patching configuration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Final

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
from core.servers.reference import ServerRefRepository
from core.servers.repository import SQLServerRepository
from noa_api.mcp_request_auth import McpSessionFactory

# The three inventory read factories, bound to their table. Named constants rather than
# `partial(...)` written straight into the field defaults below, because a call expression in a
# dataclass default is `RUF009` — the wiring is the same either way.
_WHM_SERVER_READS: Final = partial(SQLServerRepository, model=WHMServer)
_PMG_SERVER_READS: Final = partial(SQLServerRepository, model=PMGServer)
_PROXMOX_SERVER_READS: Final = partial(SQLServerRepository, model=ProxmoxServer)


@dataclass(frozen=True)
class McpToolContext:
    """Everything the MCP tool path needs, built once at startup."""

    session_factory: McpSessionFactory
    # One cipher per app, held rather than built per call: `Fernet` is stateless after
    # construction, and a per-call build would re-validate the key on every tool call and
    # turn a misconfigured key into an intermittent tool failure instead of a boot failure
    # (the secrets port — settings are injected here, there is no cipher singleton to reach for).
    secret_cipher: SecretCipher
    # `APPROVAL_PENDING_TTL_SECONDS`. Required rather than defaulted: a default here
    # would be a second answer to "how long may a request stay pending", and the one that
    # drifts silently is always the copy nobody edits.
    pending_ttl_seconds: int
    # `NOA_EMBED_BASE_URL` — the origin the approval card is served from. Required
    # rather than defaulted for the reason above and one more: a default here would be a URL
    # that works on a developer's laptop, so a deployment that forgot the variable would hand
    # every operator a `localhost` address instead of failing at startup. Normalized by
    # `core.config` (trailing slashes stripped), so callers join a path onto it directly.
    embed_base_url: str
    # `RESULT_TABLE_TTL_SECONDS` — how long a parked table stays readable behind its
    # URL. Required rather than defaulted, for `pending_ttl_seconds`' reason one field up: a
    # default here would be a second answer to a question settings already answer, and the
    # copy that drifts is always the one nobody edits.
    result_table_ttl_seconds: int
    # `RESULT_TABLE_MAX_ROWS` — how many rows one parked table may hold. Required for
    # the same reason, and with one of its own: a cap that could be forgotten at a wiring site
    # is a cap that silently becomes "all of them" for whichever tool wired it last.
    result_table_max_rows: int
    # How a generated credential reaches the operator. Required rather than
    # defaulted, and it is the one field here that could not be a scalar: `_yopass_store` needs
    # three settings, `McpToolContext` holds no `Settings` (the secrets port deleted the
    # singleton `noa-old` imported), and `noa_api.main.build_runtime` is the single `get_settings()`
    # caller. So the configuration is bound into a callable once, at startup, and a tool
    # asks for delivery rather than for a URL. `core.secrets.delivery` carries the argument.
    secret_delivery: SecretDelivery
    # `SECRET_PASSWORD_LENGTH` — how long a server-side generated password is.
    # Required for `pending_ttl_seconds`' reason: a default here would be a second answer to a
    # question settings already answer, and the copy that drifts is the one nobody edits.
    secret_password_length: int
    # `YOPASS_ONE_TIME` and `YOPASS_SECRET_EXPIRATION_SECONDS`, resolved here for the reason the
    # scalars above are: there is no settings singleton to reach for. `secret_delivery` binds the
    # same two values into the delivery hop, and these are the same two facts said to the operator
    # rather than to the service — how long the link a runner hands over keeps working, and whether
    # opening it spends it. Named after the seam rather than after yopass, because the tool context
    # does not know which service delivers (`core.secrets.delivery`).
    #
    # Held rather than written into a sentence: a runner that spelled the duration out would be
    # true for one deployment and silently false the day either variable is changed, which is
    # exactly the correction that put them here.
    secret_delivery_one_time: bool
    secret_delivery_expiration_seconds: int
    authorization_repository_factory: Callable[[AsyncSession], AuthorizationRepository] = (
        SQLAuthorizationRepository
    )
    # Typed to the concrete row, not to `UrlServerRowLike`: a tool that resolves a server then
    # calls it needs the credentials off *that* row, and the repository Protocol is generic
    # precisely so this can say so without widening the narrow view `core.servers.reference`
    # matches against.
    whm_server_repository_factory: Callable[[AsyncSession], ServerRefRepository[WHMServer]] = (
        _WHM_SERVER_READS
    )
    # Same construction, one system over. Typed to `PMGServer` rather than to
    # `SSHHostServerRowLike` because the whitelist tools resolve a node and then *connect* to it,
    # which needs the SSH columns off the row that won the resolution.
    pmg_server_repository_factory: Callable[[AsyncSession], ServerRefRepository[PMGServer]] = (
        _PMG_SERVER_READS
    )
    # Same construction, third system. Typed to `ProxmoxServer` rather than to
    # `UrlServerRowLike` for the reason the two above are: `proxmox_reset_vm_password`
    # resolves an endpoint and then *calls* it, which needs the API token off the row that won
    # the resolution — not a second read by id that could disagree with the list a tie was
    # judged against.
    proxmox_server_repository_factory: Callable[
        [AsyncSession], ServerRefRepository[ProxmoxServer]
    ] = _PROXMOX_SERVER_READS
    tool_run_repository_factory: Callable[[AsyncSession], ToolRunRepository] = SQLToolRunRepository
    # The CHANGE gate's writer. Beside the audit one and not folded into it: they write
    # different tables at different moments — this one before a change is authorised, that one
    # around a READ that already ran — and a single repository would invite a caller to reach
    # the wrong write from the wrong side of the approval boundary.
    action_request_repository_factory: Callable[[AsyncSession], ActionRequestRepository] = (
        SQLActionRequestRepository
    )
    # The action-result tool's reader, and it is only a reader: `SQLActionResultRepository` holds no
    # statement that is not a `SELECT`. Beside the writer above rather than folded into it for the
    # reason `core.approvals` splits its classes by who can reach which write — a reader that shared
    # a class with the PENDING writer would be a reason to hand the read path one.
    action_result_repository_factory: Callable[[AsyncSession], ActionResultRepository] = (
        SQLActionResultRepository
    )
    # The one write the read path may make: a PENDING request past its deadline becomes
    # EXPIRED before it is served, so no GET shows a state nobody may act on. This
    # repository can write that status and no other.
    action_request_expiry_repository_factory: Callable[
        [AsyncSession], ActionRequestExpiryRepository
    ] = SQLActionRequestExpiryRepository
    # The large-READ table's writer: where a large READ parks its rows so the answer costs no
    # tokens. A writer and nothing else — `SQLToolResultTableWriter` has no read method, and the
    # surface that reads a table back hangs off a cookie on the far side of the cookie/CSRF
    # boundary.
    result_table_writer_factory: Callable[[AsyncSession], ToolResultTableWriter] = (
        SQLToolResultTableWriter
    )
    # The WHM API seam. Production builds a real client over a real socket; a tool test swaps
    # in the same factory with an `httpx` transport, so the client, the cipher and the one
    # decrypt site all stay in the path and only the socket is doubled.
    whm_client_factory: WHMClientFactory = build_whm_client
    # The Proxmox seam, one system over and on the same terms: production builds a real
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
    secret_delivery_one_time: bool,
    secret_delivery_expiration_seconds: int,
    whm_read_timeout_seconds: float,
) -> McpToolContext:
    """Production wiring (`create_app`, beside `build_mcp_auth_context`, at the FastMCP mount)."""
    return McpToolContext(
        session_factory=session_factory,
        secret_cipher=secret_cipher,
        pending_ttl_seconds=pending_ttl_seconds,
        embed_base_url=embed_base_url,
        result_table_ttl_seconds=result_table_ttl_seconds,
        result_table_max_rows=result_table_max_rows,
        secret_delivery=secret_delivery,
        secret_password_length=secret_password_length,
        secret_delivery_one_time=secret_delivery_one_time,
        secret_delivery_expiration_seconds=secret_delivery_expiration_seconds,
        # The configured WHM read deadline, bound onto the factory once here rather than passed
        # at each tool's call site: every tool asks for a client the same way, so binding it at
        # the seam is what keeps one deployment from waiting two different lengths. The default
        # on the field below is the unbound factory, which falls back to the client's own
        # measured default — a test that supplies its own factory is unaffected either way.
        whm_client_factory=partial(build_whm_client, read_timeout_seconds=whm_read_timeout_seconds),
    )


def build_authorization_service(
    context: McpToolContext, session: AsyncSession
) -> AuthorizationService:
    """The RBAC engine over one session.

    Constructed per check rather than held on the context, because `AuthorizationService`
    snapshots its repository and the repository holds a session. A long-lived service would
    pin one connection for the life of the process and answer every later question through
    it.

    **No `tool_list_notifier`**, so this instance takes the null one. The MCP path asks
    this service questions and writes no permission — a notification is something a *write*
    emits, and there is no write here to emit one. Handing the real notifier in anyway would
    put the emit within reach of the bearer-token side of the app, which is the boundary the
    cookie/CSRF design draws for the decision path and worth respecting here for free.
    """
    return AuthorizationService(
        repository=context.authorization_repository_factory(session),
        audit_sink=context.audit_sink,
    )


def build_action_result_service(
    context: McpToolContext, session: AsyncSession
) -> ActionResultService:
    """The approval-result read path over one session.

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
