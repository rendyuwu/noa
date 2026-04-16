from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository

_TASK_POLL_ATTEMPTS = 5
_TASK_POLL_DELAY_SECONDS = 0.1


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _client_for_server(server: Any) -> ProxmoxClient:
    return ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


def _upstream_error(
    result: dict[str, object], *, fallback_message: str
) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or fallback_message),
    }


def _cloudinit_confirms_password_reset(result: dict[str, object]) -> bool:
    data = result.get("data")
    if isinstance(data, dict):
        return _normalized_text(data.get("cipassword")) is not None

    if not isinstance(data, list):
        return False

    for entry in data:
        if not isinstance(entry, dict):
            continue
        if _normalized_text(entry.get("key")) != "cipassword":
            continue
        if _normalized_text(entry.get("value")) is not None:
            return True
    return False


def _sanitize_cloudinit_dump_user(dump_value: object) -> tuple[str | None, bool]:
    if not isinstance(dump_value, str):
        return None, False

    dump_text = dump_value.strip()
    if not dump_text:
        return None, False

    sanitized_lines: list[str] = []
    found_password = False
    for line in dump_text.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("password:"):
            sanitized_lines.append(line)
            continue

        value = stripped[len("password:") :].strip()
        if not value:
            return None, False

        leading = line[: len(line) - len(stripped)]
        sanitized_lines.append(f"{leading}password: [REDACTED]")
        found_password = True

    if not found_password:
        return None, False

    sanitized = "\n".join(sanitized_lines)
    if dump_value.endswith("\n"):
        sanitized += "\n"
    return sanitized, True


async def _resolve_client(
    *, session: AsyncSession, server_ref: str
) -> tuple[ProxmoxClient, str] | dict[str, object]:
    repo: Any = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)
    assert resolution.server_id is not None
    return client, str(resolution.server_id)


def _is_terminal_task_status(
    task_status: str | None, task_exit_status: str | None
) -> bool:
    if task_status == "stopped":
        return True
    return task_exit_status is not None and task_status != "running"


async def _poll_task_status(
    *,
    client: ProxmoxClient,
    node: str,
    upid: str,
) -> tuple[dict[str, object] | None, bool]:
    latest_result: dict[str, object] | None = None

    for attempt in range(_TASK_POLL_ATTEMPTS):
        status_result = await client.get_task_status(node, upid)
        if status_result.get("ok") is not True:
            return status_result, False
        latest_result = status_result

        task_status = _normalized_text(status_result.get("task_status"))
        task_exit_status = _normalized_text(status_result.get("task_exit_status"))
        if _is_terminal_task_status(task_status, task_exit_status):
            return status_result, True

        if attempt < _TASK_POLL_ATTEMPTS - 1:
            await asyncio.sleep(_TASK_POLL_DELAY_SECONDS)

    return latest_result, False


async def proxmox_preflight_vm_cloudinit_password_reset(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
) -> dict[str, object]:
    normalized_node = node.strip()
    resolved = await _resolve_client(session=session, server_ref=server_ref)
    if isinstance(resolved, dict):
        return resolved

    client, server_id = resolved
    config_result = await client.get_qemu_config(normalized_node, vmid)
    if config_result.get("ok") is not True:
        return _upstream_error(
            config_result, fallback_message="Proxmox VM config lookup failed"
        )

    cloudinit_result = await client.get_qemu_cloudinit(normalized_node, vmid)
    if cloudinit_result.get("ok") is not True:
        return _upstream_error(
            cloudinit_result, fallback_message="Proxmox cloud-init lookup failed"
        )

    return {
        "ok": True,
        "message": "ok",
        "server_id": server_id,
        "node": normalized_node,
        "vmid": vmid,
        "config": config_result,
        "cloudinit": cloudinit_result,
    }


async def _wait_for_terminal_task(
    *,
    client: ProxmoxClient,
    node: str,
    upid: str,
) -> dict[str, object] | None:
    task_result, reached_terminal = await _poll_task_status(
        client=client,
        node=node,
        upid=upid,
    )
    if not reached_terminal:
        if isinstance(task_result, dict) and task_result.get("ok") is not True:
            return {
                "ok": False,
                "error_code": str(task_result.get("error_code") or "unknown"),
                "message": str(
                    task_result.get("message") or "Unable to check Proxmox task status"
                ),
            }
        return {
            "ok": False,
            "error_code": "task_timeout",
            "message": "Proxmox task did not reach a terminal state before verification timed out",
        }

    task_status = _normalized_text(
        task_result.get("task_status") if isinstance(task_result, dict) else None
    )
    task_exit_status = _normalized_text(
        task_result.get("task_exit_status") if isinstance(task_result, dict) else None
    )
    if task_status == "stopped" and task_exit_status not in {None, "OK"}:
        return {
            "ok": False,
            "error_code": "task_failed",
            "message": f"Proxmox task finished with exit status '{task_exit_status}'",
        }
    return None


async def proxmox_reset_vm_cloudinit_password(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    new_password: str,
    reason: str,
) -> dict[str, object]:
    _ = reason
    normalized_node = node.strip()
    resolved = await _resolve_client(session=session, server_ref=server_ref)
    if isinstance(resolved, dict):
        return resolved

    client, server_id = resolved

    set_result = await client.set_qemu_cloudinit_password(
        normalized_node, vmid, new_password
    )
    if set_result.get("ok") is not True:
        return _upstream_error(
            set_result,
            fallback_message="Proxmox cloud-init password update failed",
        )

    set_upid = _normalized_text(set_result.get("data"))
    if set_upid is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected task identifier",
        }

    task_error = await _wait_for_terminal_task(
        client=client,
        node=normalized_node,
        upid=set_upid,
    )
    if task_error is not None:
        return task_error

    regenerate_result = await client.regenerate_qemu_cloudinit(normalized_node, vmid)
    if regenerate_result.get("ok") is not True:
        return _upstream_error(
            regenerate_result,
            fallback_message="Proxmox cloud-init regeneration failed",
        )

    regenerate_upid = _normalized_text(regenerate_result.get("data"))
    if regenerate_upid is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected task identifier",
        }

    task_error = await _wait_for_terminal_task(
        client=client,
        node=normalized_node,
        upid=regenerate_upid,
    )
    if task_error is not None:
        return task_error

    cloudinit_result = await client.get_qemu_cloudinit(normalized_node, vmid)
    if cloudinit_result.get("ok") is not True:
        return _upstream_error(
            cloudinit_result,
            fallback_message="Unable to verify Proxmox cloud-init values",
        )

    if not _cloudinit_confirms_password_reset(cloudinit_result):
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init payload did not confirm the password reset",
        }

    dump_result = await client.get_qemu_cloudinit_dump_user(normalized_node, vmid)
    if dump_result.get("ok") is not True:
        return _upstream_error(
            dump_result,
            fallback_message="Unable to verify Proxmox cloud-init user dump",
        )

    sanitized_dump, dump_confirmed = _sanitize_cloudinit_dump_user(
        dump_result.get("data")
    )
    if not dump_confirmed or sanitized_dump is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": "Proxmox cloud-init dump did not confirm the password reset",
        }

    return {
        "ok": True,
        "message": "ok",
        "status": "changed",
        "server_id": server_id,
        "node": normalized_node,
        "vmid": vmid,
        "set_password_task": set_result,
        "regenerate_cloudinit": regenerate_result,
        "cloudinit": cloudinit_result,
        "cloudinit_dump_user": {**dump_result, "data": sanitized_dump},
        "verified": True,
    }
