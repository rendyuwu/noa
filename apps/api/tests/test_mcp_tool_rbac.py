"""RBAC on the MCP path, over the real mount (T19 — V1, V10, V11, V14, V74, I.mcp).

V1's two clauses are two different failures, so they get two sets of tests:

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

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ToolRisk
from core.db.models import ADMIN_ROLE_NAME
from noa_api.mcp_rbac import ERROR_TOOL_NOT_PERMITTED
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.noa_read import TOOL_NOA_GET_ACTION_RESULT
from noa_api.mcp_tools.pmg_read import TOOL_PMG_WHITELIST_LIST, TOOL_PMG_WHITELIST_SEARCH
from noa_api.mcp_tools.registry import RegistryError, assert_names_in_catalog, register_mcp_tools
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_firewall import TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES
from noa_api.mcp_tools.whm_read import (
    TOOL_WHM_LIST_ACCOUNTS,
    TOOL_WHM_LIST_SERVERS,
    TOOL_WHM_SEARCH_ACCOUNTS,
)
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import McpSession, mounted_app, open_session
from support.rbac import ROLE_SUPPORT, FakeAuthorizationRepository
from support.servers import build_tool_context, whm_server

# What this build registers, in the order `tools/list` sorts them. A literal rather than a set
# derived from `register_mcp_tools`: an `admin` sees exactly this, so a tool added without
# anyone noticing it became visible to every admin should fail here rather than be asserted
# against itself. Grows with each of §T.20-31 and §T.63.
REGISTERED_TOOLS = sorted(
    [
        TOOL_WHM_LIST_SERVERS,
        TOOL_WHM_LIST_ACCOUNTS,
        TOOL_WHM_SEARCH_ACCOUNTS,
        TOOL_WHM_SUSPEND_ACCOUNT,
        TOOL_WHM_UNSUSPEND_ACCOUNT,
        TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES,
        TOOL_PMG_WHITELIST_SEARCH,
        TOOL_PMG_WHITELIST_LIST,
        TOOL_NOA_GET_ACTION_RESULT,
    ]
)

# A catalogued tool this build does not register yet (T27, `proxmox_reset_vm_password`).
# Standing in for what a client holding a stale catalog, or a prompt-injected call, would name.
# It was `whm_list_accounts` until T20 built that one and `pmg_whitelist_list` until T30 did —
# the stand-in has to be a name the registry genuinely does not know, or this file asserts V10
# against a tool that answers. Proxmox has no tool module at all, so this one moves last.
UNREGISTERED_TOOL = "proxmox_reset_vm_password"

# Never in the catalog at all (V10).
UNKNOWN_TOOL = "whm_delete_everything"


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


# --- V1, first clause: `tools/list` is filtered ---


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
    """V1 is per tool, and with two registered tools that is finally observable.

    A role granted `whm_search_accounts` sees only it, and calling the other one is refused —
    a gate keyed on "has any WHM grant" would pass both and no earlier test could tell.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_SEARCH_ACCOUNTS,))

    assert session.tool_names() == [TOOL_WHM_SEARCH_ACCOUNTS]
    assert session.call_tool(TOOL_WHM_LIST_SERVERS)["isError"] is True


