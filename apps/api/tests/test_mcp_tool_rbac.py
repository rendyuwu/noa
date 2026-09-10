"""RBAC on the MCP path, over the real mount.

The permission check has two clauses — two different failures — so they get two sets of tests:

    `tools/list` RBAC-filtered per user. `tools/call` re-checks permission.

Everything here runs against `create_app()` through `TestClient` — a real bearer, a real
`initialize` handshake, a real `Mcp-Session-Id`, the real verifier, the real
`RbacToolMiddleware`, the real `AuthorizationService`. Only the SQL and the directory are
doubled (`support.mcp_mount`, `support.servers`). That matters more here than anywhere else
in the suite: the gate reads the caller's identity from `get_access_token()`, which is
populated by the authentication middleware from the ASGI scope, so a test that called the
middleware directly would be asserting against an identity it planted itself.

The two identity doubles are joined by `user_id`: `FakeMcpIdentityRepository.add_token`
mints the credential and `FakeAuthorizationRepository.add_user` holds the roles, and both
are given the same id — the same join production makes through `users.id`.
"""

from __future__ import annotations

import pytest
from fastmcp.tools import ToolResult
from mcp import types as mt

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ToolRisk
from core.db.models import ADMIN_ROLE_NAME
from noa_api.mcp_rbac import (
    ERROR_TOOL_NOT_PERMITTED,
    MESSAGE_TOOL_NOT_PERMITTED,
    RbacToolMiddleware,
)
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.noa_read import TOOL_NOA_GET_ACTION_RESULT
from noa_api.mcp_tools.pmg_read import TOOL_PMG_WHITELIST_LIST, TOOL_PMG_WHITELIST_SEARCH
from noa_api.mcp_tools.pmg_whitelist import TOOL_PMG_WHITELIST
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.registry import RegistryError, assert_names_in_catalog, register_mcp_tools
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_firewall import TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES
from noa_api.mcp_tools.whm_firewall_allowlist import TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE
from noa_api.mcp_tools.whm_firewall_change import TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW
from noa_api.mcp_tools.whm_read import (
    TOOL_WHM_LIST_ACCOUNTS,
    TOOL_WHM_LIST_SERVERS,
    TOOL_WHM_SEARCH_ACCOUNTS,
)
from support.mcp_identity import (
    LIBRECHAT_USER,
    FakeMcpIdentityRepository,
    authenticated_caller,
    http_request_context,
)
from support.mcp_mount import McpSession, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import build_tool_context, whm_server

# What this build registers, in the order `tools/list` sorts them. A literal rather than a set
# derived from `register_mcp_tools`: an `admin` sees exactly this, so a tool added without
# anyone noticing it became visible to every admin should fail here rather than be asserted
# against itself. Grew with each tool registered since, and the pmg-whitelist tool closed it:
# this is now the whole of `TOOL_CATALOG`, which is asserted below rather than left as a
# coincidence.
REGISTERED_TOOLS = sorted(
    [
        TOOL_WHM_LIST_SERVERS,
        TOOL_WHM_LIST_ACCOUNTS,
        TOOL_WHM_SEARCH_ACCOUNTS,
        TOOL_WHM_SUSPEND_ACCOUNT,
        TOOL_WHM_UNSUSPEND_ACCOUNT,
        TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES,
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        TOOL_PROXMOX_RESET_VM_PASSWORD,
        TOOL_PROXMOX_VM_NIC,
        TOOL_PMG_WHITELIST_SEARCH,
        TOOL_PMG_WHITELIST_LIST,
        TOOL_PMG_WHITELIST,
        TOOL_NOA_GET_ACTION_RESULT,
    ]
)

