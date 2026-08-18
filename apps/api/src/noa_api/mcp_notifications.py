"""`notifications/tools/list_changed` on a live MCP session (T66 — V1, V14, V74, C23, R30).

V74 requires NOA to emit this notification when a permission changes. The awkward part is not
the emit — `ServerSession.send_tool_list_changed()` is one call — it is that the *trigger* and
the *session* are on opposite sides of the app. A grant changes inside an admin REST request
authenticated by a cookie; the session that has to be told belongs to a Streamable HTTP
connection authenticated by a bearer token, running in another task. The transport offers no
way to look a session up, so this module keeps a register of them.

Three pieces, in the order a notification travels:

1. `McpSessionRegistryMiddleware` — a fastmcp middleware that remembers, per MCP message,
   which `users.id` is speaking on which `ServerSession`.
2. `McpSessionRegistry` — the register itself, keyed by user id.
3. `McpToolListChangedNotifier` — the `ToolListChangedNotifier` the RBAC engine calls after a
   committed write.

**Read this before reading the rest: at the pinned client, none of it changes what an operator
sees.** R30 settled it by measurement at LibreChat `45cc53c4` — `ToolListChangedNotificationSchema`
has zero handlers tree-wide, `connection.ts:1852` registers the *resource* list-changed schema
only, and a notification NOA provably put on a session's stream drew no `tools/list` after it.
So this ships because it is protocol-correct, costs almost nothing, and a later client may
honour it. What actually keeps a revoked grant from being callable is V1's execution-time
re-check (`noa_api.mcp_rbac`), which is a different mechanism and the one with teeth. Nothing
here is a security control, and no test in this repo asserts that a client refetched — that
would be an assertion about behaviour NOA does not control, red on an upstream whim (V69).

**Why `on_message` and not `on_call_tool`.** The hook has to be the one that runs for *every*
message, because the catalog a client holds comes from `tools/list` and a client can hold one
without ever calling a tool. Registering on tool calls only would leave exactly the sessions
this notification exists for — idle ones displaying a stale catalog — unregistered, and every
test that opened a session and called something would still pass.

**Why weak references.** A Streamable HTTP session ends when its transport is terminated or its
task group unwinds, and neither event has a hook a middleware can subscribe to. A strong
register would therefore grow for the life of the process, holding dead sessions and their
streams. `WeakSet` inverts that: the register stops being a reason for a session to exist, and
a session nobody else holds simply leaves. Sends that fail on a closed stream are dropped too
(below), so the two mechanisms cover each other.
"""

from __future__ import annotations

import weakref
from collections.abc import Collection, Sequence
from typing import Final
from uuid import UUID

import anyio
import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.server.session import ServerSession

from core.auth.mcp_auth_errors import McpAuthError
from noa_api.mcp_request_auth import current_mcp_identity

# How long one session's emit may take before it is abandoned.
#
# A bound is required rather than prudent. The transport's write stream is unbuffered
# (`mcp/server/streamable_http.py`: `create_memory_object_stream[SessionMessage](0)`), so a send
# completes only once the session's message router picks it up — and the router may itself be
# blocked writing to a standalone SSE stream whose client has stopped reading. Without this
# timeout, an admin `PUT /admin/roles/{name}/tools` would hang on a wedged browser tab. One
# second is generous for a same-process memory stream and short enough that the operator's write
# is not visibly delayed even if it elapses; the notification is best-effort either way (V74).
NOTIFY_TIMEOUT_SECONDS: Final = 1.0

# Emitted once per notification, with counts only — never a tool name and never a role, since a
# permission change is already recorded by the audit event that preceded it (V14).
LOG_TOOL_LIST_CHANGED = "mcp_tool_list_changed_emitted"

# One session's emit did not complete inside `NOTIFY_TIMEOUT_SECONDS`. Distinct from a closed
# stream: this one means a client is connected and not reading, which is worth seeing.
LOG_NOTIFY_TIMED_OUT = "mcp_tool_list_changed_timed_out"

# A send failed for a reason that is not a shut stream. Its own event name rather than the one
# above with a flag: a closed session is expected and unlogged, this is a bug worth grepping for.
LOG_NOTIFY_FAILED = "mcp_tool_list_changed_send_failed"

logger = structlog.get_logger(__name__)


