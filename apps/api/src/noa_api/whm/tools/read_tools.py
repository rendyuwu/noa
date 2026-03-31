from __future__ import annotations

import ipaddress
import re
import shlex
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.remote_exec.ssh import SSHExecutionError, command_from_argv, ssh_exec
from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.storage.postgres.whm_servers import SQLWHMServerRepository
from noa_api.whm.integrations.client import WHMClient
from noa_api.whm.integrations.ssh import resolve_whm_ssh_config
from noa_api.whm.server_ref import resolve_whm_server_ref
from noa_api.whm.tools.result_shapes import normalize_whm_account_summary

_BINARY_NAME_RE = re.compile(r"^[A-Za-z0-9._+-]{1,128}$")

_ALLOWED_LFD_AUTH_SERVICES = {"smtpauth", "imapd", "pop3d"}

_LFD_SERVICE_RE = re.compile(
    r"\blfd(?:\[\d+\])?:\s*\((?P<service>[A-Za-z0-9_-]+)\)",
    re.IGNORECASE,
)
_LFD_DATE_RE = re.compile(
    r"\b(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(?P<day>\d{1,2})\s+\d{2}:\d{2}:\d{2}\s+\d{4}\b"
)
_LFD_FROM_IP_RE = re.compile(r"\bfrom\s+(?P<ip>\S+)\b")

_MAILLOG_TIMEOUT_SECONDS = 120.0


def _resolution_error(result: Any) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": str(getattr(result, "error_code", None) or "unknown"),
        "message": str(getattr(result, "message", "")),
        "choices": list(getattr(result, "choices", []) or []),
    }


def _client_for_server(server: Any) -> WHMClient:
    return WHMClient(
        base_url=str(getattr(server, "base_url")),
        api_username=str(getattr(server, "api_username")),
        api_token=maybe_decrypt_text(str(getattr(server, "api_token"))),
        verify_ssl=bool(getattr(server, "verify_ssl")),
    )


async def whm_list_servers(*, session: AsyncSession) -> dict[str, object]:
    repo = SQLWHMServerRepository(session)
    servers = await repo.list_servers()
    return {
        "ok": True,
        "servers": [server.to_safe_dict() for server in servers],
    }


async def whm_validate_server(
    *, session: AsyncSession, server_ref: str
) -> dict[str, object]:
    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    client = _client_for_server(server)
    result = await client.applist()
    if result.get("ok") is True:
        return {"ok": True, "message": "ok"}
    return {
        "ok": False,
        "error_code": str(result.get("error_code") or "unknown"),
        "message": str(result.get("message") or "WHM validation failed"),
    }


async def whm_list_accounts(
    *, session: AsyncSession, server_ref: str
) -> dict[str, object]:
    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None
    client = _client_for_server(server)
    result = await client.list_accounts()
    if result.get("ok") is not True:
        return {
            "ok": False,
            "error_code": str(result.get("error_code") or "unknown"),
            "message": str(result.get("message") or "WHM list accounts failed"),
        }
    accounts = result.get("accounts")
    return {
        "ok": True,
        "accounts": _normalize_account_list(accounts),
    }


async def whm_search_accounts(
    *,
    session: AsyncSession,
    server_ref: str,
    query: str,
    limit: int = 20,
) -> dict[str, object]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return {
            "ok": False,
            "error_code": "query_required",
            "message": "Query is required",
        }
    if limit <= 0:
        return {
            "ok": False,
            "error_code": "limit_invalid",
            "message": "Limit must be a positive integer",
        }

    listed = await whm_list_accounts(session=session, server_ref=server_ref)
    if listed.get("ok") is not True:
        return listed

    raw_accounts = listed.get("accounts")
    accounts = raw_accounts if isinstance(raw_accounts, list) else []

    matches: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        user = account.get("user")
        domain = account.get("domain")
        haystack = " ".join(
            [
                str(user) if user is not None else "",
                str(domain) if domain is not None else "",
            ]
        ).lower()
        if normalized_query in haystack:
            matches.append(account)
        if limit > 0 and len(matches) >= limit:
            break

    return {"ok": True, "accounts": matches, "query": query}


