"""The seven-tool CHANGE harness: each tool through its gate, then the runner it authorised.

Lifted out of `test_change_receipt_halves.py` when that file crossed the 900-line cap
`test_config.py` enforces over `git ls-files`. A cap is met by splitting and never by trimming an
assertion to make a number go down, and this is the seam that was already there: what moved is the
*apparatus* — the two halves of one approved change, and the lane per tool that produces them —
while every claim made about those halves stayed where it was.

**Here rather than imported test-file-to-test-file.** A second suite reaching into a first for its
fixtures makes the first's collection order load-bearing and gives the shared names no home of
their own. `support/` is where this repo already keeps apparatus two suites share.

What a lane answers is a `Halves`: the evidence the gate wrote and the operator was asked against,
and the payload the runner answered with, both read back off a receipt `build_receipt` actually
built rather than off the values handed to it. That distinction is the point of the whole harness —
`after` is the payload AFTER `redact_mapping`, so a fixture holding the raw envelope would assert
equality against bytes no receipt contains.

`CASES` is read against `build_change_runners` by its reader, so an eighth CHANGE tool arrives in
every parametrisation rather than being a name somebody has to remember to add to a list.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from core.approvals.context import arguments_from_context, evidence_from_context
from core.approvals.delta import ChangeDelta
from core.approvals.execution import (
    RECEIPT_AFTER_KEY,
    RECEIPT_BEFORE_KEY,
    ChangeExecutionRequest,
    ChangeRunner,
    build_receipt,
)
from noa_api.mcp_tools.pmg_whitelist import TOOL_PMG_WHITELIST
from noa_api.mcp_tools.pmg_whitelist_runner import build_pmg_whitelist_runner
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_nic_runner import build_proxmox_vm_nic_runner
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.proxmox_password_runner import build_proxmox_reset_vm_password_runner
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
    build_whm_unsuspend_runner,
)
from noa_api.mcp_tools.whm_firewall_allowlist import (
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    build_whm_firewall_allowlist_remove_runner,
)
from noa_api.mcp_tools.whm_firewall_change import (
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    build_whm_firewall_release_runner,
)
from support.action_decisions import REASON
from support.change_delta import outcome_of
from support.mcp_identity import authenticated_caller, http_request_context
from support.pmg import call_whitelist, whitelist_change_context
from support.proxmox_nic import call_nic, nic_context, no_polling_delay
from support.proxmox_password import no_polling_delay as no_password_polling_delay
from support.proxmox_password import reset, reset_context
from support.servers import ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    UNSUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_success_body,
)
from support.whm_firewall import (
    CSF_ALLOW_LINE,
    CSF_CLEAN_OUTPUT,
    CSF_DENY_LINE,
    IMUNIFY_CLEAN,
    IMUNIFY_DROP,
    IMUNIFY_WHITE,
    FakeFirewallBox,
    csf_answer,
    csf_backend,
    imunify_answer,
    imunify_backend,
)
from support.whm_firewall_change import allowlist_remove, release, release_context

# The WHM account the two account lanes run against, and the reseller WHM reports as its owner.
# `whm_server`'s credential is `root`, and the runner refuses unless the two agree.
ACCOUNT = "acmeco"
OWNER = "root"


@dataclass(frozen=True)
class Halves:
    """One approved change end to end, as its receipt is built from it.

    `evidence` is what the gate wrote and the operator was asked against; `payload` is what the
    runner answered. Both are read back off a receipt `build_receipt` actually built, rather than
    from the values handed to it: `after` is the payload AFTER `redact_mapping`, and a harness
    holding the raw envelope instead would assert equality against bytes no receipt contains.
    That gap is empty today only because no CHANGE payload currently carries a key on the
    redactor's list, which is a coincidence rather than a property — `proxmox_reset_vm_password`
    already carries a credential and already shares a key, so it is one field name away from
    `password` and the harness would have gone on agreeing in the weaker direction.
    """

    tool: str
    evidence: dict[str, Any]
    payload: dict[str, Any]
    delta: ChangeDelta | None

    @property
    def shared_keys(self) -> set[str]:
        """The top-level keys both halves spell the same way."""
        return set(self.evidence) & set(self.payload)

    @property
    def moved_fields(self) -> set[str]:
        """Every field the delta reports as having changed."""
        rendered = {} if self.delta is None else self.delta.as_payload()
        return {change["field"] for change in rendered.get("changed_fields") or []}


async def approved_run(fixture: ToolFixture, runner: ChangeRunner, *, tool: str) -> Halves:
    """Run `runner` against the request the gate just wrote, and return both halves.

    The request is built the way `core.approvals.execution` builds one — arguments and evidence
    lifted off `approval_context` by the production readers — so the evidence the runner sees is
    the row's, not a fixture's approximation of it. Both halves then come off `build_receipt`
    itself rather than off the values passed to it, which is what makes this harness's claim about
    receipts true by construction instead of true by coincidence.
    """
    recorded = fixture.action_requests.requests[0]
    request = ChangeExecutionRequest(
        action_request_id=recorded.action_request_id,
        tool_run_id=uuid4(),
        tool_name=tool,
        arguments=arguments_from_context(recorded.approval_context),
        evidence=evidence_from_context(recorded.approval_context),
        reason=REASON,
    )
    outcome = await outcome_of(runner, request)
    receipt = build_receipt(
        evidence=request.evidence,
        payload=outcome.payload,
        delta=outcome.delta,
    )
    return Halves(
        tool=tool,
        evidence=dict(receipt[RECEIPT_BEFORE_KEY]),
        payload=dict(receipt[RECEIPT_AFTER_KEY]),
        delta=outcome.delta,
    )


# --------------------------------------------------------------------------------------
# One lane per CHANGE tool: call the gate, then run what it authorised
# --------------------------------------------------------------------------------------


async def release_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The firewall-release tool, on a box that reads blocked before the change and allowlisted
    after it.
    """
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_DENY_LINE), csf_answer(CSF_ALLOW_LINE)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_DROP), imunify_answer(IMUNIFY_WHITE)),
        ),
    )
    await release(fixture)
    return await approved_run(
        fixture,
        build_whm_firewall_release_runner(context=fixture.context),
        tool=TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW,
    )


