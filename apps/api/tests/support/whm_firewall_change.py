"""Calling the firewall CHANGE tools' two halves, and the box a runner test needs.

`support/whm_firewall.py` owns the WHM box itself — the command answers, the availability
probes, the transport double. This owns what a *test* needs to drive
`whm_firewall_release_and_allow` and `whm_firewall_allowlist_remove`: the call
helpers for each tool and each runner, and the fixture shapes all four lanes share.

Its own module because those lanes are four test files, split so none runs past C14's line
budget, and helpers duplicated across files are helpers that drift.

The `ChangeExecutionRequest`s here are assembled the way `core.approvals.execution` assembles
one, because that is what the executor hands a runner — and their `arguments` deliberately name
a different server from their evidence, so every runner test that resolves a target is also a
V33 assertion.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from core.approvals.execution import ChangeExecutionRequest
from core.integrations.whm.csf_cli import CSF_BINARY
from noa_api.mcp_tools.whm_firewall_allowlist import (
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    whm_firewall_allowlist_remove,
)
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
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    IMUNIFY_CLEAN,
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
    authenticated identity rather than from an argument, so a call outside it would be
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
    name a `server_ref` the runner must ignore.
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


# --- T26: `whm_firewall_allowlist_remove` ---


async def allowlist_remove(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    target: str = TARGET,
) -> tuple[Any, UUID]:
    """Call T26's tool inside a real request context; return its answer and the caller's id.

    The context is not decoration, for `release`'s reason: `open_change_request` reads the
    requester from the authenticated identity rather than from an argument.
    """
    user, resolved = authenticated_caller()
    with http_request_context({}, user=user):
        answer = await whm_firewall_allowlist_remove(
            server_ref=server_ref,
            target=target,
            context=fixture.context,
        )
    return answer, resolved


def removal_request(
    *,
    server_id: UUID | str,
    target: str = TARGET,
    reason: str = REASON,
    action_request_id: UUID | None = None,
    server_ref: str = SERVER_NAME,
) -> ChangeExecutionRequest:
    """What `core.approvals.execution` hands T26's runner for an approved removal.

    The before-state on the evidence is an *allowlisted* reading, because that is the state an
    operator approves a removal against — and it carries NOA's marker and the reason T25 wrote,
    since that is the entry being deleted and the reason V96 has to keep off the way back.

    `reason` is on the request the way it is on every approved change. This runner never
    reads it, and the tests assert that it does not reappear.
    """
    # Resolved once: the marker on the evidence line has to be *this* request's, or a test
    # asserting that the reason behind it never comes back would be aimed at a stranger's id.
    request_id = action_request_id or uuid4()
    return ChangeExecutionRequest(
        action_request_id=request_id,
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
        arguments={"server_ref": server_ref, "target": target},
        evidence={
            EVIDENCE_SERVER_ID: str(server_id),
            EVIDENCE_SERVER_NAME: SERVER_NAME,
            EVIDENCE_TARGET: target,
            EVIDENCE_FIREWALL: {
                "combined_verdict": "allowlisted",
                "matches": [f"{CSF_ALLOW_LINE} noa:{request_id} {reason}"],
            },
        },
        reason=reason,
    )


def removed_box(
    *, csf_after: str = CSF_CLEAN_OUTPUT, imunify_after: str = IMUNIFY_CLEAN
) -> FakeFirewallBox:
    """A box for T26's **runner** lane, whose one read is the confirming one.

    `released_box`' argument, one tool over: the runner reads exactly once and the point of that
    read is that it happens after the change, so a one-entry queue holds the *after* state. The
    default is a clean address — the removal took.
    """
    return FakeFirewallBox(
        csf=csf_backend(csf_answer(csf_after)),
        imunify=imunify_backend(imunify_answer(imunify_after)),
    )


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
    "allowlist_remove",
    "changes",
    "csf_commands",
    "execution_request",
    "imunify_commands",
    "release",
    "release_context",
    "released_box",
    "removal_request",
    "removed_box",
]
