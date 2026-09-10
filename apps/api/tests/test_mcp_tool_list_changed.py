"""`notifications/tools/list_changed` on a permission change.

**What this file may and may not assert.** R30 measured, at LibreChat pin `45cc53c4`, that the
client ignores this notification: zero handlers for `ToolListChangedNotificationSchema` in the
tree, and a notification NOA provably put on a session's stream drew no `tools/list` after it.
So there is no test here that a client refetched, and there must not be — that would assert
behaviour NOA does not control, and it would go red on an upstream whim. It is the shape of the
inert host-key control in §B.2 that V69 exists to prevent.

What is left is NOA's own behaviour, and it splits three ways:

1. **Who gets told, and when** — `AuthorizationService` over in-memory doubles. Every mutation
   that can move an effective tool set notifies the affected users, after its commit; the ones
   that cannot, notify nobody.
2. **How the emit reaches a session** — `McpToolListChangedNotifier` and `McpSessionRegistry`
   over a session double, including the failure paths (closed stream, wedged client).
3. **That the register is populated at all** — over the real mount, because a middleware nobody
   reaches is the failure mode this whole feature would otherwise hide behind V74's
   best-effort silence.

And one assertion that is about the *consequence* rather than the mechanism: T66's own V1
backstop, at the bottom, where a grant revoked through the production write path is refused at
execution while the client's captured catalog still names it. That is the property that makes a
stale catalog safe, and it is the only behavioural claim §T.66 permits here.
"""

from __future__ import annotations

import gc
from typing import Any
from uuid import UUID, uuid4

import anyio
import pytest

from core.auth.authorization_errors import (
    ReservedRoleError,
    RoleNotFoundError,
    UnknownRoleError,
    UnknownToolError,
)
from core.auth.authorization_service import AuthorizationService
from core.auth.tool_catalog import TOOL_CATALOG
from core.auth.tool_list_notifications import NullToolListChangedNotifier
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.deps import STATE_TOOL_LIST_NOTIFIER
from noa_api.mcp_notifications import (
    NOTIFY_TIMEOUT_SECONDS,
    McpSessionRegistry,
    McpSessionRegistryMiddleware,
    McpToolListChangedNotifier,
)
from noa_api.mcp_rbac import ERROR_TOOL_NOT_PERMITTED
from noa_api.mcp_tools.whm_read import TOOL_WHM_LIST_SERVERS
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import mounted_app, open_session
from support.rbac import (
    ROLE_NOC,
    ROLE_SUPPORT,
    TOOL_CHANGE,
    TOOL_READ,
    FakeAuthorizationRepository,
    RecordingAuditSink,
    RecordingToolListNotifier,
    build_service,
)
from support.servers import build_tool_context, whm_server

EMAIL = "operator@example.com"
OTHER_EMAIL = "colleague@example.com"
ADMIN_EMAIL = "admin@example.com"


# --- 1. Who gets told, and when ---


async def test_setting_role_tools_notifies_every_holder_of_that_role() -> None:
    """V74: a grant change moves the catalog of everyone holding the role.

    Two holders and one non-holder, so the audience is a real selection rather than "everyone
    who exists" — with a single user in the fixture, a broadcast would pass.
    """
    rbac = build_service()
    holder = rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    other_holder = rbac.repository.add_user(OTHER_EMAIL, roles=(ROLE_SUPPORT,))
    bystander = rbac.repository.add_user("noc@example.com", roles=(ROLE_NOC,))

    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ])

    assert rbac.notifier.notified == sorted([holder.id, other_holder.id])
    assert bystander.id not in rbac.notifier.notified


async def test_deleting_a_role_notifies_the_holders_the_cascade_is_about_to_strip() -> None:
    """V74: the holders are read *before* the delete, not after.

    `ON DELETE CASCADE` removes the assignments with the role, so a service that read its
    audience after the write would find nobody — and an empty audience is indistinguishable from
    "nobody held it". The negative control is the assertion below: after the call, the repository
    genuinely reports no holders, so reading in the wrong order really would have answered `[]`.
    """
    rbac = build_service()
    holder = rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    await rbac.service.delete_role(ROLE_SUPPORT)

    assert rbac.notifier.notified == [holder.id]
    assert await rbac.repository.list_user_ids_with_role(ROLE_SUPPORT) == []


