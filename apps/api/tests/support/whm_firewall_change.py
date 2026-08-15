"""Calling T25's two halves, and the box a runner test needs (T25).

`support/whm_firewall.py` owns the WHM box itself — the command answers, the availability
probes, the transport double. This owns what a *test* needs to drive
`whm_firewall_release_and_allow`: the call helpers for the tool and the runner, and the fixture
shapes both lanes share.

Its own module because those lanes are two test files (`test_whm_tools_firewall_release_and_allow`
and `test_whm_firewall_release_runner`), split so neither runs past C14's line budget, and
helpers duplicated across two files are two helpers that drift (V66).

The `ChangeExecutionRequest` here is assembled the way `core.approvals.execution` assembles one,
because that is what the executor hands a runner — and its `arguments` deliberately name a
different server from its evidence, so every runner test that resolves a target is also a V33
assertion.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from core.approvals.execution import ChangeExecutionRequest
from core.integrations.whm.csf_cli import CSF_BINARY
from noa_api.mcp_tools.whm_firewall_change import (
    EVIDENCE_DURATION_MINUTES,
    EVIDENCE_FIREWALL,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    EVIDENCE_TARGET,
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    whm_firewall_release_and_allow,
)
from support.action_decisions import REASON
from support.mcp_identity import authenticated_caller, http_request_context
from support.servers import ToolFixture
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_DENY_LINE,
    IMUNIFY_WHITE,
    SERVER_NAME,
    TARGET,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    firewall_context,
    imunify_answer,
    imunify_backend,
    is_probe,
    working_box,
)

# Two hours, in the minutes the schema takes. Not a round hour and not a bound, so an assertion
# on it separates a tool that carried the operator's number from one that fell back to anything.
DURATION_MINUTES = 137


def release_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    box: FakeFirewallBox | None = None,
    **kwargs: Any,
) -> tuple[ToolFixture, Any]:
    """A tool context whose WHM server is reachable only through one recorded `ssh_exec`."""
    return firewall_context(monkeypatch, firewall=box or working_box(), **kwargs)


async def release(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    target: str = TARGET,
    duration_minutes: int = DURATION_MINUTES,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The context is not decoration: `open_change_request` reads the requester from the
    authenticated identity rather than from an argument (V23, V27), so a call outside it would be
    asserting against an identity the test planted.
    """
    user, resolved = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await whm_firewall_release_and_allow(
            server_ref=server_ref,
            target=target,
            duration_minutes=duration_minutes,
            context=fixture.context,
        )
    return answer, resolved


def execution_request(
    *,
    server_id: UUID | str,
    target: str = TARGET,
    duration_minutes: Any = DURATION_MINUTES,
    reason: str = REASON,
    action_request_id: UUID | None = None,
    server_ref: str = SERVER_NAME,
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands a runner for an approved release.

    The evidence is what the gate wrote and what the operator saw; the arguments deliberately
    name a `server_ref` the runner must ignore (V33).
    """
    return ChangeExecutionRequest(
        action_request_id=action_request_id or uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
        arguments={
            "server_ref": server_ref,
            "target": target,
            "duration_minutes": duration_minutes,
        },
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_TARGET: target,
            EVIDENCE_DURATION_MINUTES: duration_minutes,
            EVIDENCE_FIREWALL: {"combined_verdict": "blocked", "matches": [CSF_DENY_LINE]},
        },
        reason=reason,
    )


def changes(fake: Any) -> list[str]:
    """Every command that was not an availability probe, in the order it was sent."""
    return [command for command in fake.commands if not is_probe(command)]


def csf_commands(fake: Any) -> list[str]:
    return [command for command in changes(fake) if CSF_BINARY in command]


def imunify_commands(fake: Any) -> list[str]:
    return [command for command in changes(fake) if CSF_BINARY not in command]


def released_box(
    *, csf_after: str = CSF_ALLOW_LINE, imunify_after: str = IMUNIFY_WHITE
) -> FakeFirewallBox:
    """A box for the **runner** lane, whose one read is the confirming one.

    The runner reads exactly once — the tool took the before-state, and the whole point of the
    runner's read is that it happens after the change. So a one-entry queue here holds the
    *after* state; the queue mechanism is what an end-to-end test that drove both halves would
    need, and putting the before-state in front of it in a runner-only test would silently give
    the postflight the operator's own reading back.
    """
    return FakeFirewallBox(
        csf=csf_backend(csf_answer(csf_after)),
        imunify=imunify_backend(imunify_answer(imunify_after)),
    )


__all__ = [
    "DURATION_MINUTES",
    "changes",
    "csf_commands",
    "execution_request",
    "imunify_commands",
    "release",
    "release_context",
    "released_box",
]