# **There is no catalogued-but-unregistered name left.** The pmg-whitelist tool registered
# `pmg_whitelist`, the last one, so the stand-in this file carried through `whm_list_accounts`,
# `proxmox_reset_vm_password`, `proxmox_vm_nic` and `pmg_whitelist_list` has nothing to point
# at any more. Its assertions are not simply deleted, which would leave the gate's own
# registered-set intersection covered by nothing: they move to a probe that
# builds the middleware with a registered set of its own
# (`test_a_catalogued_tool_this_server_did_not_register_is_refused_in_noas_shape`). A predicate
# that no longer separates against production is one to keep separating against a fixture, not
# one to drop (the compare-must-still-separate rule, and `test_change_runner_registry`'s probe
# one guard over).

# Never in the catalog at all.
UNKNOWN_TOOL = "whm_delete_everything"


class _CallToolProbe:
    """The one field `RbacToolMiddleware.on_call_tool` reads off a `MiddlewareContext`.

    A stand-in rather than a real context because building one means building a fastmcp request
    the gate never looks at: it reads `context.message.name` and hands the context to
    `call_next`, which the probe below never lets run. Everything else in that path — the
    identity, the authorization service, the intersection — is production.
    """

    def __init__(self, tool_name: str) -> None:
        self.message = mt.CallToolRequestParams(name=tool_name, arguments={})


@pytest.fixture
def scenario(monkeypatch: pytest.MonkeyPatch):
    """A mounted app plus a helper that signs a caller in with given roles and grants."""
    identities = FakeMcpIdentityRepository()
    authorization = FakeAuthorizationRepository()
    tools = build_tool_context(
        servers=[whm_server("alpha")],
        authorization=authorization,
    )

    with mounted_app(monkeypatch, repository=identities, tool_context=tools.context) as fixture:

        def sign_in(
            *,
            roles: tuple[str, ...] = (),
            grants: tuple[str, ...] = (),
            is_active: bool = True,
        ) -> McpSession:
            user = authorization.add_user("operator@example.com", is_active=is_active, roles=roles)
            for role in roles:
                if grants:
                    authorization.grant(role, *grants)
            plaintext, _ = identities.add_token(
                user_id=user.id, librechat_user_id=LIBRECHAT_USER, is_active=is_active
            )
            return open_session(fixture.client, plaintext)

        yield sign_in, authorization


# --- Permission check, first clause: `tools/list` is filtered ---


def test_a_granted_tool_is_listed_and_callable(scenario) -> None:
    """The baseline: a role that grants the tool can see it and run it.

    Asserted first because every refusal below is only meaningful if the permitted path
    works — a gate that refused everyone would pass all the negative tests.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_LIST_SERVERS,))

    assert session.tool_names() == [TOOL_WHM_LIST_SERVERS]

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)
    assert result.get("isError") is not True
    assert result["structuredContent"]["ok"] is True
    assert [server["name"] for server in result["structuredContent"]["servers"]] == ["alpha"]


def test_a_grant_names_one_tool_and_not_the_toolset(scenario) -> None:
    """The permission check is per tool, and with two registered tools that is finally observable.

    A role granted `whm_search_accounts` sees only it, and calling the other one is refused —
    a gate keyed on "has any WHM grant" would pass both and no earlier test could tell.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_SEARCH_ACCOUNTS,))

    assert session.tool_names() == [TOOL_WHM_SEARCH_ACCOUNTS]
    assert session.call_tool(TOOL_WHM_LIST_SERVERS)["isError"] is True


