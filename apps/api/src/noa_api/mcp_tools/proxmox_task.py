"""One Proxmox task poll, for the two runners that wait on a UPID.

The NIC runner and the password runner both write a VM config and then wait for the task Proxmox
hands back. The loop and its four numbers were written twice, byte for byte, and answered in two
different failure types — so the loop lives here and returns the verdict as a pair, leaving each
caller to say it in its own words. The two sentences an operator reads are per-tool and stay
beside the runner that composes them.
"""

from __future__ import annotations

import asyncio
from typing import Final

from core.integrations.proxmox.client import ProxmoxClient
from noa_api.mcp_tools.proxmox_password import text_or_none

# The config write's own task did not reach a terminal state in time. The write was accepted, so
# the change may already have taken — which is why neither caller reports this as a refusal.
ERROR_TASK_TIMEOUT: Final = "task_timeout"

# The task reached a terminal state and it was a failure — Proxmox saying it did not apply.
ERROR_TASK_FAILED: Final = "task_failed"

# `noa-old`'s numbers, kept. A VM config write finishes in well under a second in practice.
TASK_POLL_ATTEMPTS: Final = 30
TASK_POLL_DELAY_SECONDS: Final = 0.5


async def wait_for_terminal_task(
    client: ProxmoxClient, *, node: str, upid: str
) -> tuple[str, str | None] | None:
    """Poll one UPID to a terminal state. `None` when it finished successfully.

    `(ERROR_TASK_FAILED, exit_status)` when the task reached a terminal non-`OK` status, and
    `(ERROR_TASK_TIMEOUT, None)` when the polls ran out or a poll could not read the status.

    Terminality is `status == "stopped"`, or an exit status present while the task is no longer
    running — `client.get_task_status` normalises both to `None` when absent, so an empty string
    cannot read as present.

    A poll that cannot *read* the status is treated as a timeout rather than as a task failure:
    not knowing what a task did is not evidence that it failed, and the write it belongs to has
    already been accepted.

    The two failures keep separate codes, and the postflights read them apart: a terminal non-`OK`
    task is Proxmox saying it did not apply the write, while `task_timeout` is NOA having stopped
    waiting on a write Proxmox accepted. Only the first makes a disagreeing read conclusive.

    `client` and `node` rather than one of the two runners' target records: the poll needs no more
    than those two, and taking either dataclass would make this module know about both.
    """
    for attempt in range(TASK_POLL_ATTEMPTS):
        status_result = await client.get_task_status(node, upid)
        if status_result.get("ok") is not True:
            break

        task_status = text_or_none(status_result.get("task_status"))
        task_exit_status = text_or_none(status_result.get("task_exit_status"))
        if task_status == "stopped" or (task_exit_status is not None and task_status != "running"):
            if task_exit_status in (None, "OK"):
                return None
            return ERROR_TASK_FAILED, task_exit_status

        if attempt < TASK_POLL_ATTEMPTS - 1:
            await asyncio.sleep(TASK_POLL_DELAY_SECONDS)

    return ERROR_TASK_TIMEOUT, None