async def whm_check_binary_exists(
    *, session: AsyncSession, server_ref: str, binary_name: str
) -> dict[str, object]:
    normalized_binary_name = binary_name.strip()
    if not _BINARY_NAME_RE.fullmatch(normalized_binary_name):
        return {
            "ok": False,
            "error_code": "invalid_binary_name",
            "message": "Binary name must contain only letters, numbers, dot, underscore, plus, or dash",
        }

    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        result = await ssh_exec(
            ssh_config,
            command=f"command -v {normalized_binary_name}",
        )
    except SSHExecutionError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }

    if result.exit_code != 0:
        return {
            "ok": True,
            "binary_name": normalized_binary_name,
            "found": False,
            "path": None,
        }

    path = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
    return {
        "ok": True,
        "binary_name": normalized_binary_name,
        "found": path is not None,
        "path": path,
    }


def _parse_lfd_auth_block_line(line: str) -> dict[str, object]:
    normalized = line.strip()
    if not normalized:
        return {
            "ok": False,
            "error_code": "log_line_required",
            "message": "LFD log line is required",
        }

    service_match = _LFD_SERVICE_RE.search(normalized)
    if not service_match:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Could not parse LFD service from log line",
        }

    service = (service_match.group("service") or "").strip().lower()
    if service not in _ALLOWED_LFD_AUTH_SERVICES:
        return {
            "ok": False,
            "error_code": "unsupported_service",
            "message": "This tool only supports LFD auth blocks for smtpauth, imapd, or pop3d",
        }

    date_match = _LFD_DATE_RE.search(normalized)
    if not date_match:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Could not parse month/day from log line",
        }
    month = (date_match.group("month") or "").strip()
    day_raw = (date_match.group("day") or "").strip()
    try:
        day = int(day_raw)
    except ValueError:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Could not parse day from log line",
        }
    if day < 1 or day > 31:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Parsed day is out of range",
        }

    ip_match = _LFD_FROM_IP_RE.search(normalized)
    if not ip_match:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Could not parse IP address from log line",
        }
    ip_raw = (ip_match.group("ip") or "").strip()
    try:
        ip_obj = ipaddress.ip_address(ip_raw)
    except ValueError:
        return {
            "ok": False,
            "error_code": "log_line_unparseable",
            "message": "Parsed IP address is not a valid IP address",
        }

    return {
        "ok": True,
        "service": service,
        "month": month,
        "day": day,
        "ip": ip_raw,
        "ip_version": ip_obj.version,
    }