def test_tools_list_hides_a_tool_the_caller_may_not_call(scenario) -> None:
    """A role with no grant sees an empty catalog, not the server's registry.

    This is the failure that has no symptom without a test. Drop the middleware and the tool
    still works, the handshake still succeeds, and every other test in the suite passes —
    the only difference is that a role which grants nothing can call everything.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,))

    assert session.tool_names() == []


def test_a_revoked_grant_disappears_from_the_next_list(scenario) -> None:
    """Permission updates take effect immediately, with no cache to wait out."""
    sign_in, authorization = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_LIST_SERVERS,))
    assert session.tool_names() == [TOOL_WHM_LIST_SERVERS]

    authorization.role_tools[ROLE_SUPPORT] = set()

    assert session.tool_names() == []


# --- Permission check, second clause: `tools/call` re-checks ---


def test_calling_an_unpermitted_tool_is_refused_even_when_the_client_cached_it(
    scenario,
) -> None:
    """A stale catalog is not a permission — the execution-time RBAC re-check backstops it.

    The negotiated handshake era has no `ttlMs`/`cacheScope`, so NOA cannot bound how long a
    client displays a revoked tool. What it can do — and this is the whole reason the
    execution check exists separately from the filter — is refuse the call.
    """
    sign_in, authorization = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_LIST_SERVERS,))
    session.tool_names()  # the client's catalog, captured while the grant existed

    authorization.role_tools[ROLE_SUPPORT] = set()
    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["isError"] is True
    assert result["structuredContent"] == {
        "ok": False,
        "error_code": ERROR_TOOL_NOT_PERMITTED,
        "message": result["structuredContent"]["message"],
    }


def test_the_refused_call_never_reaches_the_tool(scenario) -> None:
    """A denial is a refusal to run, not a run whose output was discarded.

    Asserted on the repository read counter: if the gate ran after the tool, the inventory
    would have been read and the refusal would be cosmetic.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,))

    result = session.call_tool(TOOL_WHM_LIST_SERVERS)

    assert result["isError"] is True
    assert "servers" not in result["structuredContent"]


def test_a_disabled_user_sees_no_tools_and_may_call_none(scenario) -> None:
    """`is_active=False` means zero permissions, whatever the roles say.

    Disabled *after* the session opened, and disabled only in the `users` row the RBAC
    engine reads. In production one write flips one row and both readers see it, so a
    disabled operator is stopped twice over — the identity resolver refuses the request with
    `mcp_user_inactive` before this gate is reached. That is why the two are
    separated here: a disabled account having zero permissions is a claim about *permission
    resolution*, and it has to hold on its own rather than because authentication happened to
    get there first. An operator disabled while holding an open MCP session is exactly the
    case where it matters.

    `admin` is the role on purpose: the disabled-zeroes-permissions rule is checked before the
    admin bypass, so the caller who would otherwise get every tool gets none.
    """
    sign_in, authorization = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,), grants=(TOOL_WHM_LIST_SERVERS,))
    assert sorted(session.tool_names()) == REGISTERED_TOOLS

    for user in authorization.users.values():
        user.is_active = False

    assert session.tool_names() == []
    assert session.call_tool(TOOL_WHM_LIST_SERVERS)["isError"] is True


# --- The admin bypass, and its bound ---


def test_an_admin_sees_every_registered_tool(scenario) -> None:
    """`admin` skips the grant table.

    The list is the *intersection* of the catalog and what is registered, which is why this
    asserts against the registered set rather than against `TOOL_CATALOG` — the remaining
    catalogued names have no implementation yet.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,))

    assert sorted(session.tool_names()) == REGISTERED_TOOLS


def test_an_admin_cannot_call_a_tool_the_catalog_does_not_know(scenario) -> None:
    """The admin bypass's second half: an admin bypasses the grant table, not existence.

    `whm_delete_everything` is in no catalog and no registry, and it comes back as
    `tool_not_permitted` — the same code a missing grant gets, so the refusal is not an oracle
    over which names exist.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,))

    result = session.call_tool(UNKNOWN_TOOL)

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == ERROR_TOOL_NOT_PERMITTED