async def allowlist_remove_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The allowlist-remove tool, on a box holding an allow entry that the removal then clears."""
    fixture, _ = release_context(
        monkeypatch,
        box=FakeFirewallBox(
            csf=csf_backend(csf_answer(CSF_ALLOW_LINE), csf_answer(CSF_CLEAN_OUTPUT)),
            imunify=imunify_backend(imunify_answer(IMUNIFY_WHITE), imunify_answer(IMUNIFY_CLEAN)),
        ),
    )
    await allowlist_remove(fixture)
    return await approved_run(
        fixture,
        build_whm_firewall_allowlist_remove_runner(context=fixture.context),
        tool=TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    )


async def nic_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The VM NIC tool. The fake VM holds its own config, so the runner reads what the gate saw."""
    no_polling_delay(monkeypatch)
    fixture, _ = nic_context()
    await call_nic(fixture)
    return await approved_run(
        fixture,
        build_proxmox_vm_nic_runner(context=fixture.context),
        tool=TOOL_PROXMOX_VM_NIC,
    )


async def reset_password_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The password-reset tool, the one whose changed value may not be rendered at all."""
    no_password_polling_delay(monkeypatch)
    fixture, _ = reset_context()
    await reset(fixture)
    return await approved_run(
        fixture,
        build_proxmox_reset_vm_password_runner(context=fixture.context),
        tool=TOOL_PROXMOX_RESET_VM_PASSWORD,
    )


async def whitelist_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The whitelist tool. The fake gateway holds `mynetworks`, so the add the gate proposed is the
    one run.
    """
    fixture, _ = whitelist_change_context(monkeypatch)
    await call_whitelist(fixture)
    return await approved_run(
        fixture,
        build_pmg_whitelist_runner(context=fixture.context),
        tool=TOOL_PMG_WHITELIST,
    )


def account_context(*, listings: list[list[dict[str, Any]]], path: str) -> ToolFixture:
    """A WHM endpoint answering `listaccts` twice — once for the gate, once for the postflight.

    Two bodies rather than one, because a CHANGE workflow reads that endpoint on both sides of the
    change and a single answer would let the postflight pass against a read that never happened.
    """
    api = FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: [listaccts_body(rows) for rows in listings],
            path: [whm_api_success_body()],
        },
    )
    cipher = build_tool_context().cipher
    row = whm_server("alpha")
    row.api_token = cipher.encrypt_text("whm-api-token-plaintext")
    return build_tool_context(servers=[row], cipher=cipher, whm_transport=api.transport)


def account_row(*, suspended: int) -> dict[str, Any]:
    """One `listaccts` row for the account both lanes act on."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=suspended, owner=OWNER)


async def suspend_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The suspend tool: live when the operator was asked, suspended when the runner checked."""
    fixture = account_context(
        listings=[[account_row(suspended=0)], [account_row(suspended=1)]],
        path=SUSPENDACCT_PATH,
    )
    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        await whm_suspend_account(server_ref="alpha", username=ACCOUNT, context=fixture.context)
    return await approved_run(
        fixture,
        build_whm_suspend_runner(context=fixture.context),
        tool=TOOL_WHM_SUSPEND_ACCOUNT,
    )


async def unsuspend_halves(monkeypatch: pytest.MonkeyPatch) -> Halves:
    """The unsuspend tool, the mirror: suspended at gate time and live afterwards."""
    fixture = account_context(
        listings=[[account_row(suspended=1)], [account_row(suspended=0)]],
        path=UNSUSPENDACCT_PATH,
    )
    user, _ = authenticated_caller()
    with http_request_context({}, user=user):
        await whm_unsuspend_account(server_ref="alpha", username=ACCOUNT, context=fixture.context)
    return await approved_run(
        fixture,
        build_whm_unsuspend_runner(context=fixture.context),
        tool=TOOL_WHM_UNSUSPEND_ACCOUNT,
    )


HalvesBuilder = Callable[[pytest.MonkeyPatch], Awaitable[Halves]]

# Every CHANGE tool, its lane, and the keys its two halves were measured to share. The key sets
# are pinned rather than merely checked for the property below, so a tool that grows an overlap
# fails here and its author has to look at whether the halves are still undiffable — which is the
# claim four modules and `docs/change-delta.md` make about this table.
CASES: dict[str, tuple[HalvesBuilder, set[str]]] = {
    TOOL_WHM_SUSPEND_ACCOUNT: (suspend_halves, {"server"}),
    TOOL_WHM_UNSUSPEND_ACCOUNT: (unsuspend_halves, {"server"}),
    TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW: (
        release_halves,
        {"server", "target", "duration_minutes"},
    ),
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE: (allowlist_remove_halves, {"server", "target"}),
    TOOL_PROXMOX_RESET_VM_PASSWORD: (
        reset_password_halves,
        {"server", "node", "vmid", "username"},
    ),
    TOOL_PROXMOX_VM_NIC: (nic_halves, {"server", "node", "vmid", "net", "action"}),
    TOOL_PMG_WHITELIST: (
        whitelist_halves,
        {"server", "action", "target", "normalized_target"},
    ),
}
