from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class _CSFState:
    blocked: set[str] = field(default_factory=set)
    allowlisted: set[str] = field(default_factory=set)
    calls: list[tuple[str, ...]] = field(default_factory=list)


def _grep_output(*, target: str, blocked: bool, allowlisted: bool) -> str:
    if blocked:
        return "\n".join(
            [
                "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
                "",
                f"filter DENYIN           1        0     0 DROP       all  --  ens192 *       {target}         0.0.0.0/0",
                "",
                "ip6tables:",
                "",
                "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
                f"No matches found for {target} in ip6tables",
                "",
                f"Temporary Blocks: IP:{target} Port: Dir:in TTL:600 (NOA ttl deny test)",
            ]
        )
    if allowlisted:
        return "\n".join(
            [
                "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
                "",
                f"filter ALLOWIN          1        0     0 ACCEPT     all  --  ens192 *       {target}         0.0.0.0/0",
                "",
                f"filter ALLOWOUT         1        0     0 ACCEPT     all  --  *      ens192  0.0.0.0/0            {target}",
                "",
                "ip6tables:",
                "",
                "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
                f"No matches found for {target} in ip6tables",
                "",
                f"Temporary Allows: IP:{target} Port: Dir:inout TTL:600 (NOA ttl allow test)",
            ]
        )
    return "\n".join(
        [
            "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
            f"No matches found for {target} in iptables",
            "",
            "ip6tables:",
            "",
            "Table  Chain            num   pkts bytes target     prot opt in     out     source               destination",
            f"No matches found for {target} in ip6tables",
        ]
    )


@pytest.mark.asyncio
async def test_whm_csf_unblock_changes_when_blocked(monkeypatch) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked={"1.2.3.4"})

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
                stderr="",
                duration_ms=1,
            )
        if args[0] in {"-tr", "-dr"}:
            state.blocked.discard(args[1])
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout="ok",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_unblock(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="customer blocked",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "changed"
    assert ("-tr", "1.2.3.4") in state.calls
    assert ("-dr", "1.2.3.4") in state.calls


@pytest.mark.asyncio
async def test_whm_csf_unblock_is_noop_when_not_blocked(monkeypatch) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked=set())

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=False,
                    allowlisted=False,
                ),
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_unblock(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="customer blocked",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "no-op"
    assert state.calls == [("-g", "1.2.3.4")]


@pytest.mark.asyncio
async def test_whm_csf_allowlist_add_ttl_rejects_cidr_and_ipv6_and_converts_minutes(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState()

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
                stderr="",
                duration_ms=1,
            )
        if args[0] == "-ta":
            state.allowlisted.add(args[1])
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout="ok",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_allowlist_add_ttl(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.0/24", "2001:db8::1", "1.2.3.4"],
        duration_minutes=30,
        reason="temporary allow",
    )

    assert result["ok"] is False
    by_target = {r["target"]: r for r in result["results"]}
    assert by_target["1.2.3.0/24"]["error_code"] == "invalid_target"
    assert by_target["2001:db8::1"]["error_code"] == "invalid_target"
    assert by_target["1.2.3.4"]["status"] in {"changed", "no-op"}

    allow_calls = [call for call in state.calls if call[0] == "-ta"]
    assert len(allow_calls) == 1
    assert allow_calls[0] == ("-ta", "1.2.3.4", "1800", "temporary allow")


@pytest.mark.asyncio
async def test_whm_csf_allowlist_remove_changes_when_allowlisted(monkeypatch) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(allowlisted={"1.2.3.4"})

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
                stderr="",
                duration_ms=1,
            )
        if args[0] in {"-tra", "-ar"}:
            state.allowlisted.discard(args[1])
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout="ok",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_allowlist_remove(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="cleanup",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "changed"
    assert ("-tra", "1.2.3.4") in state.calls
    assert ("-ar", "1.2.3.4") in state.calls


@pytest.mark.asyncio
async def test_whm_csf_allowlist_remove_is_noop_when_not_allowlisted(
    monkeypatch,
) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(allowlisted=set())

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_allowlist_remove(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        reason="cleanup",
    )

    assert result["ok"] is True
    assert result["results"][0]["status"] == "no-op"
    assert state.calls == [("-g", "1.2.3.4")]


@pytest.mark.asyncio
async def test_whm_csf_denylist_add_ttl_converts_minutes(monkeypatch) -> None:
    from noa_api.whm.tools import csf_change_tools

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
    monkeypatch.setattr(
        csf_change_tools, "SQLWHMServerRepository", lambda session: repo
    )

    state = _CSFState(blocked=set())

    async def _run_csf_command(server_obj, *, args: list[str]) -> CommandResult:
        assert server_obj is server
        state.calls.append(tuple(args))
        if args[0] == "-g":
            target = args[1]
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout=_grep_output(
                    target=target,
                    blocked=target in state.blocked,
                    allowlisted=target in state.allowlisted,
                ),
                stderr="",
                duration_ms=1,
            )
        if args[0] == "-td":
            state.blocked.add(args[1])
            return CommandResult(
                command="fake",
                exit_code=0,
                stdout="ok",
                stderr="",
                duration_ms=1,
            )
        raise AssertionError(f"unexpected args: {args}")

    monkeypatch.setattr(csf_change_tools, "run_csf_command", _run_csf_command)

    result = await csf_change_tools.whm_csf_denylist_add_ttl(
        session=_Session(),
        server_ref="web1",
        targets=["1.2.3.4"],
        duration_minutes=45,
        reason="customer abuse",
    )

    assert result["ok"] is True
    deny_calls = [call for call in state.calls if call[0] == "-td"]
    assert len(deny_calls) == 1
    assert deny_calls[0] == ("-td", "1.2.3.4", "2700", "customer abuse")


async def test_whm_csf_change_tools_are_registered() -> None:
    from noa_api.core.tools.registry import get_tool_definition

    assert get_tool_definition("whm_csf_unblock") is not None
    assert get_tool_definition("whm_csf_allowlist_remove") is not None
    assert get_tool_definition("whm_csf_allowlist_add_ttl") is not None
    assert get_tool_definition("whm_csf_denylist_add_ttl") is not None