async def whm_mail_log_failed_auth_suspects(
    *,
    session: AsyncSession,
    server_ref: str,
    lfd_log_line: str,
    top_n: int = 50,
    include_raw_output: bool = False,
) -> dict[str, object]:
    """Search maillog for failed auth attempts and summarize usernames.

    Hard guard: only accepts LFD log lines for smtpauth, imapd, or pop3d blocks.
    """

    if top_n <= 0 or top_n > 200:
        return {
            "ok": False,
            "error_code": "top_n_invalid",
            "message": "top_n must be between 1 and 200",
        }

    parsed = _parse_lfd_auth_block_line(lfd_log_line)
    if parsed.get("ok") is not True:
        return parsed

    service = str(parsed.get("service"))
    month = str(parsed.get("month"))
    day = int(parsed.get("day"))
    ip = str(parsed.get("ip"))
    ip_version = int(parsed.get("ip_version") or 4)

    repo: Any = SQLWHMServerRepository(session)
    resolution = await resolve_whm_server_ref(server_ref, repo=repo)
    if not resolution.ok:
        return _resolution_error(resolution)

    server = resolution.server
    assert server is not None

    date_anchor_re = f"^{month}[[:space:]]+{day}[[:space:]]"

    ip_escaped = re.escape(ip)
    if ip_version == 4:
        # Avoid substring matches (e.g., 1.2.3.4 should not match 11.2.3.45)
        ip_match_cmd = f"grep -E {shlex.quote(rf'(^|[^0-9]){ip_escaped}([^0-9]|$)')}"
    else:
        # IPv6 addresses are distinctive; fixed-string match is typically sufficient.
        ip_match_cmd = f"grep -F {shlex.quote(ip)}"

    failed_auth_re = "Failed|auth failed|Authentication failed|authenticator failed|password mismatch"

    awk_program = r"""
{
  if (match($0, /user=<[^>]+>/)) {
    s = substr($0, RSTART, RLENGTH);
    sub(/^user=</, "", s);
    sub(/>$/, "", s);
    if (s != "") print s;
    next;
  }
  if (match($0, /sasl_username=[^ ,]+/)) {
    s = substr($0, RSTART, RLENGTH);
    sub(/^sasl_username=/, "", s);
    if (s != "") print s;
    next;
  }
  if (match($0, /A=dovecot_login:[^ ]+/)) {
    s = substr($0, RSTART, RLENGTH);
    sub(/^A=dovecot_login:/, "", s);
    if (s != "") print s;
    next;
  }
  if (match($0, /authenticator failed for \([^)]*\)/)) {
    s = substr($0, RSTART, RLENGTH);
    sub(/^authenticator failed for \(/, "", s);
    sub(/\)$/, "", s);
    if (s != "") print s;
    next;
  }
}
""".strip()

    # NOTE: This may take a while on servers with large log histories.
    # We intentionally avoid `head` in the pipeline (SIGPIPE) and truncate locally.
    log_globs = ["/var/log/maillog*"]
    if service == "smtpauth":
        # cPanel typically logs SMTP AUTH failures in Exim logs.
        log_globs.append("/var/log/exim_mainlog*")

    # Intentionally unquoted so bash can expand globs.
    globs_expr = " ".join(log_globs)

    script = (
        "set -o pipefail; "
        "shopt -s nullglob; "
        f"files=({globs_expr}); "
        "if [ ${#files[@]} -eq 0 ]; then echo 'No matching mail log files found' >&2; exit 3; fi; "
        f'zgrep -h -E {shlex.quote(date_anchor_re)} "${{files[@]}}" '
        f"| {ip_match_cmd} "
        f"| grep -iE {shlex.quote(failed_auth_re)} "
        f"| awk {shlex.quote(awk_program)} "
        "| sort "
        "| uniq -c "
        "| sort -nr"
    )
    command = command_from_argv(["bash", "-lc", script])

    try:
        ssh_config = resolve_whm_ssh_config(
            server,
            require_host_key_fingerprint=True,
        )
        result = await ssh_exec(
            ssh_config,
            command=command,
            timeout_seconds=_MAILLOG_TIMEOUT_SECONDS,
        )
    except SSHExecutionError as exc:
        return {
            "ok": False,
            "error_code": exc.code,
            "message": exc.message,
        }

    # grep/zgrep return 1 when there are no matches; treat as an empty result.
    if result.exit_code not in (0, 1):
        stderr = result.stderr.strip()
        message = f"Remote command failed with exit code {result.exit_code}"
        if stderr:
            message = f"{message}: {stderr.splitlines()[0]}"
        return {
            "ok": False,
            "error_code": "remote_command_failed",
            "message": message,
        }

    lines = result.stdout.strip("\n").splitlines() if result.stdout else []
    truncated_lines = lines[:top_n]
    raw_output = "\n".join(truncated_lines) if include_raw_output else ""
    suspects: list[dict[str, object]] = []
    for line in truncated_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        count_raw, email = parts
        try:
            count = int(count_raw)
        except ValueError:
            continue
        normalized_email = email.strip()
        if not normalized_email:
            continue
        suspects.append({"email": normalized_email, "count": count})

    return {
        "ok": True,
        "service": service,
        "month": month,
        "day": day,
        "ip": ip,
        "top_n": top_n,
        "suspects": suspects,
        "raw_output": raw_output,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
    }


def _normalize_account_list(accounts: object) -> list[dict[str, object]]:
    if not isinstance(accounts, list):
        return []

    normalized_accounts: list[dict[str, object]] = []
    for account in accounts:
        normalized = normalize_whm_account_summary(account)
        if normalized is not None:
            normalized_accounts.append(normalized)
    return normalized_accounts
