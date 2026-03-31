from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.csf import parse_csf_grep_output, parse_csf_target
from noa_api.whm.integrations.csf_cli import (
    CSFCLIError,
    require_csf_success,
    run_csf_command,
)
from noa_api.whm.integrations.imunify import (
    format_imunify_matches,
    imunify_entry_to_dict,
    parse_imunify_ip_list_response,
)
from noa_api.whm.integrations.imunify_cli import (
    ImunifyCLIError,
    check_firewall_binaries,
    command_output_text as imunify_command_output_text,
    parse_imunify_json_output,
    run_imunify_command,
)
from noa_api.whm.server_ref import resolve_whm_server_ref
from noa_api.whm.tools.read_tools import whm_mail_log_failed_auth_suspects


_LFD_AUTH_LINE_RE = re.compile(
    r"\blfd(?:\[\d+\])?:\s*\((smtpauth|imapd|pop3d)\)",
    re.IGNORECASE,
)


def _extract_lfd_auth_line(csf_preflight: object) -> str | None:
    if not isinstance(csf_preflight, dict):
        return None
    if csf_preflight.get("ok") is not True:
        return None

    matches = csf_preflight.get("matches")
    if isinstance(matches, list):
        for item in matches:
            if isinstance(item, str) and _LFD_AUTH_LINE_RE.search(item):
                return item

    raw_output = csf_preflight.get("raw_output")
    if isinstance(raw_output, str):
        for line in raw_output.splitlines():
            if _LFD_AUTH_LINE_RE.search(line):
                return line.strip()
    return None


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _no_firewall_tools_error() -> dict[str, object]:
    return {
        "ok": False,
        "error_code": "no_firewall_tools",
        "message": "Neither CSF nor Imunify360 is available on this server",
    }


# ---------------------------------------------------------------------------
# CSF operations (internal)
# ---------------------------------------------------------------------------


async def _csf_preflight(server: Any, *, target: str) -> dict[str, object]:
    """Check target status in CSF."""
    try:
        grep_result = await run_csf_command(server, args=["-g", target])
        output = require_csf_success(grep_result, default_message="CSF grep failed")
        if not output.strip():
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "CSF grep returned an invalid response",
            }
        parsed = parse_csf_grep_output(output, target=target)
        return {
            "ok": True,
            "verdict": parsed.verdict,
            "matches": parsed.matches,
            "raw_output": output,
        }
    except CSFCLIError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }


async def _csf_unblock(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from CSF block lists."""
    try:
        # Remove from temporary blocks
        await run_csf_command(server, args=["-tr", target])
        # Remove from permanent deny list
        await run_csf_command(server, args=["-dr", target])
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_allowlist_add_ttl(
    server: Any, *, target: str, duration_seconds: int, reason: str
) -> dict[str, object]:
    """Add target to CSF temporary allowlist."""
    try:
        await run_csf_command(
            server, args=["-ta", target, str(duration_seconds), reason]
        )
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_allowlist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from CSF allowlist."""
    try:
        # Remove from temporary allows
        await run_csf_command(server, args=["-tra", target])
        # Remove from permanent allow list
        await run_csf_command(server, args=["-ar", target])
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _csf_denylist_add_ttl(
    server: Any, *, target: str, duration_seconds: int, reason: str
) -> dict[str, object]:
    """Add target to CSF temporary denylist."""
    try:
        await run_csf_command(
            server, args=["-td", target, str(duration_seconds), reason]
        )
        return {"ok": True, "status": "changed"}
    except CSFCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


# ---------------------------------------------------------------------------
# Imunify operations (internal)
# ---------------------------------------------------------------------------


async def _imunify_preflight(server: Any, *, target: str) -> dict[str, object]:
    """Check target status in Imunify."""
    try:
        result = await run_imunify_command(
            server, args=["ip-list", "local", "list", "--by-ip", target, "--json"]
        )
        raw_output = imunify_command_output_text(result)
        data = parse_imunify_json_output(result)
        parsed = parse_imunify_ip_list_response(data, target)
        return {
            "ok": True,
            "verdict": parsed.verdict,
            "entries": [imunify_entry_to_dict(e) for e in parsed.entries],
            "matches": format_imunify_matches(parsed.entries),
            "raw_data": data,
            "raw_output": raw_output,
        }
    except ImunifyCLIError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }


