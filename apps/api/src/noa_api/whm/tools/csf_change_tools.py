from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.csf import parse_csf_grep_output, parse_csf_target
from noa_api.whm.integrations.csf_cli import (
    CSFCLIError,
    require_csf_success,
    run_csf_command,
)
from noa_api.whm.integrations.imunify_cli import check_csf_binary
from noa_api.whm.server_ref import resolve_whm_server_ref


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


async def _csf_preflight(client: Any, *, target: str) -> tuple[str, list[str]]:
    grep_result = await run_csf_command(client, args=["-g", target])
    output = require_csf_success(grep_result, default_message="CSF grep failed")
    if not output.strip():
        raise RuntimeError("CSF grep returned an invalid response")
    parsed = parse_csf_grep_output(output, target=target)
    return parsed.verdict, parsed.matches


async def _run_csf_change(
    server: Any,
    *,
    args: list[str],
    default_message: str,
) -> None:
    result = await run_csf_command(server, args=args)
    require_csf_success(result, default_message=default_message)


async def whm_csf_unblock(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    reason: str,
) -> dict[str, object]:
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

    # Check CSF binary availability
    if not await check_csf_binary(server):
        return {
            "ok": False,
            "error_code": "csf_not_available",
            "message": "CSF is not installed on this server",
        }

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

        try:
            verdict, matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "preflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if verdict != "blocked":
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "verdict": verdict,
                    "matches": matches,
                }
            )
            continue

        try:
            await _run_csf_change(
                server,
                args=["-tr", target],
                default_message="Unblock failed",
            )
            await _run_csf_change(
                server,
                args=["-dr", target],
                default_message="Unblock failed",
            )
        except CSFCLIError as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                }
            )
            continue

        try:
            post_verdict, post_matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if post_verdict == "blocked":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": "Target still appears blocked after unblock",
                    "matches": post_matches,
                }
            )
            continue

        results.append(
            {
                "target": target,
                "ok": True,
                "status": "changed",
                "verdict": post_verdict,
                "matches": post_matches,
            }
        )

    return {"ok": overall_ok, "results": results}


async def whm_csf_allowlist_remove(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    reason: str,
) -> dict[str, object]:
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

    # Check CSF binary availability
    if not await check_csf_binary(server):
        return {
            "ok": False,
            "error_code": "csf_not_available",
            "message": "CSF is not installed on this server",
        }

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

        try:
            verdict, matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "preflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if verdict != "allowlisted":
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "verdict": verdict,
                    "matches": matches,
                }
            )
            continue

        try:
            await _run_csf_change(
                server,
                args=["-tra", target],
                default_message="Allowlist remove failed",
            )
            await _run_csf_change(
                server,
                args=["-ar", target],
                default_message="Allowlist remove failed",
            )
        except CSFCLIError as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                }
            )
            continue

        try:
            post_verdict, post_matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if post_verdict == "allowlisted":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": "Target still appears allowlisted after removal",
                    "matches": post_matches,
                }
            )
            continue

        results.append(
            {
                "target": target,
                "ok": True,
                "status": "changed",
                "verdict": post_verdict,
                "matches": post_matches,
            }
        )

    return {"ok": overall_ok, "results": results}


async def whm_csf_allowlist_add_ttl(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    duration_minutes: int,
    reason: str,
) -> dict[str, object]:
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

    # Check CSF binary availability
    if not await check_csf_binary(server):
        return {
            "ok": False,
            "error_code": "csf_not_available",
            "message": "CSF is not installed on this server",
        }

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

        try:
            verdict, matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "preflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if verdict == "allowlisted":
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "verdict": verdict,
                    "matches": matches,
                }
            )
            continue

        duration_seconds = duration_minutes * 60

        try:
            await _run_csf_change(
                server,
                args=["-ta", target, str(duration_seconds), reason.strip()],
                default_message="Allowlist TTL failed",
            )
        except CSFCLIError as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                }
            )
            continue

        try:
            post_verdict, post_matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if post_verdict != "allowlisted":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": "Target did not appear allowlisted after TTL add",
                    "matches": post_matches,
                }
            )
            continue

        results.append(
            {
                "target": target,
                "ok": True,
                "status": "changed",
                "verdict": post_verdict,
                "matches": post_matches,
            }
        )

    return {"ok": overall_ok, "results": results}


async def whm_csf_denylist_add_ttl(
    *,
    session: AsyncSession,
    server_ref: str,
    targets: list[str],
    duration_minutes: int,
    reason: str,
) -> dict[str, object]:
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

    # Check CSF binary availability
    if not await check_csf_binary(server):
        return {
            "ok": False,
            "error_code": "csf_not_available",
            "message": "CSF is not installed on this server",
        }

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

        try:
            verdict, matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "preflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if verdict == "blocked":
            results.append(
                {
                    "target": target,
                    "ok": True,
                    "status": "no-op",
                    "verdict": verdict,
                    "matches": matches,
                }
            )
            continue

        duration_seconds = duration_minutes * 60

        try:
            await _run_csf_change(
                server,
                args=["-td", target, str(duration_seconds), reason.strip()],
                default_message="Denylist TTL failed",
            )
        except CSFCLIError as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": exc.code,
                    "message": exc.message,
                }
            )
            continue

        try:
            post_verdict, post_matches = await _csf_preflight(server, target=target)
        except Exception as exc:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": str(exc),
                }
            )
            continue

        if post_verdict != "blocked":
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": "postflight_failed",
                    "message": "Target did not appear blocked after TTL deny",
                    "matches": post_matches,
                }
            )
            continue

        results.append(
            {
                "target": target,
                "ok": True,
                "status": "changed",
                "verdict": post_verdict,
                "matches": post_matches,
            }
        )

    return {"ok": overall_ok, "results": results}