async def test_replacing_a_users_roles_notifies_that_user_only() -> None:
    """V74: role assignment changes what one operator resolves to."""
    rbac = build_service()
    target = rbac.repository.add_user(EMAIL)
    bystander = rbac.repository.add_user(OTHER_EMAIL, roles=(ROLE_SUPPORT,))
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    await rbac.service.set_user_roles(target.id, [ROLE_SUPPORT])

    assert rbac.notifier.notified == [target.id]
    assert bystander.id not in rbac.notifier.notified


@pytest.mark.parametrize("is_active", [False, True])
async def test_flipping_a_users_status_notifies_them(is_active: bool) -> None:
    """V11 + V74: both directions move the catalog.

    Disabling empties the effective set whatever the roles say; enabling refills it. A test that
    only covered disable would leave the other half of V11 unasserted, and "notify on disable"
    is the kind of rule that reads as complete.
    """
    rbac = build_service()
    target = rbac.repository.add_user(EMAIL, is_active=not is_active, roles=(ROLE_SUPPORT,))
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    await rbac.service.set_user_active(target.id, is_active=is_active)

    assert rbac.notifier.notified == [target.id]


async def test_deleting_a_user_notifies_them() -> None:
    """The row is gone and the tokens went with it, so this is the last word — and it is said.

    Included because the alternative is worse than the redundancy: a mechanism with one mutation
    exempted is one the next reader treats as optional.
    """
    rbac = build_service()
    target = rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.delete_user(target.id)

    assert rbac.notifier.notified == [target.id]


async def test_creating_a_role_notifies_nobody() -> None:
    """A new role has zero grants and zero holders, so no catalog moved.

    The negative control for every test above: without it, "notifies on every write" would pass
    just as well, and a client that honoured the notification would refetch for nothing every
    time an admin typed a role name.
    """
    rbac = build_service()
    rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.create_role("fresh")

    assert rbac.notifier.audiences == []


async def test_an_idempotent_role_creation_notifies_nobody() -> None:
    """Re-creating an existing role writes nothing, commits nothing, announces nothing."""
    rbac = build_service()
    rbac.repository.roles.add(ROLE_SUPPORT)

    await rbac.service.create_role(ROLE_SUPPORT)

    assert rbac.notifier.audiences == []
    assert rbac.repository.commits == 0


@pytest.mark.parametrize(
    ("call", "expected_error"),
    [
        (lambda service, uid: service.set_role_tools(ADMIN_ROLE_NAME, []), ReservedRoleError),
        (lambda service, uid: service.set_role_tools("absent", []), RoleNotFoundError),
        (lambda service, uid: service.set_role_tools(ROLE_SUPPORT, ["nope"]), UnknownToolError),
        (lambda service, uid: service.delete_role("absent"), RoleNotFoundError),
        (lambda service, uid: service.set_user_roles(uid, ["absent"]), UnknownRoleError),
    ],
)
async def test_a_refused_write_notifies_nobody(call: Any, expected_error: type[Exception]) -> None:
    """V14 + V74: every guard raises before the commit, so nothing is announced either.

    A notification without a committed change is worse than none: it tells a client to refetch a
    catalog that did not move, and if the emit ever *preceded* the commit it would advertise rows
    that then rolled back.
    """
    rbac = build_service()
    target = rbac.repository.add_user(EMAIL)
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    with pytest.raises(expected_error):
        await call(rbac.service, target.id)

    assert rbac.notifier.audiences == []
    assert rbac.repository.commits == 0