class McpSessionRegistry:
    """Which `ServerSession`s belong to which `users.id`, weakly held.

    Written by `McpSessionRegistryMiddleware` on every MCP message, read by
    `McpToolListChangedNotifier`. One instance per app, on `AppRuntime`, so a test app's
    sessions never leak into another's.

    Deliberately not a cache of anything else. It holds no roles, no tokens and no tool sets —
    a register that also remembered permissions would be a second answer to a question
    `AuthorizationService` re-reads per request (V1, V14), and the stale copy would win
    whenever the two disagreed.
    """

    def __init__(self) -> None:
        self._sessions: dict[UUID, weakref.WeakSet[ServerSession]] = {}

    def remember(self, user_id: UUID, session: ServerSession) -> None:
        """Note that `user_id` is speaking on `session`. Idempotent.

        Called on every message rather than once per session, because there is no
        session-opened hook to call it from. A `WeakSet` makes the repeat free.
        """
        self._sessions.setdefault(user_id, weakref.WeakSet()).add(session)

    def sessions_for(self, user_ids: Collection[UUID]) -> list[ServerSession]:
        """Live sessions held by any of `user_ids`, de-duplicated.

        De-duplicated by identity because one user id can appear twice in an audience — a role
        write reads its holders, and nothing stops the same operator being reached by two
        entries in a future fan-out. Sending twice would be harmless and confusing.

        Empty entries are dropped as they are found: a user whose sessions have all been
        collected leaves a `WeakSet` that would otherwise sit in the dict forever.
        """
        found: list[ServerSession] = []
        seen: set[int] = set()
        for user_id in user_ids:
            sessions = self._sessions.get(user_id)
            if sessions is None:
                continue
            for session in sessions:
                if id(session) not in seen:
                    seen.add(id(session))
                    found.append(session)
            if not sessions:
                del self._sessions[user_id]
        return found

    def forget(self, session: ServerSession) -> None:
        """Drop `session` from every user's set.

        Called when a send fails on a closed stream. Not redundant with the weak references:
        the session object may still be alive and held by its own transport task while its
        streams are shut, and re-attempting an emit on it every time an admin writes would be
        a timeout per write.
        """
        for user_id in list(self._sessions):
            sessions = self._sessions[user_id]
            sessions.discard(session)
            if not sessions:
                del self._sessions[user_id]

    # --- Introspection, for the wiring tests ---

    def session_count(self, user_id: UUID) -> int:
        """How many live sessions `user_id` holds. For assertions, not for decisions."""
        sessions = self._sessions.get(user_id)
        return len(sessions) if sessions is not None else 0

    def user_ids(self) -> list[UUID]:
        """Every user id with at least one live session."""
        return [user_id for user_id, sessions in self._sessions.items() if len(sessions) > 0]


class McpSessionRegistryMiddleware(Middleware):
    """Register the caller's session on every MCP message (T66, V74).

    A middleware for the reason every gate in this app is one: a tool cannot forget it, and
    neither can a future request type. `on_message` is the hook that runs for all of them —
    `initialize`, `tools/list`, `tools/call`, notifications — which is what makes an idle
    session, the exact case this notification exists for, reachable.

    **Registration never fails a request.** If identity cannot be resolved the message goes
    through untouched: a message that reached here is already authenticated
    (`RequireAuthMiddleware` answers 401 first), so an unresolved identity means a mount
    assumption stopped holding, and refusing the call over a bookkeeping problem would trade a
    working request for a broken one. The consequence of skipping is a session that misses a
    catalog notification — which V74 already treats as acceptable.
    """

    def __init__(self, *, registry: McpSessionRegistry) -> None:
        self._registry = registry

    async def on_message(
        self,
        context: MiddlewareContext[object],
        call_next: CallNext[object, object],
    ) -> object:
        """Note the (user, session) pair, then get out of the way."""
        self._remember(context)
        return await call_next(context)

    def _remember(self, context: MiddlewareContext[object]) -> None:
        """Resolve the caller and their session; do nothing if either is unavailable."""
        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            # No request context — a server-initiated message, or a direct middleware call in
            # a test. There is no session to register and nothing to report.
            return

        try:
            identity = current_mcp_identity()
            session = fastmcp_context.session
        except (McpAuthError, RuntimeError):
            # `McpAuthError`: no access token on the request (see the class docstring).
            # `RuntimeError`: fastmcp raises it from `.session` when no session exists, which
            # is the in-memory transport case and any future sessionless era (C23).
            return

        self._registry.remember(identity.user_id, session)


