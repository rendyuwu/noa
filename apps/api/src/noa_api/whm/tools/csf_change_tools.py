from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.client import WHMClient
from noa_api.whm.integrations.csf import parse_csf_grep_html, parse_csf_target
from noa_api.whm.server_ref import resolve_whm_server_ref


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


async def _csf_preflight(client: Any, *, target: str) -> tuple[str, list[str]]:
    grep_result = await client.csf_grep(target=target)
    if grep_result.get("ok") is not True:
        raise RuntimeError(str(grep_result.get("message") or "CSF grep failed"))
    html_value = grep_result.get("html")
    if not isinstance(html_value, str):
        raise RuntimeError("CSF grep returned an invalid response")
    parsed = parse_csf_grep_html(html_value, target=target)
    return parsed.verdict, parsed.matches


def _client_for_server(server: Any) -> WHMClient:
    return WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=str(getattr(server, "api_token")),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


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
    client = _client_for_server(server)

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
            verdict, matches = await _csf_preflight(client, target=target)
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

        mutation = await client.csf_request_action(
            action="unblock",
            params={"target": target, "reason": reason.strip()},
        )
        if mutation.get("ok") is not True:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": str(mutation.get("error_code") or "unknown"),
                    "message": str(mutation.get("message") or "Unblock failed"),
                }
            )
            continue

        try:
            post_verdict, post_matches = await _csf_preflight(client, target=target)
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
    client = _client_for_server(server)

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

        verdict, matches = await _csf_preflight(client, target=target)
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

        mutation = await client.csf_request_action(
            action="allow_remove",
            params={"target": target, "reason": reason.strip()},
        )
        if mutation.get("ok") is not True:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": str(mutation.get("error_code") or "unknown"),
                    "message": str(
                        mutation.get("message") or "Allowlist remove failed"
                    ),
                }
            )
            continue

        post_verdict, post_matches = await _csf_preflight(client, target=target)
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
    client = _client_for_server(server)

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

        verdict, matches = await _csf_preflight(client, target=target)
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

        mutation = await client.csf_request_action(
            action="allow_ttl",
            params={
                "target": target,
                "timeout": duration_minutes,
                "dur": "m",
                "reason": reason.strip(),
            },
        )
        if mutation.get("ok") is not True:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": str(mutation.get("error_code") or "unknown"),
                    "message": str(mutation.get("message") or "Allowlist TTL failed"),
                }
            )
            continue

        post_verdict, post_matches = await _csf_preflight(client, target=target)
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
    client = _client_for_server(server)

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

        verdict, matches = await _csf_preflight(client, target=target)
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

        mutation = await client.csf_request_action(
            action="deny_ttl",
            params={
                "target": target,
                "timeout": duration_minutes,
                "dur": "m",
                "reason": reason.strip(),
            },
        )
        if mutation.get("ok") is not True:
            overall_ok = False
            results.append(
                {
                    "target": target,
                    "ok": False,
                    "status": "error",
                    "error_code": str(mutation.get("error_code") or "unknown"),
                    "message": str(mutation.get("message") or "Denylist TTL failed"),
                }
            )
            continue

        post_verdict, post_matches = await _csf_preflight(client, target=target)
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
