"""A CHANGE tool with nothing able to run it is a startup failure (T38 — V10, V46).

A CHANGE tool has two halves that live in different modules and run at different moments: the
tool opens an approval request (T33), and a `ChangeRunner` performs the change once an operator
approves (`noa_api.mcp_tools.change_runners`). Register the first without the second and the
failure is invisible until an operator has typed a reason and pressed Approve — at which point
the change answers `change_runner_unavailable` and does not run.

So the check happens where both facts meet, beside `assert_names_in_catalog`, which refuses an
uncatalogued name for the same reason (`noa_api.mcp_tools.registry`).

**The guard is vacuous today and the tests are not.** T22-T29 are unbuilt, so there are no
CHANGE tools to cover and the predicate never fires in production. Written now anyway — a rule
has to exist before its first instance, or the first instance is what discovers it (V85's
discipline, T33(d)'s vacuous registry sweep) — and with a probe that proves the predicate
separates rather than merely never firing (V87).
"""

from __future__ import annotations

import pytest

from core.db.lifecycle import ToolRisk
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools import registry
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.registry import (
    RegistryError,
    assert_change_runners_cover,
    register_mcp_tools,
)
from support.servers import build_tool_context


async def noop_runner(request: object) -> dict[str, object]:
    """A runner that would be registered by T22-T29. Never called here."""
    return {"ok": True}


def test_no_change_tool_is_registered_yet() -> None:
    """The premise every other test in this file rests on, asserted rather than assumed.

    When T22 lands this goes red, which is the point: the file that says "the guard is vacuous"
    has to stop saying it the moment it is not.
    """
    context = build_tool_context().context

    assert build_change_runners(context=context) == {}


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
    assert ToolRisk.CHANGE not in set(registered.values())


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