class McpToolListChangedNotifier:
    """`ToolListChangedNotifier` over the MCP session register (T66 — V74).

    Emits `notifications/tools/list_changed` to every live session held by the given users. The
    engine calls this after its commit, and it must not raise: see
    `core.auth.tool_list_notifications` for why, and `AuthorizationService` for the log line
    that catches anything this misses.

    Sends run concurrently under one deadline rather than in sequence with a deadline each, so
    an operator whose write affects twenty sessions waits once. Cancellation of an in-flight
    send on an unbuffered memory stream simply means the message was not handed over — there is
    no half-written frame to clean up.
    """

    def __init__(self, *, registry: McpSessionRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> McpSessionRegistry:
        """The register this notifier reads.

        Exposed so a test can assert the two ends are the *same* object — the MCP mount writes
        one and the admin surface reads one, and if they were ever two the emit would reach zero
        sessions with nothing failing (V74's best-effort makes that silent). Read-only, and the
        register itself answers only counts and ids: nothing here decides a permission.
        """
        return self._registry

    async def notify(self, user_ids: Collection[UUID]) -> None:
        """Emit to every live session of `user_ids`. Best-effort, bounded, never raises."""
        sessions = self._registry.sessions_for(user_ids)
        if not sessions:
            # Nobody is connected. Not a failure and not worth a log line: the common case for
            # a deployment whose operators are not all in a chat window at once.
            return

        emitted = await self._emit_all(sessions)
        logger.info(
            LOG_TOOL_LIST_CHANGED,
            user_count=len(set(user_ids)),
            session_count=len(sessions),
            emitted=emitted,
        )

    # --- Internals ---

    async def _emit_all(self, sessions: Sequence[ServerSession]) -> int:
        """Emit to each session concurrently; return how many were handed over.

        The counter is a list rather than an `int` because each send runs in its own task. It
        is what the log line reports, and it is what separates "nobody was connected" from
        "everyone was connected and every stream was shut".
        """
        delivered: list[ServerSession] = []
        with anyio.move_on_after(NOTIFY_TIMEOUT_SECONDS) as scope:
            async with anyio.create_task_group() as task_group:
                for session in sessions:
                    task_group.start_soon(self._emit_one, session, delivered)

        if scope.cancelled_caught:
            # At least one session did not take the message in time. The others may have
            # succeeded; `delivered` says which, and the emit is not retried — a notification
            # whose whole value is timeliness is not worth a queue (V74).
            logger.warning(
                LOG_NOTIFY_TIMED_OUT,
                timeout_seconds=NOTIFY_TIMEOUT_SECONDS,
                emitted=len(delivered),
                session_count=len(sessions),
            )

        return len(delivered)

    async def _emit_one(self, session: ServerSession, delivered: list[ServerSession]) -> None:
        """One session's emit. A shut stream drops the session; nothing propagates.

        `anyio.ClosedResourceError` / `BrokenResourceError` mean the session ended between the
        register read and this send — an operator closed their chat, or the transport was
        terminated. That is ordinary, so it is dropped from the register (see
        `McpSessionRegistry.forget`) and not logged: an admin writing a grant while nobody is
        connected would otherwise produce a warning per stale entry.

        Anything else is swallowed too, and for the harder reason: this runs inside a task
        group, so an exception would propagate as a `BaseExceptionGroup` out of
        `notify` — turning a committed permission change into a 500 on the operator's write.
        The engine logs what reaches it; this makes sure nothing does.
        """
        try:
            await session.send_tool_list_changed()
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            self._registry.forget(session)
        # Broad on purpose — see the docstring: nothing may escape into the task group.
        except Exception as exc:
            logger.warning(LOG_NOTIFY_FAILED, error_type=type(exc).__name__)
        else:
            delivered.append(session)


__all__ = [
    "LOG_NOTIFY_FAILED",
    "LOG_NOTIFY_TIMED_OUT",
    "LOG_TOOL_LIST_CHANGED",
    "NOTIFY_TIMEOUT_SECONDS",
    "McpSessionRegistry",
    "McpSessionRegistryMiddleware",
    "McpToolListChangedNotifier",
]