async def test_a_catalogued_tool_this_server_did_not_register_is_refused_in_noas_shape() -> None:
    """The gate's registered-set probe: the gate carries the registered set and intersects
    with it.

    An `admin`'s grant set is the whole *catalog*, so a catalogued name this build did not
    register would sail past the permission check and reach fastmcp's own `Unknown tool` — a
    different shape, and one that answers which catalogued tools are built yet. The intersection
    in `RbacToolMiddleware._permitted_tools` is what stops that.

    Driven against the middleware directly rather than the mount, because as of the pmg-whitelist
    tool the registry and the catalog are the same fourteen names and production has no such gap
    left. Handing this one an empty registered set is how the branch keeps separating — the same
    argument `test_change_runner_registry`'s no-runner probe makes for a guard that now always
    passes (the compare-must-still-separate rule). The identity still comes from
    `get_access_token()`'s own scope key, so what is
    doubled here is the registered set and nothing about how the caller was resolved.

    `call_next` raises: the point is that the tool is never dispatched, not that its answer was
    discarded.
    """
    authorization = FakeAuthorizationRepository()
    user = authorization.add_user("operator@example.com", roles=(ADMIN_ROLE_NAME,))
    tools = build_tool_context(authorization=authorization)
    gate = RbacToolMiddleware(context=tools.context, registered_tools=frozenset())
    caller, _ = authenticated_caller(user_id=user.id)

    async def call_next(_: object) -> ToolResult:
        raise AssertionError("the gate dispatched a tool this server never registered")

    with http_request_context({}, user=caller):
        result = await gate.on_call_tool(
            _CallToolProbe(TOOL_WHM_LIST_SERVERS),  # type: ignore[arg-type]
            call_next,  # type: ignore[arg-type]
        )

    assert result.is_error is True
    assert result.structured_content == {
        "ok": False,
        "error_code": ERROR_TOOL_NOT_PERMITTED,
        "message": MESSAGE_TOOL_NOT_PERMITTED,
    }


# --- I.mcp: the registry ---


async def test_the_registry_reports_exactly_what_it_registered() -> None:
    """Closes the one assumption `register_mcp_tools` makes.

    It validates the names each registrar *returns*, because fastmcp 3.4.5 exposes its
    registry only through an async accessor and `build_mcp_server` is synchronous. This is
    the test that makes that reporting trustworthy: a registrar that under-reported would
    let an uncatalogued name through the check — and, since the tool-run writer wired in, would
    also leave that tool unaudited, because the audit middleware is handed the same mapping.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)

    reported = register_mcp_tools(server, context=context)
    listed = {tool.name for tool in await server.list_tools(run_middleware=False)}

    assert set(reported) == listed


def test_the_registered_surface_is_the_whole_catalog() -> None:
    """The MCP server's fourteen tools, and the reason this file no longer carries a stand-in.

    An equality, and it is meant to be brittle in one direction: a catalogued name added
    without a registrar puts the gap back, and the thing that goes with a gap is the
    catalogued-but-unregistered assertion this file used to make against production. Failing
    here is how that gets noticed, rather than by an `admin` meeting fastmcp's `Unknown tool`.

    The subset check below stays: `⊆` is the invariant, and this is a fact about
    today's surface.
    """
    assert sorted(TOOL_CATALOG) == REGISTERED_TOOLS


def test_every_registered_tool_name_is_in_the_catalog() -> None:
    """A name outside `TOOL_CATALOG` is a capability no role can be granted."""
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert set(registered) <= TOOL_CATALOG


def test_every_registered_tool_declares_a_risk() -> None:
    """`risk` on the audit row comes from registration, never from a default.

    A registrar that returned a bare name would make the tool unclassifiable, and the audit
    middleware's answer to "is this a READ?" would have to be a guess. Asserted over the
    whole mapping so a tool added later inherits the requirement.
    """
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert registered
    assert all(isinstance(risk, ToolRisk) for risk in registered.values())


def test_an_uncatalogued_name_fails_at_construction() -> None:
    """The guard is a raise, not an `assert` — `python -O` strips asserts."""
    with pytest.raises(RegistryError, match=UNKNOWN_TOOL):
        assert_names_in_catalog(frozenset({TOOL_WHM_LIST_SERVERS, UNKNOWN_TOOL}))


# --- Sanitizing exceptions: the backstop on the server itself ---


def test_the_server_masks_error_details_by_configuration() -> None:
    """fastmcp's default is `False`, which would put `str(exc)` in front of the model.

    `sanitize_tool_errors` is the first line and covers every tool; this covers what is
    raised *outside* one — argument validation, a bug in a registration wrapper. Pinned
    because it is a default we are overriding, and a future fastmcp changing the attribute
    name should fail here rather than quietly stop masking.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)

    assert server._mask_error_details is True