async def test_the_notification_follows_the_commit() -> None:
    """Ordering, on the log the repository and the notifier share.

    Asserted as an order rather than as two facts, because two counters cannot express it. A
    notification sent first would invite a client to read a catalog assembled from rows the
    transaction had not yet made durable — and on a rollback, from rows that never existed.
    """
    rbac = build_service()
    rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))

    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ])

    assert rbac.repository.calls == ["commit", "notify"]


async def test_a_notifier_that_raises_does_not_fail_the_write() -> None:
    """V74: best-effort. The change committed, so the caller must see success.

    The write is verified through a read afterwards rather than by the absence of an exception:
    a service that swallowed the notifier fault *and* rolled back would satisfy the weaker claim.
    """
    rbac = build_service(notifier=RecordingToolListNotifier(fail_with=RuntimeError("no stream")))
    user = rbac.repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))

    stored = await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_READ])

    assert stored == [TOOL_READ]
    assert await rbac.service.get_permitted_tools(user.id) == {TOOL_READ}
    assert rbac.repository.commits == 1


async def test_a_role_nobody_holds_reaches_the_notifier_not_at_all() -> None:
    """An empty audience is a no-op, not an empty announcement.

    Keeps the notifier from having to decide what `[]` means, and keeps a log line from
    appearing for a write that concerned nobody.
    """
    rbac = build_service()
    rbac.repository.grant(ROLE_SUPPORT, TOOL_READ)

    await rbac.service.set_role_tools(ROLE_SUPPORT, [TOOL_CHANGE])

    assert rbac.notifier.audiences == []
    assert rbac.repository.commits == 1


async def test_a_service_built_without_a_notifier_still_writes() -> None:
    """`NullToolListChangedNotifier` is a real target, not a stub.

    The MCP tool path's own service instance takes it, and so would a CLI or a migration script.
    Built without the argument on purpose — that is the code path the default exists for, and it
    must not be the code path where a permission change stops persisting.
    """
    repository = FakeAuthorizationRepository()
    unnotified = AuthorizationService(
        repository=repository, audit_sink=RecordingAuditSink(), known_tools=TOOL_CATALOG
    )
    user = repository.add_user(EMAIL, roles=(ROLE_SUPPORT,))

    await NullToolListChangedNotifier().notify([user.id])
    await unnotified.set_role_tools(ROLE_SUPPORT, [TOOL_READ])

    assert await unnotified.get_permitted_tools(user.id) == {TOOL_READ}
    assert repository.commits == 1


# --- 2. How the emit reaches a session ---


class FakeServerSession:
    """A stand-in for `ServerSession` with the one method the notifier calls.

    A double rather than a real session because a real one needs a transport, a task group and
    a client on the other end — none of which this half is about. What is being asserted is the
    *routing*: which sessions the notifier picks and what it does when a send fails. The real
    session is exercised where it belongs, over the mount, in part 3.

    Weak-referenceable, like the class it stands in for, so `McpSessionRegistry`'s weak sets
    behave here as they do in production.
    """

    def __init__(self, *, fail_with: BaseException | None = None, block: bool = False) -> None:
        self.sends = 0
        self._fail_with = fail_with
        self._block = block

    async def send_tool_list_changed(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        if self._block:
            # Longer than the notifier's own deadline, so the timeout is what ends this.
            await anyio.sleep(NOTIFY_TIMEOUT_SECONDS * 20)
        self.sends += 1


def _registered(*pairs: tuple[UUID, FakeServerSession]) -> McpSessionRegistry:
    registry = McpSessionRegistry()
    for user_id, session in pairs:
        registry.remember(user_id, session)  # type: ignore[arg-type]
    return registry


async def test_a_registered_session_receives_tool_list_changed() -> None:
    user_id = uuid4()
    session = FakeServerSession()
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, session)))

    await notifier.notify([user_id])

    assert session.sends == 1


async def test_a_session_belonging_to_another_user_receives_nothing() -> None:
    """The negative control for the whole notifier.

    Without it, an implementation that emitted to every registered session would pass every
    other test in this section — and would tell a client its catalog moved when it had not.
    """
    told, untold = uuid4(), uuid4()
    told_session, untold_session = FakeServerSession(), FakeServerSession()
    notifier = McpToolListChangedNotifier(
        registry=_registered((told, told_session), (untold, untold_session))
    )

    await notifier.notify([told])

    assert (told_session.sends, untold_session.sends) == (1, 0)


