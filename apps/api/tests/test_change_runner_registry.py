"""A CHANGE tool with nothing able to run it is a startup failure (T38 — V10, V46).

A CHANGE tool has two halves that live in different modules and run at different moments: the
tool opens an approval request (T33), and a `ChangeRunner` performs the change once an operator
approves (`noa_api.mcp_tools.change_runners`). Register the first without the second and the
failure is invisible until an operator has typed a reason and pressed Approve — at which point
the change answers `change_runner_unavailable` and does not run.

So the check happens where both facts meet, beside `assert_names_in_catalog`, which refuses an
uncatalogued name for the same reason (`noa_api.mcp_tools.registry`).

**The guard was written before its first instance and is not vacuous any more.** T22 registered
`whm_suspend_account` with the runner that performs it, T23 `whm_unsuspend_account` with its own,
T25 `whm_firewall_release_and_allow` from a second module and T26 `whm_firewall_allowlist_remove`
from a third — which is the case the equality below is actually for, since a registrar and a
runner-builder that live in different packages are where the two halves can drift apart. T27
added `proxmox_reset_vm_password`, the first from a system other than WHM, and T28
`proxmox_vm_nic`, the first *second* CHANGE tool on one system — which is where a runner map
keyed off the integration rather than the tool name would collide. T29 closed the set with
`pmg_whitelist`, the third system and the last exposed CHANGE name. The probe that registers a
CHANGE tool with no runner stays, because a predicate that is now satisfied by every name still
has to separate (V87) — and it is what the *next* CHANGE tool will meet before an operator does.
"""

from __future__ import annotations

import pytest

from core.db.lifecycle import ToolRisk
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools import registry
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.pmg_whitelist import TOOL_PMG_WHITELIST
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.registry import (
    RegistryError,
    assert_change_runners_cover,
    register_mcp_tools,
)
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_firewall_allowlist import TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE
from noa_api.mcp_tools.whm_firewall_change import TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW
from support.servers import build_tool_context


async def noop_runner(request: object) -> dict[str, object]:
    """A runner that would be registered by T22-T29. Never called here."""
    return {"ok": True}


def test_every_change_tool_is_registered_with_its_runner() -> None:
    """T22 is the guard's first real instance; before it, this file asserted the emptiness.

    Two halves that live in different modules, asserted to name the same tools: a runner
    registered under a name nothing exposes is as invisible as a tool with no runner, and
    `assert_change_runners_cover` only catches the second.

    An equality on both sets rather than a membership test, so T23's second pair had to be added
    here to stay green — and so the next one does too (V66: one builder, both directions). T25 is
    the first name whose two halves come from different modules, which is what the second
    assertion is worth having for; T26 is the first whose registrar and runner-builder are
    reached from two different aggregators, which is the same drift one level up; T27 is the
    first from a system other than WHM, so a runner map keyed off the wrong integration would
    show here rather than at an approval; T28 is the first system to contribute a *second* CHANGE
    tool, which is where a runner map returning one entry per module rather than per tool name
    would silently drop one; T29 is the third system and the name that completes the set.
    """
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    changes = {name for name, risk in registered.items() if risk is ToolRisk.CHANGE}
    assert changes == {
        TOOL_WHM_SUSPEND_ACCOUNT,
        TOOL_WHM_UNSUSPEND_ACCOUNT,
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        TOOL_PROXMOX_RESET_VM_PASSWORD,
        TOOL_PROXMOX_VM_NIC,
        TOOL_PMG_WHITELIST,
    }
    assert set(build_change_runners(context=context)) == changes


def test_a_change_tool_without_a_runner_fails_at_startup() -> None:
    """The probe (V87): the predicate separates, it does not merely never fire.

    A registered CHANGE name with no runner is exactly the arrangement an operator would meet as
    a card they can approve and a change NOA cannot run.
    """
    with pytest.raises(RegistryError, match="no post-approval runner"):
        assert_change_runners_cover({"whm_suspend_account": ToolRisk.CHANGE}, {})


def test_a_change_tool_with_a_runner_passes() -> None:
    """The other half of the probe: covered is covered."""
    assert_change_runners_cover(
        {"whm_suspend_account": ToolRisk.CHANGE},
        {"whm_suspend_account": noop_runner},
    )


def test_a_read_tool_needs_no_runner() -> None:
    """A READ executes inside its own `tools/call` (V16) — there is nothing to run afterwards.

    Without this branch the guard would demand a runner for every tool NOA exposes, which would
    make it fire on the twelve READs already registered.
    """
    assert_change_runners_cover({"whm_list_servers": ToolRisk.READ}, {})


def test_the_message_names_every_uncovered_change_tool() -> None:
    """An operator-facing failure would name one; this is a startup failure read by whoever is
    wiring the tools, so it names all of them and the second is not discovered by fixing the
    first and restarting."""
    with pytest.raises(RegistryError) as raised:
        assert_change_runners_cover(
            {
                "whm_suspend_account": ToolRisk.CHANGE,
                "whm_unsuspend_account": ToolRisk.CHANGE,
                "whm_list_servers": ToolRisk.READ,
            },
            {},
        )

    message = str(raised.value)
    assert "whm_suspend_account" in message
    assert "whm_unsuspend_account" in message
    assert "whm_list_servers" not in message


def test_the_real_registry_passes_the_guard() -> None:
    """`register_mcp_tools` runs this on every app build, so `create_app` would raise.

    Asserted directly as well, because the mount tests would report the same failure as "the app
    could not be built" and that names nothing.
    """
    context = build_tool_context().context
    registered = register_mcp_tools(build_mcp_server(tool_context=context), context=context)

    assert_change_runners_cover(registered, build_change_runners(context=context))
    # And the guard is not passing because there is nothing to cover (V87): T22 registered a
    # CHANGE tool, so this call has something to be right about.
    assert ToolRisk.CHANGE in set(registered.values())


def test_registering_a_change_tool_without_a_runner_raises_from_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard's *call site*, not just the guard (found by mutation, V87).

    `test_a_change_tool_without_a_runner_fails_at_startup` calls `assert_change_runners_cover`
    directly, so deleting the call from `register_mcp_tools` left it — and every other test here
    — green while the app happily exposed an unrunnable CHANGE tool.

    A registrar is wrapped so one already-catalogued tool reports `CHANGE` instead of `READ`.
    Nothing else changes: the tool is still registered under a name `TOOL_CATALOG` knows, so this
    reaches the runner check rather than tripping the catalog one on the way.
    """
    context = build_tool_context().context
    real = registry.register_noa_read_tools

    def as_change(server: object, *, context: object) -> dict[str, ToolRisk]:
        return dict.fromkeys(real(server, context=context), ToolRisk.CHANGE)  # type: ignore[arg-type]

    monkeypatch.setattr(registry, "register_noa_read_tools", as_change)

    with pytest.raises(RegistryError, match="no post-approval runner"):
        register_mcp_tools(build_mcp_server(tool_context=context), context=context)
