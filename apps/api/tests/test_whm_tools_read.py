from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest


@dataclass
class _Server:
    id: UUID
    name: str
    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    created_at: datetime
    updated_at: datetime
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_username": self.api_username,
            "ssh_username": self.ssh_username,
            "ssh_port": self.ssh_port,
            "ssh_host_key_fingerprint": self.ssh_host_key_fingerprint,
            "has_ssh_password": self.ssh_password is not None,
            "has_ssh_private_key": self.ssh_private_key is not None,
            "verify_ssl": self.verify_ssl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class _Repo:
    def __init__(self, servers: list[_Server]) -> None:
        self._servers = servers

    async def list_servers(self) -> list[_Server]:
        return self._servers

    async def get_by_id(self, *, server_id: UUID) -> _Server | None:
        for s in self._servers:
            if s.id == server_id:
                return s
        return None


@dataclass
class _Session:
    pass


async def _check_csf_binary_true(_server) -> bool:
    return True


@pytest.mark.asyncio
async def test_whm_list_servers_excludes_api_token(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    servers = [
        _Server(
            id=uuid4(),
            name="web1",
            base_url="https://whm.example.com:2087",
            api_username="root",
            api_token="SECRET",
            verify_ssl=True,
            created_at=now,
            updated_at=now,
        )
    ]
    repo = _Repo(servers)
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    result = await read_tools.whm_list_servers(session=_Session())

    assert result["ok"] is True
    assert result["servers"][0]["name"] == "web1"
    assert "api_token" not in result["servers"][0]
    assert "SECRET" not in str(result)


@pytest.mark.asyncio
async def test_whm_list_accounts_ambiguous_server_ref_returns_choices(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    a = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://a.example.com:2087",
        api_username="root",
        api_token="A",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    b = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://b.example.com:2087",
        api_username="root",
        api_token="B",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([a, b])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    result = await read_tools.whm_list_accounts(session=_Session(), server_ref="web1")

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert len(result["choices"]) >= 2


@pytest.mark.asyncio
async def test_whm_validate_server_propagates_client_errors(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def applist(self) -> dict[str, object]:
            return {
                "ok": False,
                "error_code": "auth_failed",
                "message": "WHM authentication failed",
            }

    monkeypatch.setattr(read_tools, "WHMClient", _Client)

    result = await read_tools.whm_validate_server(session=_Session(), server_ref="web1")

    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


@pytest.mark.asyncio
async def test_whm_check_binary_exists_reports_resolved_path(monkeypatch) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = (config, command, timeout_seconds)
        return CommandResult(
            command="command -v python3",
            exit_code=0,
            stdout="/usr/bin/python3\n",
            stderr="",
            duration_ms=12,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    result = await read_tools.whm_check_binary_exists(
        session=_Session(),
        server_ref="web1",
        binary_name="python3",
    )

    assert result == {
        "ok": True,
        "binary_name": "python3",
        "found": True,
        "path": "/usr/bin/python3",
    }


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_parses_and_aggregates(
    monkeypatch,
) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    captured: dict[str, object] = {}

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = config
        captured["command"] = command
        captured["timeout_seconds"] = timeout_seconds
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="     11 acct@example.com\n     10 general@example.com\n",
            stderr="",
            duration_ms=1234,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    log_line = "lfd[12345]: (pop3d) Failed POP3 login from 203.0.113.22 (XX/Test/host-x): 30 in the last 3600 secs - Tue Mar 31 12:23:11 2026"
    result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
        top_n=50,
    )

    assert captured["timeout_seconds"] == 120.0
    assert "zgrep" in str(captured["command"])
    assert result["ok"] is True
    assert result["service"] == "pop3d"
    assert result["month"] == "Mar"
    assert result["day"] == 31
    assert result["ip"] == "203.0.113.22"
    assert result["suspects"] == [
        {"email": "acct@example.com", "count": 11},
        {"email": "general@example.com", "count": 10},
    ]


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_smtpauth_includes_exim_logs(
    monkeypatch,
) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    captured: dict[str, object] = {}

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = config
        captured["command"] = command
        captured["timeout_seconds"] = timeout_seconds
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="     3 acct@example.com\n",
            stderr="",
            duration_ms=10,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    log_line = "lfd: (smtpauth) Failed SMTP AUTH login from 198.51.100.10 (XX/Test/-): 30 in the last 3600 secs - Thu Mar 19 08:18:38 2026"
    result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
    )

    assert captured["timeout_seconds"] == 120.0
    assert "/var/log/exim_mainlog*" in str(captured["command"])
    assert result["ok"] is True
    assert result["service"] == "smtpauth"
    assert result["month"] == "Mar"
    assert result["day"] == 19
    assert result["ip"] == "198.51.100.10"


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_exit_code_1_is_empty_success(
    monkeypatch,
) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = (config, command, timeout_seconds)
        return CommandResult(
            command=command,
            exit_code=1,
            stdout="",
            stderr="",
            duration_ms=5,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    log_line = "lfd: (imapd) Failed IMAP login from 203.0.113.10 (XX/Test/-): 30 in the last 3600 secs - Thu Mar 19 08:18:38 2026"
    result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
    )

    assert result == {
        "ok": True,
        "service": "imapd",
        "month": "Mar",
        "day": 19,
        "ip": "203.0.113.10",
        "top_n": 50,
        "suspects": [],
        "raw_output": "",
        "stderr": "",
        "duration_ms": 5,
    }


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_raw_output_is_opt_in(
    monkeypatch,
) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = (config, command, timeout_seconds)
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="     2 acct@example.com\n",
            stderr="",
            duration_ms=1,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    log_line = "lfd: (imapd) Failed IMAP login from 203.0.113.10 (XX/Test/-): 30 in the last 3600 secs - Thu Mar 19 08:18:38 2026"

    default_result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
    )
    assert default_result["raw_output"] == ""

    included_result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
        include_raw_output=True,
    )
    assert included_result["raw_output"].strip() == "2 acct@example.com"


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_nonzero_exit_code_is_error(
    monkeypatch,
) -> None:
    from noa_api.core.remote_exec.types import CommandResult
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_password="SSH_PASSWORD",
        ssh_host_key_fingerprint="SHA256:known",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    async def _fake_ssh_exec(config, *, command: str, timeout_seconds: float = 20.0):
        _ = (config, command, timeout_seconds)
        return CommandResult(
            command=command,
            exit_code=2,
            stdout="",
            stderr="zgrep: /var/log/maillog*: No such file or directory\n",
            duration_ms=5,
        )

    monkeypatch.setattr(read_tools, "ssh_exec", _fake_ssh_exec)

    log_line = "lfd: (imapd) Failed IMAP login from 203.0.113.10 (XX/Test/-): 30 in the last 3600 secs - Thu Mar 19 08:18:38 2026"
    result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
    )

    assert result["ok"] is False
    assert result["error_code"] == "remote_command_failed"