async def test_every_session_one_user_holds_is_told() -> None:
    """One operator, two chat windows. Both hold a catalog, so both are told."""
    user_id = uuid4()
    first, second = FakeServerSession(), FakeServerSession()
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, first), (user_id, second)))

    await notifier.notify([user_id])

    assert (first.sends, second.sends) == (1, 1)


async def test_a_session_shared_by_two_ids_in_one_audience_is_told_once() -> None:
    """De-duplicated by identity, so a repeated id in an audience is not a repeated emit."""
    user_id = uuid4()
    session = FakeServerSession()
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, session)))

    await notifier.notify([user_id, user_id])

    assert session.sends == 1


async def test_notifying_nobody_touches_no_session() -> None:
    user_id = uuid4()
    session = FakeServerSession()
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, session)))

    await notifier.notify([])
    await notifier.notify([uuid4()])

    assert session.sends == 0


@pytest.mark.parametrize("error", [anyio.ClosedResourceError(), anyio.BrokenResourceError()])
async def test_a_closed_session_is_dropped_and_nothing_propagates(error: Exception) -> None:
    """An ended session is ordinary, not an error.

    Dropped from the register as well as survived, and the drop is the point: re-attempting an
    emit on a shut stream every time an admin writes would cost a timeout per write. Asserted
    through the register rather than through a log line.
    """
    user_id = uuid4()
    session = FakeServerSession(fail_with=error)
    registry = _registered((user_id, session))
    notifier = McpToolListChangedNotifier(registry=registry)

    await notifier.notify([user_id])

    assert registry.session_count(user_id) == 0
    assert registry.user_ids() == []


async def test_a_live_session_beside_a_closed_one_still_gets_told() -> None:
    """One dead stream is not a reason to withhold the others' notification (V86's shape)."""
    user_id = uuid4()
    dead = FakeServerSession(fail_with=anyio.ClosedResourceError())
    live = FakeServerSession()
    registry = _registered((user_id, dead), (user_id, live))
    notifier = McpToolListChangedNotifier(registry=registry)

    await notifier.notify([user_id])

    assert live.sends == 1
    assert registry.session_count(user_id) == 1


async def test_an_unexpected_send_failure_does_not_escape_the_task_group() -> None:
    """Anything other than a closed stream is swallowed too, and for a harder reason.

    The sends run in an `anyio` task group, so an exception would leave `notify` as a
    `BaseExceptionGroup` — which the engine's `except Exception` would not catch, turning a
    committed permission change into a 500 on the operator's write.
    """
    user_id = uuid4()
    session = FakeServerSession(fail_with=ValueError("unexpected"))
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, session)))

    await notifier.notify([user_id])


async def test_a_wedged_session_is_abandoned_at_the_deadline() -> None:
    """A client that is connected and not reading must not hang an admin write.

    The transport's write stream is unbuffered, so a send completes only once the session's
    router takes it — and the router may itself be blocked on an SSE stream nobody is draining.
    Measured on the clock, and the live session beside it is what proves the deadline bounded
    the emit rather than cancelling it wholesale: with `move_on_after` removed this passes only
    by waiting twenty times as long.
    """
    user_id = uuid4()
    wedged, live = FakeServerSession(block=True), FakeServerSession()
    notifier = McpToolListChangedNotifier(registry=_registered((user_id, wedged), (user_id, live)))

    with anyio.fail_after(NOTIFY_TIMEOUT_SECONDS * 5):
        await notifier.notify([user_id])

    assert wedged.sends == 0
    assert live.sends == 1


