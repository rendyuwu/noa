from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