async def _imunify_blacklist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from Imunify blacklist."""
    try:
        result = await run_imunify_command(
            server,
            args=["ip-list", "local", "delete", "--purpose", "drop", target, "--json"],
        )
        # Parse to verify success
        parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_whitelist_add_ttl(
    server: Any, *, target: str, expiration_epoch: int, reason: str
) -> dict[str, object]:
    """Add target to Imunify whitelist with expiration."""
    try:
        result = await run_imunify_command(
            server,
            args=[
                "ip-list",
                "local",
                "add",
                "--purpose",
                "white",
                target,
                "--comment",
                reason,
                "--expiration",
                str(expiration_epoch),
                "--json",
            ],
        )
        parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_whitelist_remove(server: Any, *, target: str) -> dict[str, object]:
    """Remove target from Imunify whitelist."""
    try:
        result = await run_imunify_command(
            server,
            args=["ip-list", "local", "delete", "--purpose", "white", target, "--json"],
        )
        parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


async def _imunify_blacklist_add_ttl(
    server: Any, *, target: str, expiration_epoch: int, reason: str
) -> dict[str, object]:
    """Add target to Imunify blacklist with expiration."""
    try:
        result = await run_imunify_command(
            server,
            args=[
                "ip-list",
                "local",
                "add",
                "--purpose",
                "drop",
                target,
                "--comment",
                reason,
                "--expiration",
                str(expiration_epoch),
                "--json",
            ],
        )
        parse_imunify_json_output(result)
        return {"ok": True, "status": "changed"}
    except ImunifyCLIError as exc:
        return {"ok": False, "error_code": exc.code, "message": exc.message}


# ---------------------------------------------------------------------------
# Combined verdict logic
# ---------------------------------------------------------------------------


def _compute_combined_verdict(
    csf_verdict: str | None,
    imunify_verdict: str | None,
) -> str:
    """
    Compute combined verdict from CSF and Imunify results.

    Priority: blocked > allowlisted/whitelisted > not_found
    """
    if csf_verdict == "blocked" or imunify_verdict == "blacklisted":
        return "blocked"
    if csf_verdict == "allowlisted" or imunify_verdict == "whitelisted":
        return "allowlisted"
    return "not_found"


# ---------------------------------------------------------------------------
# Public unified firewall tools
# ---------------------------------------------------------------------------


async def whm_preflight_firewall_entries(
    *,
    session: AsyncSession,
    server_ref: str,
    target: str,
) -> dict[str, object]:
    """
    Check IP/target status in both CSF and Imunify (based on availability).

    Returns combined result with verdict from all available firewall tools.
    """
    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    normalized_target = target.strip()
    if not normalized_target:
        return {
            "ok": False,
            "error_code": "target_required",
            "message": "Target is required",
        }

    # Validate target format
    parsed_target = parse_csf_target(normalized_target)
    if parsed_target.kind == "unknown":
        return {
            "ok": False,
            "error_code": "invalid_target",
            "message": "Target must be a valid IP, CIDR, or hostname",
        }

    # Check which firewall tools are available
    available = await check_firewall_binaries(server)

    if not available["csf"] and not available["imunify"]:
        return _no_firewall_tools_error()

    # Query available tools in parallel
    tasks: dict[str, Any] = {}
    if available["csf"]:
        tasks["csf"] = _csf_preflight(server, target=normalized_target)
    if available["imunify"]:
        tasks["imunify"] = _imunify_preflight(server, target=normalized_target)

    results_list = await asyncio.gather(*tasks.values())
    results = dict(zip(tasks.keys(), results_list))

    # Extract verdicts
    csf_verdict: str | None = None
    imunify_verdict: str | None = None
    all_matches: list[str] = []

    if "csf" in results:
        csf_result = results["csf"]
        if csf_result.get("ok"):
            csf_verdict = csf_result.get("verdict")
            all_matches.extend(csf_result.get("matches", []))

    if "imunify" in results:
        imunify_result = results["imunify"]
        if imunify_result.get("ok"):
            imunify_verdict = imunify_result.get("verdict")
            all_matches.extend(imunify_result.get("matches", []))

    combined_verdict = _compute_combined_verdict(csf_verdict, imunify_verdict)

    response: dict[str, object] = {
        "ok": True,
        "server_id": str(resolution.server_id),
        "target": normalized_target,
        "available_tools": available,
        "combined_verdict": combined_verdict,
        "matches": all_matches,
    }

    if "csf" in results:
        response["csf"] = results["csf"]
    if "imunify" in results:
        response["imunify"] = results["imunify"]

    return response


async def whm_firewall_unblock(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    reason: str,
) -> dict[str, object]:
    """
    Remove block entries from both CSF and Imunify (based on availability).
    """
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    # Check which firewall tools are available
    available = await check_firewall_binaries(server)

    if not available["csf"] and not available["imunify"]:
        return _no_firewall_tools_error()

    results: list[dict[str, object]] = []
    overall_ok = True

    for raw in targets:
        target = raw.strip()
        if not target:
            overall_ok = False
            results.append(
                {
                    "target": raw,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "Target is required",
                }
            )
            continue

        # Preflight check
        preflight_tasks: dict[str, Any] = {}
        if available["csf"]:
            preflight_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            preflight_tasks["imunify"] = _imunify_preflight(server, target=target)

        preflight_results = await asyncio.gather(*preflight_tasks.values())
        preflight = dict(zip(preflight_tasks.keys(), preflight_results))

        csf_blocked = (
            preflight.get("csf", {}).get("ok")
            and preflight.get("csf", {}).get("verdict") == "blocked"
        )
        imunify_blocked = (
            preflight.get("imunify", {}).get("ok")
            and preflight.get("imunify", {}).get("verdict") == "blacklisted"
        )

        if not csf_blocked and not imunify_blocked:
            # Nothing to unblock
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "available_tools": available,
                    "csf": preflight.get("csf"),
                    "imunify": preflight.get("imunify"),
                }
            )
            continue

        # Execute unblock operations
        change_tasks: dict[str, Any] = {}
        if csf_blocked:
            change_tasks["csf"] = _csf_unblock(server, target=target)
        if imunify_blocked:
            change_tasks["imunify"] = _imunify_blacklist_remove(server, target=target)

        change_results = await asyncio.gather(*change_tasks.values())
        changes = dict(zip(change_tasks.keys(), change_results))

        # Postflight verification
        post_tasks: dict[str, Any] = {}
        if available["csf"]:
            post_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            post_tasks["imunify"] = _imunify_preflight(server, target=target)

        post_results = await asyncio.gather(*post_tasks.values())
        postflight = dict(zip(post_tasks.keys(), post_results))

        # Check if still blocked
        csf_still_blocked = (
            postflight.get("csf", {}).get("ok")
            and postflight.get("csf", {}).get("verdict") == "blocked"
        )
        imunify_still_blocked = (
            postflight.get("imunify", {}).get("ok")
            and postflight.get("imunify", {}).get("verdict") == "blacklisted"
        )

        target_ok = True
        status = "changed"

        if csf_still_blocked or imunify_still_blocked:
            target_ok = False
            overall_ok = False
            status = "error"

        # Check for execution errors
        for key, change_result in changes.items():
            if not change_result.get("ok"):
                target_ok = False
                overall_ok = False
                status = "error"

        entry: dict[str, object] = {
            "target": target,
            "ok": target_ok,
            "status": status,
            "available_tools": available,
            "csf": {
                "preflight": preflight.get("csf"),
                "change": changes.get("csf"),
                "postflight": postflight.get("csf"),
            }
            if available["csf"]
            else None,
            "imunify": {
                "preflight": preflight.get("imunify"),
                "change": changes.get("imunify"),
                "postflight": postflight.get("imunify"),
            }
            if available["imunify"]
            else None,
        }

        # If the CSF reason indicates an LFD auth block (smtpauth/imapd/pop3d),
        # automatically identify the most likely suspect mailbox usernames.
        lfd_line = _extract_lfd_auth_line(preflight.get("csf"))
        if (
            status == "changed"
            and target_ok
            and lfd_line is not None
            and not csf_still_blocked
            and not imunify_still_blocked
        ):
            suspects_result = await whm_mail_log_failed_auth_suspects(
                session=session,
                server_ref=server_ref,
                lfd_log_line=lfd_line,
                include_raw_output=False,
            )
            if suspects_result.get("ok") is True:
                entry["failed_auth_suspects"] = suspects_result.get("suspects", [])
                entry["failed_auth_service"] = suspects_result.get("service")
                entry["failed_auth_ip"] = suspects_result.get("ip")
                entry["failed_auth_month"] = suspects_result.get("month")
                entry["failed_auth_day"] = suspects_result.get("day")
            else:
                entry["failed_auth_suspects_error"] = {
                    "error_code": suspects_result.get("error_code") or "unknown",
                    "message": suspects_result.get("message")
                    or "Failed to identify suspect mailbox usernames",
                }

        results.append(entry)

    return {"ok": overall_ok, "available_tools": available, "results": results}


async def whm_firewall_allowlist_add_ttl(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    duration_minutes: int,
    reason: str,
) -> dict[str, object]:
    """
    Add targets to allowlist/whitelist in both CSF and Imunify (based on availability).
    """
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }
    if duration_minutes <= 0:
        return {
            "ok": False,
            "error_code": "duration_invalid",
            "message": "duration_minutes must be positive",
        }

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    available = await check_firewall_binaries(server)

    if not available["csf"] and not available["imunify"]:
        return _no_firewall_tools_error()

    duration_seconds = duration_minutes * 60
    expiration_epoch = int(time.time()) + duration_seconds

    results: list[dict[str, object]] = []
    overall_ok = True

    for raw in targets:
        target = raw.strip()
        if not target:
            overall_ok = False
            results.append(
                {
                    "target": raw,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "Target is required",
                }
            )
            continue

        # Validate IPv4 only for TTL operations
        parsed_target = parse_csf_target(target)
        if parsed_target.kind != "ip":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "TTL allowlist only supports IPv4 addresses",
                }
            )
            continue

        # Preflight check
        preflight_tasks: dict[str, Any] = {}
        if available["csf"]:
            preflight_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            preflight_tasks["imunify"] = _imunify_preflight(server, target=target)

        preflight_results = await asyncio.gather(*preflight_tasks.values())
        preflight = dict(zip(preflight_tasks.keys(), preflight_results))

        csf_already = (
            preflight.get("csf", {}).get("ok")
            and preflight.get("csf", {}).get("verdict") == "allowlisted"
        )
        imunify_already = (
            preflight.get("imunify", {}).get("ok")
            and preflight.get("imunify", {}).get("verdict") == "whitelisted"
        )

        # If already allowlisted in all available tools, no-op
        all_already = True
        if available["csf"] and not csf_already:
            all_already = False
        if available["imunify"] and not imunify_already:
            all_already = False

        if all_already:
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "available_tools": available,
                    "csf": preflight.get("csf"),
                    "imunify": preflight.get("imunify"),
                }
            )
            continue

        # Execute allowlist operations
        change_tasks: dict[str, Any] = {}
        if available["csf"] and not csf_already:
            change_tasks["csf"] = _csf_allowlist_add_ttl(
                server,
                target=target,
                duration_seconds=duration_seconds,
                reason=reason.strip(),
            )
        if available["imunify"] and not imunify_already:
            change_tasks["imunify"] = _imunify_whitelist_add_ttl(
                server,
                target=target,
                expiration_epoch=expiration_epoch,
                reason=reason.strip(),
            )

        change_results = await asyncio.gather(*change_tasks.values())
        changes = dict(zip(change_tasks.keys(), change_results))

        # Postflight verification
        post_tasks: dict[str, Any] = {}
        if available["csf"]:
            post_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            post_tasks["imunify"] = _imunify_preflight(server, target=target)

        post_results = await asyncio.gather(*post_tasks.values())
        postflight = dict(zip(post_tasks.keys(), post_results))

        # Check if successfully allowlisted
        csf_success = (
            not available["csf"]
            or postflight.get("csf", {}).get("verdict") == "allowlisted"
        )
        imunify_success = (
            not available["imunify"]
            or postflight.get("imunify", {}).get("verdict") == "whitelisted"
        )

        target_ok = csf_success and imunify_success
        status = "changed" if target_ok else "error"

        if not target_ok:
            overall_ok = False

        results.append(
            {
                "target": target,
                "ok": target_ok,
                "status": status,
                "available_tools": available,
                "csf": {
                    "preflight": preflight.get("csf"),
                    "change": changes.get("csf"),
                    "postflight": postflight.get("csf"),
                }
                if available["csf"]
                else None,
                "imunify": {
                    "preflight": preflight.get("imunify"),
                    "change": changes.get("imunify"),
                    "postflight": postflight.get("imunify"),
                }
                if available["imunify"]
                else None,
            }
        )

    return {"ok": overall_ok, "available_tools": available, "results": results}


async def whm_firewall_allowlist_remove(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    reason: str,
) -> dict[str, object]:
    """
    Remove targets from allowlist/whitelist in both CSF and Imunify (based on availability).
    """
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    available = await check_firewall_binaries(server)

    if not available["csf"] and not available["imunify"]:
        return _no_firewall_tools_error()

    results: list[dict[str, object]] = []
    overall_ok = True

    for raw in targets:
        target = raw.strip()
        if not target:
            overall_ok = False
            results.append(
                {
                    "target": raw,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "Target is required",
                }
            )
            continue

        # Preflight check
        preflight_tasks: dict[str, Any] = {}
        if available["csf"]:
            preflight_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            preflight_tasks["imunify"] = _imunify_preflight(server, target=target)

        preflight_results = await asyncio.gather(*preflight_tasks.values())
        preflight = dict(zip(preflight_tasks.keys(), preflight_results))

        csf_allowlisted = (
            preflight.get("csf", {}).get("ok")
            and preflight.get("csf", {}).get("verdict") == "allowlisted"
        )
        imunify_whitelisted = (
            preflight.get("imunify", {}).get("ok")
            and preflight.get("imunify", {}).get("verdict") == "whitelisted"
        )

        if not csf_allowlisted and not imunify_whitelisted:
            # Nothing to remove
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "available_tools": available,
                    "csf": preflight.get("csf"),
                    "imunify": preflight.get("imunify"),
                }
            )
            continue

        # Execute remove operations
        change_tasks: dict[str, Any] = {}
        if csf_allowlisted:
            change_tasks["csf"] = _csf_allowlist_remove(server, target=target)
        if imunify_whitelisted:
            change_tasks["imunify"] = _imunify_whitelist_remove(server, target=target)

        change_results = await asyncio.gather(*change_tasks.values())
        changes = dict(zip(change_tasks.keys(), change_results))

        # Postflight verification
        post_tasks: dict[str, Any] = {}
        if available["csf"]:
            post_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            post_tasks["imunify"] = _imunify_preflight(server, target=target)

        post_results = await asyncio.gather(*post_tasks.values())
        postflight = dict(zip(post_tasks.keys(), post_results))

        # Check if still allowlisted (failure)
        csf_still = (
            postflight.get("csf", {}).get("ok")
            and postflight.get("csf", {}).get("verdict") == "allowlisted"
        )
        imunify_still = (
            postflight.get("imunify", {}).get("ok")
            and postflight.get("imunify", {}).get("verdict") == "whitelisted"
        )

        target_ok = not csf_still and not imunify_still
        status = "changed" if target_ok else "error"

        if not target_ok:
            overall_ok = False

        results.append(
            {
                "target": target,
                "ok": target_ok,
                "status": status,
                "available_tools": available,
                "csf": {
                    "preflight": preflight.get("csf"),
                    "change": changes.get("csf"),
                    "postflight": postflight.get("csf"),
                }
                if available["csf"]
                else None,
                "imunify": {
                    "preflight": preflight.get("imunify"),
                    "change": changes.get("imunify"),
                    "postflight": postflight.get("imunify"),
                }
                if available["imunify"]
                else None,
            }
        )

    return {"ok": overall_ok, "available_tools": available, "results": results}


async def whm_firewall_denylist_add_ttl(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    duration_minutes: int,
    reason: str,
) -> dict[str, object]:
    """
    Add targets to denylist/blacklist in both CSF and Imunify (based on availability).
    """
    if not reason.strip():
        return {
            "ok": False,
            "error_code": "reason_required",
            "message": "Reason is required",
        }
    if duration_minutes <= 0:
        return {
            "ok": False,
            "error_code": "duration_invalid",
            "message": "duration_minutes must be positive",
        }

    repo = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    available = await check_firewall_binaries(server)

    if not available["csf"] and not available["imunify"]:
        return _no_firewall_tools_error()

    duration_seconds = duration_minutes * 60
    expiration_epoch = int(time.time()) + duration_seconds

    results: list[dict[str, object]] = []
    overall_ok = True

    for raw in targets:
        target = raw.strip()
        if not target:
            overall_ok = False
            results.append(
                {
                    "target": raw,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "Target is required",
                }
            )
            continue

        # Validate IPv4 only for TTL operations
        parsed_target = parse_csf_target(target)
        if parsed_target.kind != "ip":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "invalid_target",
                    "message": "TTL denylist only supports IPv4 addresses",
                }
            )
            continue

        # Preflight check
        preflight_tasks: dict[str, Any] = {}
        if available["csf"]:
            preflight_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            preflight_tasks["imunify"] = _imunify_preflight(server, target=target)

        preflight_results = await asyncio.gather(*preflight_tasks.values())
        preflight = dict(zip(preflight_tasks.keys(), preflight_results))

        csf_already = (
            preflight.get("csf", {}).get("ok")
            and preflight.get("csf", {}).get("verdict") == "blocked"
        )
        imunify_already = (
            preflight.get("imunify", {}).get("ok")
            and preflight.get("imunify", {}).get("verdict") == "blacklisted"
        )

        # If already blocked in all available tools, no-op
        all_already = True
        if available["csf"] and not csf_already:
            all_already = False
        if available["imunify"] and not imunify_already:
            all_already = False

        if all_already:
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "available_tools": available,
                    "csf": preflight.get("csf"),
                    "imunify": preflight.get("imunify"),
                }
            )
            continue

        # Execute denylist operations
        change_tasks: dict[str, Any] = {}
        if available["csf"] and not csf_already:
            change_tasks["csf"] = _csf_denylist_add_ttl(
                server,
                target=target,
                duration_seconds=duration_seconds,
                reason=reason.strip(),
            )
        if available["imunify"] and not imunify_already:
            change_tasks["imunify"] = _imunify_blacklist_add_ttl(
                server,
                target=target,
                expiration_epoch=expiration_epoch,
                reason=reason.strip(),
            )

        change_results = await asyncio.gather(*change_tasks.values())
        changes = dict(zip(change_tasks.keys(), change_results))

        # Postflight verification
        post_tasks: dict[str, Any] = {}
        if available["csf"]:
            post_tasks["csf"] = _csf_preflight(server, target=target)
        if available["imunify"]:
            post_tasks["imunify"] = _imunify_preflight(server, target=target)

        post_results = await asyncio.gather(*post_tasks.values())
        postflight = dict(zip(post_tasks.keys(), post_results))

        # Check if successfully blocked
        csf_success = (
            not available["csf"]
            or postflight.get("csf", {}).get("verdict") == "blocked"
        )
        imunify_success = (
            not available["imunify"]
            or postflight.get("imunify", {}).get("verdict") == "blacklisted"
        )

        target_ok = csf_success and imunify_success
        status = "changed" if target_ok else "error"

        if not target_ok:
            overall_ok = False

        results.append(
            {
                "target": target,
                "ok": target_ok,
                "status": status,
                "available_tools": available,
                "csf": {
                    "preflight": preflight.get("csf"),
                    "change": changes.get("csf"),
                    "postflight": postflight.get("csf"),
                }
                if available["csf"]
                else None,
                "imunify": {
                    "preflight": preflight.get("imunify"),
                    "change": changes.get("imunify"),
                    "postflight": postflight.get("imunify"),
                }
                if available["imunify"]
                else None,
            }
        )

    return {"ok": overall_ok, "available_tools": available, "results": results}