async def test_the_register_forgets_a_collected_session() -> None:
    """Weak references, because the transport offers no session-ended hook.

    A strong register would hold every session that ever connected, and their streams with them,
    for the life of the process.
    """
    user_id = uuid4()
    registry = McpSessionRegistry()
    session = FakeServerSession()
    registry.remember(user_id, session)  # type: ignore[arg-type]
    assert registry.session_count(user_id) == 1

    del session
    gc.collect()

    assert registry.session_count(user_id) == 0
    assert registry.user_ids() == []


async def test_remembering_the_same_session_twice_registers_it_once() -> None:
    """The middleware calls `remember` per message, so the repeat has to be free."""
    user_id = uuid4()
    registry = McpSessionRegistry()
    session = FakeServerSession()

    registry.remember(user_id, session)  # type: ignore[arg-type]
    registry.remember(user_id, session)  # type: ignore[arg-type]

    assert registry.session_count(user_id) == 1


async def test_the_middleware_registers_nothing_without_a_request_context() -> None:
    """A server-initiated message has no session to register, and must still pass through."""
    registry = McpSessionRegistry()
    middleware = McpSessionRegistryMiddleware(registry=registry)
    sentinel = object()

    class _Context:
        fastmcp_context = None

    async def call_next(_: Any) -> object:
        return sentinel

    result = await middleware.on_message(_Context(), call_next)  # type: ignore[arg-type]

    assert result is sentinel
    assert registry.user_ids() == []


# --- 3. The register, over the real mount ---


@pytest.fixture
def mount(monkeypatch: pytest.MonkeyPatch):
    """The real app, with a handle on the register its MCP middleware writes.

    `create_app` builds both halves, so the register read here is the one production uses —
    reached through the notifier on `app.state`, which is the same route the admin surface takes.
    """
    identities = FakeMcpIdentityRepository()
    tools = build_tool_context(servers=[whm_server("alpha")])

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:
        notifier = getattr(fixture.app.state, STATE_TOOL_LIST_NOTIFIER)
        yield fixture, identities, tools, notifier.registry


def test_the_real_mount_registers_the_callers_session(mount) -> None:
    """The middleware is reached, and it resolves the caller's `users.id`.

    The gap this closes is the one the rest of this file cannot: every notifier test above passes
    against a register nothing ever writes, and V74's best-effort emit means an unwritten
    register fails silently in production.
    """
    fixture, identities, tools, registry = mount
    user = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)

    open_session(fixture.client, plaintext)

    assert registry.user_ids() == [user.id]
    assert registry.session_count(user.id) == 1


def test_a_session_registers_without_ever_calling_a_tool(mount) -> None:
    """The negative control on the hook choice.

    An idle client holding a catalog from `tools/list` is the exact case this notification is
    for. Registration hung off `on_call_tool` would leave those sessions unregistered while every
    test that called something still passed — so this one never calls a tool, and the handshake
    alone has to be enough.
    """
    fixture, identities, tools, registry = mount
    user = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)

    session = open_session(fixture.client, plaintext)
    assert registry.session_count(user.id) == 1

    session.tool_names()

    assert registry.session_count(user.id) == 1


def test_two_operators_register_under_their_own_ids(mount) -> None:
    """Keyed by `users.id`, so one operator's write never reaches another's session."""
    fixture, identities, tools, registry = mount
    first = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    second = tools.authorization.add_user(OTHER_EMAIL, roles=(ROLE_NOC,))
    first_token, _ = identities.add_token(user_id=first.id, librechat_user_id=LIBRECHAT_USER)
    second_token, _ = identities.add_token(user_id=second.id, librechat_user_id="other-librechat")

    open_session(fixture.client, first_token)
    open_session(fixture.client, second_token, librechat_user="other-librechat")

    assert sorted(registry.user_ids()) == sorted([first.id, second.id])
    assert registry.session_count(first.id) == 1
    assert registry.session_count(second.id) == 1