@pytest.mark.asyncio
async def test_whm_mail_log_failed_auth_suspects_rejects_unsupported_service(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    log_line = "lfd[1]: (sshd) Failed login from 1.2.3.4 - Tue Mar 31 12:23:11 2026"
    result = await read_tools.whm_mail_log_failed_auth_suspects(
        session=_Session(),
        server_ref="web1",
        lfd_log_line=log_line,
    )

    assert result["ok"] is False
    assert result["error_code"] == "unsupported_service"


@pytest.mark.asyncio
async def test_whm_search_accounts_filters_results(monkeypatch) -> None:
    from noa_api.whm.tools import read_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(read_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def list_accounts(self) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "accounts": [
                    {"user": "alice", "domain": "alice.example.com"},
                    {"user": "bob", "domain": "bob.example.com"},
                ],
            }

    monkeypatch.setattr(read_tools, "WHMClient", _Client)

    result = await read_tools.whm_search_accounts(
        session=_Session(),
        server_ref="web1",
        query="ali",
    )

    assert result["ok"] is True
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["user"] == "alice"


@pytest.mark.asyncio
async def test_whm_search_accounts_rejects_non_positive_limit() -> None:
    from noa_api.whm.tools import read_tools

    result = await read_tools.whm_search_accounts(
        session=_Session(),
        server_ref="web1",
        query="alice",
        limit=0,
    )

    assert result == {
        "ok": False,
        "error_code": "limit_invalid",
        "message": "Limit must be a positive integer",
    }


@pytest.mark.asyncio
async def test_whm_preflight_account_finds_account(monkeypatch) -> None:
    from noa_api.whm.tools import preflight_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(preflight_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def list_accounts(self) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "accounts": [
                    {
                        "user": "alice",
                        "domain": "alice.example.com",
                        "suspended": 1,
                        "email": "alice@example.com",
                    },
                ],
            }

    monkeypatch.setattr(preflight_tools, "WHMClient", _Client)

    result = await preflight_tools.whm_preflight_account(
        session=_Session(),
        server_ref="web1",
        username="alice",
    )

    assert result["ok"] is True
    assert result["server_id"] == str(server.id)
    assert result["account"]["user"] == "alice"


@pytest.mark.asyncio
async def test_whm_preflight_primary_domain_change_detects_addon_conflict(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import preflight_tools

    now = datetime.now(UTC)
    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=now,
        updated_at=now,
    )
    repo = _Repo([server])
    monkeypatch.setattr(preflight_tools, "SQLWHMServerRepository", lambda session: repo)

    class _Client:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def list_accounts(self) -> dict[str, object]:
            return {
                "ok": True,
                "message": "ok",
                "accounts": [
                    {
                        "user": "alice",
                        "domain": "old.example.com",
                        "suspended": 0,
                        "email": "alice@example.com",
                    },
                ],
            }

        async def get_domain_owner(self, *, domain: str) -> dict[str, object]:
            _ = domain
            return {"ok": True, "message": "ok", "owner": "alice"}

        async def list_domains_for_account(self, *, username: str) -> dict[str, object]:
            _ = username
            return {
                "ok": True,
                "message": "ok",
                "domains": {
                    "main_domain": "old.example.com",
                    "addon_domains": ["new.example.com"],
                    "parked_domains": [],
                    "sub_domains": [],
                },
            }

    monkeypatch.setattr(preflight_tools, "WHMClient", _Client)

    result = await preflight_tools.whm_preflight_primary_domain_change(
        session=_Session(),
        server_ref="web1",
        username="alice",
        new_domain="new.example.com",
    )

    assert result == {
        "ok": False,
        "error_code": "domain_is_addon",
        "message": "Domain 'new.example.com' already exists as an addon domain on WHM account 'alice'",
    }


async def test_whm_read_and_preflight_tools_are_registered() -> None:
    from noa_api.core.tools.registry import get_tool_definition

    assert get_tool_definition("whm_list_servers") is not None
    assert get_tool_definition("whm_validate_server") is not None
    assert get_tool_definition("whm_list_accounts") is not None
    assert get_tool_definition("whm_search_accounts") is not None
    assert get_tool_definition("whm_preflight_account") is not None
    assert get_tool_definition("whm_preflight_primary_domain_change") is not None
    assert get_tool_definition("whm_preflight_firewall_entries") is not None
