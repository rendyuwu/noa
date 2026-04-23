from __future__ import annotations

import asyncio
import re
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.proxmox.integrations.client import ProxmoxClient
from noa_api.proxmox.server_ref import resolve_proxmox_server_ref
from noa_api.storage.postgres.proxmox_servers import SQLProxmoxServerRepository

_NET_KEY_RE = re.compile(r"^net\d+$")
_TASK_POLL_ATTEMPTS = 5
_TASK_POLL_DELAY_SECONDS = 0.1


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _resolution_error(result: object) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _client_for_server(server: object) -> ProxmoxClient:
    return ProxmoxClient(
        base_url=str(getattr(server, "base_url")),
        api_token_id=str(getattr(server, "api_token_id")),
        api_token_secret=maybe_decrypt_text(str(getattr(server, "api_token_secret"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


def _parse_net_segments(net_value: str) -> list[tuple[str, str | None]]:
    segments: list[tuple[str, str | None]] = []
    for raw_segment in net_value.split(","):
        segment = raw_segment.strip()
        if not segment:
            continue
        if "=" in segment:
            key, value = segment.split("=", 1)
            segments.append((key.strip(), value.strip()))
            continue
        segments.append((segment, None))
    return segments


def _net_has_link_down(net_value: str) -> bool:
    for key, value in _parse_net_segments(net_value):
        if key != "link_down":
            continue
        if value is None:
            return True
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _net_link_state(net_value: str) -> str:
    return "down" if _net_has_link_down(net_value) else "up"


def _set_link_down(net_value: str, *, disabled: bool) -> str:
    segments = _parse_net_segments(net_value)
    rewritten: list[str] = []
    link_down_present = False

    for key, value in segments:
        if key == "link_down":
            link_down_present = True
            if disabled:
                rewritten.append("link_down=1")
            continue
        rewritten.append(key if value is None else f"{key}={value}")

    if disabled and not link_down_present:
        rewritten.append("link_down=1")

    return ",".join(rewritten)


def _normalize_nic_entry(key: str, value: str) -> dict[str, object]:
    segments = _parse_net_segments(value)
    model = segments[0][0] if segments else None
    mac_address = segments[0][1] if segments else None
    bridge: str | None = None
    for segment_key, segment_value in segments:
        if segment_key == "bridge":
            bridge = segment_value
            break
    link_down = _net_has_link_down(value)
    return {
        "key": key,
        "value": value,
        "link_down": link_down,
        "link_state": "down" if link_down else "up",
        "model": model,
        "mac_address": mac_address,
        "bridge": bridge,
    }


def _normalize_nics(config: dict[str, object]) -> list[dict[str, object]]:
    nets: list[dict[str, object]] = []
    for key in sorted(config):
        if not _NET_KEY_RE.fullmatch(key):
            continue
        value = _normalized_text(config.get(key))
        if value is None:
            continue
        nets.append(_normalize_nic_entry(key, value))
    return nets


def _select_nic(
    *,
    nets: list[dict[str, object]],
    requested_net: str | None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    normalized_requested_net = _normalized_text(requested_net)

    if not nets:
        return None, {
            "ok": False,
            "error_code": "no_nics_found",
            "message": "No QEMU NICs were found for this VM",
            "nets": [],
        }

    if normalized_requested_net is None:
        if len(nets) == 1:
            return nets[0], None
        return None, {
            "ok": False,
            "error_code": "net_selection_required",
            "message": "Multiple QEMU NICs exist for this VM. Specify which net device to change.",
            "nets": nets,
        }

    for net in nets:
        if _normalized_text(net.get("key")) == normalized_requested_net:
            return net, None

    return None, {
        "ok": False,
        "error_code": "net_not_found",
        "message": f"No QEMU NIC named '{normalized_requested_net}' was found for this VM",
        "nets": nets,
    }


async def _fetch_vm_nic_state(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str | None,
) -> dict[str, object]:
    repo = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = _client_for_server(server)
    config_result = await client.get_qemu_config(node, vmid)
    if config_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(config_result.get("error_code") or "unknown"),
            "message": str(
                config_result.get("message") or "Proxmox QEMU config lookup failed"
            ),
        }

    config = config_result.get("config")
    digest = _normalized_text(config_result.get("digest"))
    config = _object_dict(config)
    if config is None or digest is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected QEMU config payload",
        }

    nets = _normalize_nics(config)
    selected_net, selection_error = _select_nic(nets=nets, requested_net=net)
    if selection_error is not None:
        return {
            **selection_error,
            "server_id": str(resolution.server_id),
            "node": node,
            "vmid": vmid,
            "digest": digest,
        }

    assert selected_net is not None
    selected_key = _normalized_text(selected_net.get("key"))
    selected_value = _normalized_text(selected_net.get("value"))
    assert selected_key is not None
    assert selected_value is not None

    return {
        "ok": True,
        "server_id": str(resolution.server_id),
        "node": node,
        "vmid": vmid,
        "digest": digest,
        "net": selected_key,
        "before_net": selected_value,
        "link_state": _net_link_state(selected_value),
        "auto_selected_net": net is None and len(nets) == 1,
        "nets": nets,
    }


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


async def proxmox_preflight_vm_nic_toggle(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str | None = None,
) -> dict[str, object]:
    return await _fetch_vm_nic_state(
        session=session,
        server_ref=server_ref,
        node=node.strip(),
        vmid=vmid,
        net=net,
    )


async def _change_vm_nic_link_state(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str,
    digest: str,
    disabled: bool,
) -> dict[str, object]:
    normalized_node = node.strip()
    normalized_digest = digest.strip()
    if not normalized_digest:
        return {
            "ok": False,
            "error_code": "digest_required",
            "message": "Digest is required",
        }

    state = await _fetch_vm_nic_state(
        session=session,
        server_ref=server_ref,
        node=normalized_node,
        vmid=vmid,
        net=net,
    )
    if state.get("ok") is not True:
        return state

    current_digest = _normalized_text(state.get("digest"))
    selected_net = _normalized_text(state.get("net"))
    before_net = _normalized_text(state.get("before_net"))
    server_id = _normalized_text(state.get("server_id"))
    if (
        current_digest is None
        or selected_net is None
        or before_net is None
        or server_id is None
    ):
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox preflight returned an unexpected result",
        }

    if current_digest != normalized_digest:
        return {
            "ok": False,
            "error_code": "digest_mismatch",
            "message": "The VM configuration digest changed. Run preflight again before retrying.",
        }

    current_link_state = _net_link_state(before_net)
    desired_link_state = "down" if disabled else "up"
    if current_link_state == desired_link_state:
        return {
            "ok": True,
            "server_id": server_id,
            "node": normalized_node,
            "vmid": vmid,
            "net": selected_net,
            "digest": current_digest,
            "status": "no-op",
            "message": (
                "NIC is already disabled" if disabled else "NIC is already enabled"
            ),
            "before_net": before_net,
            "after_net": before_net,
            "link_state": current_link_state,
            "verified": True,
            "upid": None,
            "task_status": None,
            "task_exit_status": None,
        }

    repo = SQLProxmoxServerRepository(session)
    resolution = await resolve_proxmox_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)

    after_net_requested = _set_link_down(before_net, disabled=disabled)
    mutation_result = await client.update_qemu_config(
        normalized_node,
        vmid,
        digest=current_digest,
        net_key=selected_net,
        net_value=after_net_requested,
    )
    if mutation_result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(mutation_result.get("error_code") or "unknown"),
            "message": str(
                mutation_result.get("message") or "Proxmox QEMU config update failed"
            ),
        }

    upid = _normalized_text(mutation_result.get("upid"))
    if upid is not None:
        task_result, reached_terminal = await _poll_task_status(
            client=client,
            node=normalized_node,
            upid=upid,
        )
        if not reached_terminal:
            if isinstance(task_result, dict) and task_result.get("ok") is not True:
                return {
                    "ok": False,
                    "error_code": str(task_result.get("error_code") or "unknown"),
                    "message": str(
                        task_result.get("message")
                        or "Unable to check Proxmox task status"
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
            task_result.get("task_exit_status")
            if isinstance(task_result, dict)
            else None
        )
        if task_status == "stopped" and task_exit_status not in {None, "OK"}:
            return {
                "ok": False,
                "error_code": "task_failed",
                "message": f"Proxmox task finished with exit status '{task_exit_status}'",
            }
    else:
        task_status = None
        task_exit_status = None

    postflight = await client.get_qemu_config(normalized_node, vmid)
    if postflight.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(postflight.get("error_code") or "unknown"),
            "message": str(
                postflight.get("message")
                or "Unable to verify VM NIC state after update"
            ),
        }

    post_config = postflight.get("config")
    post_config = _object_dict(post_config)
    if post_config is None:
        return {
            "ok": False,
            "error_code": "invalid_response",
            "message": "Proxmox returned an unexpected post-update QEMU config payload",
        }

    after_net = _normalized_text(post_config.get(selected_net))
    if after_net is None:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": f"Unable to verify NIC '{selected_net}' after the update",
        }

    verified = _net_has_link_down(after_net) is disabled
    if not verified:
        return {
            "ok": False,
            "error_code": "postflight_failed",
            "message": (
                "NIC did not become disabled after the update"
                if disabled
                else "NIC did not become enabled after the update"
            ),
        }

    return {
        "ok": True,
        "server_id": server_id,
        "node": normalized_node,
        "vmid": vmid,
        "net": selected_net,
        "digest": current_digest,
        "status": "changed",
        "message": "NIC disabled" if disabled else "NIC enabled",
        "before_net": before_net,
        "after_net": after_net,
        "link_state": _net_link_state(after_net),
        "verified": True,
        "upid": upid,
        "task_status": task_status,
        "task_exit_status": task_exit_status,
    }


async def proxmox_disable_vm_nic(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str,
    digest: str,
    reason: str,
) -> dict[str, object]:
    _ = reason
    return await _change_vm_nic_link_state(
        session=session,
        server_ref=server_ref,
        node=node,
        vmid=vmid,
        net=net,
        digest=digest,
        disabled=True,
    )


async def proxmox_enable_vm_nic(
    *,
    session: AsyncSession,
    server_ref: str,
    node: str,
    vmid: int,
    net: str,
    digest: str,
    reason: str,
) -> dict[str, object]:
    _ = reason
    return await _change_vm_nic_link_state(
        session=session,
        server_ref=server_ref,
        node=node,
        vmid=vmid,
        net=net,
        digest=digest,
        disabled=False,
    )