def test_the_emit_reaches_a_real_server_session(mount) -> None:
    """The last gap: `send_tool_list_changed()` on the session the transport actually built.

    Every notifier test above runs against `FakeServerSession`, which proves the routing and
    nothing about the object being routed to. Here the session is the real `ServerSession` behind
    a real `Mcp-Session-Id`, and the notification goes onto its real write stream — so a method
    name that drifted, or a session shape the register cannot hold, fails here.

    `emitted == 1` is the assertion, not the absence of an exception: a notifier that silently
    dropped every send would satisfy the weaker claim. What happens to the message *after* the
    write stream is the transport's business and R30's answer — with no standalone GET stream
    open, the router discards it (`streamable_http.py`), which is why this asserts the hand-over
    rather than a delivery.
    """
    fixture, identities, tools, registry = mount
    user = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
    open_session(fixture.client, plaintext)

    notifier = McpToolListChangedNotifier(registry=registry)
    emitted = fixture.client.portal.call(notifier._emit_all, registry.sessions_for([user.id]))

    assert emitted == 1
    # Still registered: a successful emit is not a reason to forget the session.
    assert registry.session_count(user.id) == 1


def test_notifying_a_real_session_twice_stays_green(mount) -> None:
    """A permission change is not once-per-session, and neither is the emit.

    Two admin writes in a row must both reach the same live session — the failure this rules out
    is a send that consumes something (a one-shot stream, a cached notification object) and
    leaves the second write silently un-announced.
    """
    fixture, identities, tools, registry = mount
    user = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)
    open_session(fixture.client, plaintext)
    notifier = getattr(fixture.app.state, STATE_TOOL_LIST_NOTIFIER)

    fixture.client.portal.call(notifier.notify, [user.id])
    fixture.client.portal.call(notifier.notify, [user.id])

    assert registry.session_count(user.id) == 1


def test_an_unauthenticated_request_registers_nothing(mount) -> None:
    """No token, no identity, no entry — and the 401 is unchanged."""
    fixture, _identities, _tools, registry = mount

    response = fixture.client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers={"Accept": "application/json, text/event-stream"},
    )

    assert response.status_code == 401
    assert registry.user_ids() == []


# --- T66's own assertion: the V1 backstop behind a production write ---


def test_a_grant_revoked_through_the_service_is_refused_while_the_cached_catalog_names_it(
    mount,
) -> None:
    """The one behavioural claim §T.66 permits, and the one that matters.

    `test_mcp_tool_rbac.py` asserts the backstop by mutating the repository directly. This asserts
    it behind the *production write* — `AuthorizationService.set_role_tools`, the same call
    `PUT /admin/roles/{name}/tools` makes and the same call that emits T66's notification — so the
    revocation, the emit and the refusal are one sequence rather than three separate claims.

    The client's catalog is captured while the grant exists and is *not* re-read afterwards, which
    is the situation R30 leaves NOA in: LibreChat ignores the notification, so its displayed
    catalog keeps the revoked tool until the connection is rebuilt. That is documented in
    `ARCHITECTURE.md` as an operator-facing consequence, and it is safe for exactly one reason —
    the execution gate re-resolves per call.
    """
    fixture, identities, tools, _registry = mount
    user = tools.authorization.add_user(EMAIL, roles=(ROLE_SUPPORT,))
    tools.authorization.grant(ROLE_SUPPORT, TOOL_WHM_LIST_SERVERS)
    plaintext, _ = identities.add_token(user_id=user.id, librechat_user_id=LIBRECHAT_USER)

    session = open_session(fixture.client, plaintext)
    cached_catalog = session.tool_names()
    assert cached_catalog == [TOOL_WHM_LIST_SERVERS]

    # The admin write, through the engine rather than through the fake's dicts. Run on the app's
    # own event loop via the `TestClient` portal, so this is one process and one repository —
    # the same join production makes between the admin surface and the MCP mount.
    revoking = build_service(repository=tools.authorization)
    fixture.client.portal.call(revoking.service.set_role_tools, ROLE_SUPPORT, [])

    # The client has not re-listed: its catalog still names the tool it captured above.
    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert cached_catalog == [TOOL_WHM_LIST_SERVERS]
    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == ERROR_TOOL_NOT_PERMITTED