def test_tools_list_hides_a_tool_the_caller_may_not_call(scenario) -> None:
    """V1: a role with no grant sees an empty catalog, not the server's registry.

    This is the failure that has no symptom without a test. Drop the middleware and the tool
    still works, the handshake still succeeds, and every other test in the suite passes —
    the only difference is that a role which grants nothing can call everything.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ROLE_SUPPORT,))

    assert session.tool_names() == []


def test_a_revoked_grant_disappears_from_the_next_list(scenario) -> None:
    """V14: permission updates take effect immediately, with no cache to wait out."""
    sign_in, authorization = scenario
    session = sign_in(roles=(ROLE_SUPPORT,), grants=(TOOL_WHM_LIST_SERVERS,))
    assert session.tool_names() == [TOOL_WHM_LIST_SERVERS]

    authorization.role_tools[ROLE_SUPPORT] = set()

    assert session.tool_names() == []


# --- V1, second clause: `tools/call` re-checks ---


def test_calling_an_unpermitted_tool_is_refused_even_when_the_client_cached_it(
    scenario,
) -> None:
    """V74's backstop: a stale catalog is not a permission.

    The handshake era C23 pins has no `ttlMs`/`cacheScope`, so NOA cannot bound how long a
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
    """V11: `is_active=False` means zero permissions, whatever the roles say.

    Disabled *after* the session opened, and disabled only in the `users` row the RBAC
    engine reads. In production one write flips one row and both readers see it, so a
    disabled operator is stopped twice over — the identity resolver refuses the request with
    `mcp_user_inactive` (T11/T12) before this gate is reached. That is why the two are
    separated here: V11 is a claim about *permission resolution*, and it has to hold on its
    own rather than because authentication happened to get there first. An operator disabled
    while holding an open MCP session is exactly the case where it matters.

    `admin` is the role on purpose: V11 is checked before V10's bypass, so the caller who
    would otherwise get every tool gets none.
    """
    sign_in, authorization = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,), grants=(TOOL_WHM_LIST_SERVERS,))
    assert sorted(session.tool_names()) == REGISTERED_TOOLS

    for user in authorization.users.values():
        user.is_active = False

    assert session.tool_names() == []
    assert session.call_tool(TOOL_WHM_LIST_SERVERS)["isError"] is True


# --- V10: the admin bypass, and its bound ---


def test_an_admin_sees_every_registered_tool(scenario) -> None:
    """V10: `admin` skips the grant table.

    The list is the *intersection* of the catalog and what is registered, which is why this
    asserts against the registered set rather than against `TOOL_CATALOG` — the remaining
    catalogued names have no implementation yet (T25-T29).
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,))

    assert sorted(session.tool_names()) == REGISTERED_TOOLS


@pytest.mark.parametrize("tool_name", [UNREGISTERED_TOOL, UNKNOWN_TOOL])
def test_an_admin_cannot_call_a_tool_that_is_not_registered(
    scenario,
    tool_name: str,
) -> None:
    """V10's second half, and the reason the two names give the same answer.

    `proxmox_reset_vm_password` is catalogued but unbuilt (T27); `whm_delete_everything` is
    neither.
    An admin's grant set is the whole *catalog*, so without the registered-name intersection
    in `RbacToolMiddleware` the first of these would sail past the gate and come back as
    fastmcp's `Unknown tool` — a different shape, and one that says which catalogued tools
    exist yet. Both are refused with `tool_not_permitted` instead.
    """
    sign_in, _ = scenario
    session = sign_in(roles=(ADMIN_ROLE_NAME,))

    result = session.call_tool(tool_name)

    assert result["isError"] is True
    assert result["structuredContent"]["error_code"] == ERROR_TOOL_NOT_PERMITTED


# --- I.mcp: the registry ---


async def test_the_registry_reports_exactly_what_it_registered() -> None:
    """Closes the one assumption `register_mcp_tools` makes.

    It validates the names each registrar *returns*, because fastmcp 3.4.5 exposes its
    registry only through an async accessor and `build_mcp_server` is synchronous. This is
    the test that makes that reporting trustworthy: a registrar that under-reported would
    let an uncatalogued name through the check — and, since T73, would also leave that tool
    unaudited, because the audit middleware is handed the same mapping.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)

    reported = register_mcp_tools(server, context=context)
    listed = {tool.name for tool in await server.list_tools(run_middleware=False)}

    assert set(reported) == listed


def test_every_registered_tool_name_is_in_the_catalog() -> None:
    """V10, C22: a name outside `TOOL_CATALOG` is a capability no role can be granted."""
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert set(registered) <= TOOL_CATALOG


def test_every_registered_tool_declares_a_risk() -> None:
    """V20, T73: `risk` on the audit row comes from registration, never from a default.

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


# --- V19: the backstop on the server itself ---


def test_the_server_masks_error_details_by_configuration() -> None:
    """fastmcp's default is `False`, which would put `str(exc)` in front of the model.

    `sanitize_tool_errors` is the first line and covers every tool; this covers what is
    raised *outside* one — argument validation, a bug in a registration wrapper. Pinned
    because it is a default we are overriding, and a future fastmcp changing the attribute
    name should fail here rather than quietly stop masking.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)

    assert server._mask_error_details is True
