from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from noa_api.core.remote_exec.types import CommandResult


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


@pytest.mark.asyncio
async def test_imunify_blacklist_remove_uses_drop_purpose(monkeypatch) -> None:
    from noa_api.whm.tools import firewall_tools

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    captured: list[list[str]] = []

    async def _run_imunify_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        captured.append(args)
        return CommandResult(
            command="fake",
            exit_code=0,
            stdout="{}",
            stderr="",
            duration_ms=1,
        )

    monkeypatch.setattr(firewall_tools, "run_imunify_command", _run_imunify_command)

    result = await firewall_tools._imunify_blacklist_remove(server, target="1.2.3.4")

    assert result == {"ok": True, "status": "changed"}
    assert captured == [
        [
            "ip-list",
            "local",
            "delete",
            "--purpose",
            "drop",
            "1.2.3.4",
            "--json",
        ]
    ]


@pytest.mark.asyncio
async def test_whm_preflight_firewall_entries_returns_receipt_ready_data(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import firewall_tools

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    monkeypatch.setattr(
        firewall_tools, "SQLWHMServerRepository", lambda session: object()
    )

    async def _resolve(server_ref: str, *, repo) -> SimpleNamespace:
        assert server_ref == "web1"
        _ = repo
        return SimpleNamespace(ok=True, server=server, server_id=server.id)

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        assert args == ["-g", "130.103.21.22"]
        raw_output = (
            "Table  Chain\n\n"
            "Temporary Blocks: IP:130.103.21.22 Port: Dir:in TTL:432000"
            " (osTicket #121312)"
        )
        return CommandResult(
            command="fake-csf",
            exit_code=0,
            stdout=raw_output,
            stderr="",
            duration_ms=1,
        )

    async def _run_imunify_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        assert args == [
            "ip-list",
            "local",
            "list",
            "--by-ip",
            "130.103.21.22",
            "--json",
        ]
        payload = {
            "items": [
                {
                    "ip": "130.103.21.22",
                    "purpose": "drop",
                    "expiration": 1775225644,
                    "comment": "osTicket #121312",
                    "manual": True,
                    "country": {"code": "US", "name": "United States"},
                }
            ],
            "counts": {},
        }
        return CommandResult(
            command="fake-imunify",
            exit_code=0,
            stdout=json.dumps(payload),
            stderr="",
            duration_ms=1,
        )

    monkeypatch.setattr(firewall_tools, "resolve_whm_server_ref", _resolve)

    async def _check_firewall_binaries(server_obj) -> dict[str, bool]:
        assert server_obj is server
        return {"csf": True, "imunify": True}

    monkeypatch.setattr(
        firewall_tools,
        "check_firewall_binaries",
        _check_firewall_binaries,
    )
    monkeypatch.setattr(firewall_tools, "run_csf_command", _run_csf_command)
    monkeypatch.setattr(firewall_tools, "run_imunify_command", _run_imunify_command)

    result = await firewall_tools.whm_preflight_firewall_entries(
        session=object(),
        server_ref="web1",
        target="130.103.21.22",
    )

    assert result["ok"] is True
    assert result["server_id"] == str(server.id)
    assert result["combined_verdict"] == "blocked"
    csf = result["csf"]
    imunify = result["imunify"]
    assert isinstance(csf, dict)
    assert isinstance(imunify, dict)
    assert csf["raw_output"].endswith("(osTicket #121312)")
    assert imunify["entries"][0]["comment"] == "osTicket #121312"
    assert imunify["raw_data"]["items"][0]["purpose"] == "drop"


@pytest.mark.asyncio
async def test_whm_preflight_firewall_entries_rejects_invalid_target(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import firewall_tools

    server = _Server(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    monkeypatch.setattr(
        firewall_tools, "SQLWHMServerRepository", lambda session: object()
    )

    async def _resolve(server_ref: str, *, repo) -> SimpleNamespace:
        _ = server_ref, repo
        return SimpleNamespace(ok=True, server=server, server_id=server.id)

    monkeypatch.setattr(firewall_tools, "resolve_whm_server_ref", _resolve)

    async def _should_not_run(*args, **kwargs) -> dict[str, object]:
        raise AssertionError("firewall commands should not run for invalid targets")

    async def _check_firewall_binaries(server_obj) -> dict[str, bool]:
        assert server_obj is server
        return {"csf": True, "imunify": True}

    monkeypatch.setattr(
        firewall_tools,
        "check_firewall_binaries",
        _check_firewall_binaries,
    )
    monkeypatch.setattr(firewall_tools, "run_csf_command", _should_not_run)
    monkeypatch.setattr(firewall_tools, "run_imunify_command", _should_not_run)

    result = await firewall_tools.whm_preflight_firewall_entries(
        session=object(),
        server_ref="web1",
        target="bad_target",
    )

    assert result == {
        "ok": False,
        "error_code": "invalid_target",
        "message": "Target must be a valid IP, CIDR, or hostname",
    }
