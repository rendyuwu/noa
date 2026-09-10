"""The three validation services' branch structure (T54, V14, V82, V86-adjacent).

`test_admin_server_routes.py` owns the HTTP surface, `test_server_host_key_validation.py` owns
the trust-on-first-use rule against a real `asyncssh` server, and
`test_server_admin_repository.py` owns the transaction boundary. What is left, and lives here,
is the shape of `POST …/validate` *between* those: which probes run in which order, what a
failure at each step answers, when a pin is written, and the audit event that records it.

Everything production stays in the path except the two things that need a socket: the WHM API
client gets a `httpx.MockTransport` (`support.whm_api`), and the SSH probe is a callable the
service takes as a parameter. The real `SecretCipher`, the real `resolve_*_ssh_config` — with
its three pre-socket refusals — and the real `probe_with_trust_on_first_use` all run.

**The session discipline is asserted, not assumed.** `RecordingSessionFactory.opened` counts
transactions: one when nothing was pinned, two when a pin was stored. That is how "no pooled
connection is held across a hop" (T21's rule) is observable at all — a service holding the
request's session would open zero.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest

from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.whm.client import WHMClient, build_whm_client_from_creds
from core.integrations.whm.errors import WHMFirewallCLIError
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.types import SSHConnectionConfig
from core.secrets.crypto import SecretCipher
from core.servers.errors import (
    PMGServerNotFoundError,
    ProxmoxServerNotFoundError,
    WHMServerNotFoundError,
)
from core.servers.validation import (
    PMGServerValidationService,
    ProxmoxServerValidationService,
    WHMServerValidationService,
)
from support.rbac import RecordingAuditSink
from support.secrets import build_cipher
from support.server_admin import FakeHostKeyPinRepository, RecordingSessionFactory
from support.servers import pmg_server, proxmox_server, whm_server
from support.whm_api import (
    MYPRIVS_PATH,
    FakeWHMApi,
    myprivs_body,
    reseller_privileges,
    whm_api_failure_body,
)

CAPTURED_FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAICapturedByValidate"
STORED_FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAIAlreadyStoredPin"

VERSION_PATH = "/api2/json/version"


@pytest.fixture
def cipher() -> SecretCipher:
    return build_cipher()


def whm_api_ok() -> FakeWHMApi:
    """A WHM endpoint whose `myprivs` succeeds with a token that may suspend (§V111).

    The ACL gate itself lives in `test_whm_validate_acl_gate.py`; here the API probe is a step
    the SSH branches have to get past, so it answers the measured reseller set.
    """
    return FakeWHMApi(body=myprivs_body(reseller_privileges()))


def whm_client_factory(api: FakeWHMApi) -> Callable[..., WHMClient]:
    """A `WHMClientFactory` over `api`'s transport, keeping the real client and decrypt site."""

    def factory(server: Any, *, cipher: SecretCipher) -> WHMClient:
        return build_whm_client_from_creds(
            base_url=server.base_url,
            api_username=server.api_username,
            encrypted_token=server.api_token,
            verify_ssl=server.verify_ssl,
            cipher=cipher,
            transport=api.transport,
        )

    return factory


def recording_capture(fingerprint: str = CAPTURED_FINGERPRINT) -> Any:
    """A `HostKeyCapture` that records the config it was handed and answers `fingerprint`."""
    calls: list[SSHConnectionConfig] = []

    async def capture(config: SSHConnectionConfig) -> str:
        calls.append(config)
        return fingerprint

    capture.calls = calls  # type: ignore[attr-defined]
    return capture


def recording_probe(error: Exception | None = None) -> Any:
    """An `SSHProbe` that records the config it ran against, optionally failing."""
    calls: list[SSHConnectionConfig] = []

    async def probe(config: SSHConnectionConfig) -> None:
        calls.append(config)
        if error is not None:
            raise error

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


def build_whm_service(
    *,
    rows: list[Any],
    cipher: SecretCipher,
    api: FakeWHMApi,
    capture: Callable[[SSHConnectionConfig], Awaitable[str]] | None = None,
    probe: Callable[[SSHConnectionConfig], Awaitable[None]] | None = None,
) -> tuple[WHMServerValidationService, FakeHostKeyPinRepository, RecordingSessionFactory, Any]:
    pins = FakeHostKeyPinRepository(rows)
    factory = RecordingSessionFactory()
    audit = RecordingAuditSink()
    service = WHMServerValidationService(
        session_factory=factory,
        repository_factory=lambda _session: pins,
        cipher=cipher,
        audit_sink=audit,
        client_factory=whm_client_factory(api),
        capture_host_key=capture or recording_capture(),
        probe=probe or recording_probe(),
    )
    return service, pins, factory, audit


# --- WHM: two transports, in order ---


async def test_a_whm_validate_checks_the_api_then_ssh(cipher: SecretCipher) -> None:
    """Both transports run, and the pin is captured because the row carries none."""
    row = whm_server(
        "web16",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    api = whm_api_ok()
    probe = recording_probe()
    service, pins, factory, audit = build_whm_service(
        rows=[row], cipher=cipher, api=api, probe=probe
    )

    result = await service.validate(row.id, actor_email="admin@example.com")

    assert result.ok is True
    assert result.error_code is None
    assert [request.url.path for request in api.requests] == [MYPRIVS_PATH]
    # The probe ran against the *captured* value, not against an empty pin.
    assert [config.host_key_fingerprint for config in probe.calls] == [CAPTURED_FINGERPRINT]
    assert pins.pins == [CAPTURED_FINGERPRINT]
    # Two transactions: the read, then the pin write. Nothing was held across the hops.
    assert factory.opened == 2
    assert pins.commits == 1
    assert [event.event_type for event in audit.events] == ["whm_server_validated"]


async def test_a_failing_whm_api_check_never_reaches_ssh(cipher: SecretCipher) -> None:
    """The cheaper probe first, and its failure is the answer.

    An operator whose token is wrong should not have to read an SSH error to find that out, and
    NOA should not open a second connection to a server it already knows it cannot use.
    """
    row = whm_server(
        "web16",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    api = FakeWHMApi(body=whm_api_failure_body("Access denied"))
    probe = recording_probe()
    service, pins, factory, _ = build_whm_service(rows=[row], cipher=cipher, api=api, probe=probe)

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "whm_api_error"
    assert result.message == "Access denied"
    assert probe.calls == []
    assert pins.pins == []
    # One transaction: the read. Nothing to store, so no second one was opened.
    assert factory.opened == 1


async def test_a_whm_row_with_no_ssh_credentials_validates_on_the_api_alone(
    cipher: SecretCipher,
) -> None:
    """Right rather than lenient: a WHM server used only for account reads is legitimate.

    The firewall tools refuse it with `ssh_not_configured` when they are pointed at
    it, which names the remedy — so the validate route has nothing to add.
    """
    row = whm_server(
        "read-only",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=None,
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    probe = recording_probe()
    service, pins, factory, _ = build_whm_service(
        rows=[row], cipher=cipher, api=whm_api_ok(), probe=probe
    )

    result = await service.validate(row.id)

    assert result.ok is True
    assert probe.calls == []
    assert pins.pins == []
    assert factory.opened == 1


async def test_an_ssh_failure_after_a_capture_stores_nothing(cipher: SecretCipher) -> None:
    """The rollback branch `noa-old` needed does not exist here, by construction.

    The probe runs against the captured value rather than against the database, so a failure
    leaves no pin because none was ever written. Asserted on `pins` — the *list of writes* — so
    a service that wrote and then cleared would fail here even though the final state matches.
    """
    row = whm_server(
        "unreachable",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    service, pins, factory, audit = build_whm_service(
        rows=[row],
        cipher=cipher,
        api=whm_api_ok(),
        probe=recording_probe(SSHExecutionError(code="ssh_auth_failed", message="denied")),
    )

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "ssh_auth_failed"
    assert pins.pins == []
    assert pins.commits == 0
    assert factory.opened == 1
    assert row.ssh_host_key_fingerprint is None
    # The failure is still in the trail: "somebody tried and it did not answer" is a fact worth
    # recording, and V14 does not distinguish successes.
    assert audit.events[0].metadata["ok"] is False
    assert audit.events[0].metadata["fingerprint_captured"] is False


async def test_an_existing_pin_is_used_and_not_rewritten(cipher: SecretCipher) -> None:
    """V82's rule as a branch: a stored pin is what the probe runs under, and it stays.

    The capture must not run at all — a service that captured "just to compare" would have made
    an unpinned connection to the host, which is the thing the pin exists to avoid.
    """
    row = whm_server(
        "pinned",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=STORED_FINGERPRINT,
    )
    capture = recording_capture()
    probe = recording_probe()
    service, pins, factory, audit = build_whm_service(
        rows=[row], cipher=cipher, api=whm_api_ok(), capture=capture, probe=probe
    )

    result = await service.validate(row.id)

    assert result.ok is True
    assert capture.calls == []
    assert [config.host_key_fingerprint for config in probe.calls] == [STORED_FINGERPRINT]
    assert pins.pins == []
    assert factory.opened == 1
    assert row.ssh_host_key_fingerprint == STORED_FINGERPRINT
    assert audit.events[0].metadata["host_key_was_pinned"] is True
    assert audit.events[0].metadata["fingerprint_captured"] is False


async def test_a_mismatch_on_an_existing_pin_is_reported_not_repinned(
    cipher: SecretCipher,
) -> None:
    """The event `noa-old`'s WHM service could not report, because it re-pinned first.

    `ssh_host_key_mismatch` comes out of `ssh_exec`'s own handshake in production; here the
    probe raises it, and what is asserted is what the service does *with* it — answer, and write
    nothing.
    """
    row = whm_server(
        "rotated",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=STORED_FINGERPRINT,
    )
    service, pins, _, _ = build_whm_service(
        rows=[row],
        cipher=cipher,
        api=whm_api_ok(),
        probe=recording_probe(
            SSHExecutionError(code="ssh_host_key_mismatch", message="key changed")
        ),
    )

    result = await service.validate(row.id)

    assert result == type(result)(
        ok=False, message="key changed", error_code="ssh_host_key_mismatch"
    )
    assert pins.pins == []
    assert row.ssh_host_key_fingerprint == STORED_FINGERPRINT


async def test_a_firewall_cli_failure_is_an_answer_too(cipher: SecretCipher) -> None:
    """`WHMFirewallCLIError` is in `REMOTE_FAILURES`, so it shapes a result rather than a 502."""
    row = whm_server(
        "sudoless",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=STORED_FINGERPRINT,
    )
    service, _, _, _ = build_whm_service(
        rows=[row],
        cipher=cipher,
        api=whm_api_ok(),
        probe=recording_probe(
            WHMFirewallCLIError(code="ssh_sudo_required", message="needs sudoers")
        ),
    )

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "ssh_sudo_required"


async def test_a_whm_validate_on_an_absent_row_raises(cipher: SecretCipher) -> None:
    """The one refusal, and it is the production error class the route maps to 404."""
    service, _, _, _ = build_whm_service(rows=[], cipher=cipher, api=whm_api_ok())

    with pytest.raises(WHMServerNotFoundError):
        await service.validate(uuid4())


# --- Proxmox: one transport, no write ---


def build_proxmox_service(
    *, rows: list[Any], cipher: SecretCipher, transport: httpx.MockTransport
) -> tuple[ProxmoxServerValidationService, RecordingSessionFactory, RecordingAuditSink]:
    factory = RecordingSessionFactory()
    audit = RecordingAuditSink()

    class _Reader:
        async def get_by_id(self, server_id: Any) -> Any:
            return next((row for row in rows if row.id == server_id), None)

    def client_factory(server: Any, *, cipher: SecretCipher) -> Any:
        from core.integrations.proxmox.client import build_proxmox_client_from_creds

        return build_proxmox_client_from_creds(
            base_url=server.base_url,
            api_token_id=server.api_token_id,
            encrypted_token_secret=server.api_token_secret,
            verify_ssl=server.verify_ssl,
            cipher=cipher,
            transport=transport,
        )

    service = ProxmoxServerValidationService(
        session_factory=factory,
        repository_factory=lambda _session: _Reader(),
        cipher=cipher,
        audit_sink=audit,
        client_factory=client_factory,
    )
    return service, factory, audit


async def test_a_proxmox_validate_probes_the_version_endpoint(cipher: SecretCipher) -> None:
    """One hop, one transaction, no pin: Proxmox has no SSH path to validate (I.ext)."""
    row = proxmox_server("pve1", api_token_secret=cipher.encrypt_text("token-secret"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": {"version": "8.2"}})

    service, factory, audit = build_proxmox_service(
        rows=[row], cipher=cipher, transport=httpx.MockTransport(handler)
    )

    result = await service.validate(row.id, actor_email="admin@example.com")

    assert result.ok is True
    assert seen == [VERSION_PATH]
    assert factory.opened == 1
    assert [event.event_type for event in audit.events] == ["proxmox_server_validated"]
    assert audit.events[0].metadata["host_key_was_pinned"] is False
    assert audit.events[0].metadata["fingerprint_captured"] is False


async def test_a_refused_proxmox_token_is_an_answer(cipher: SecretCipher) -> None:
    row = proxmox_server("pve1", api_token_secret=cipher.encrypt_text("token-secret"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": {"auth": "invalid token"}})

    service, _, audit = build_proxmox_service(
        rows=[row], cipher=cipher, transport=httpx.MockTransport(handler)
    )

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code is not None
    assert audit.events[0].metadata["ok"] is False


async def test_a_proxmox_validate_on_an_absent_row_raises(cipher: SecretCipher) -> None:
    service, _, _ = build_proxmox_service(
        rows=[], cipher=cipher, transport=httpx.MockTransport(lambda _r: httpx.Response(200))
    )

    with pytest.raises(ProxmoxServerNotFoundError):
        await service.validate(uuid4())


# --- PMG: SSH only, same rule ---


def build_pmg_service(
    *,
    rows: list[Any],
    cipher: SecretCipher,
    capture: Any = None,
    probe: Any = None,
) -> tuple[PMGServerValidationService, FakeHostKeyPinRepository, RecordingSessionFactory, Any]:
    pins = FakeHostKeyPinRepository(rows)
    factory = RecordingSessionFactory()
    audit = RecordingAuditSink()
    service = PMGServerValidationService(
        session_factory=factory,
        repository_factory=lambda _session: pins,
        cipher=cipher,
        audit_sink=audit,
        capture_host_key=capture or recording_capture(),
        probe=probe or recording_probe(),
    )
    return service, pins, factory, audit


async def test_a_pmg_validate_captures_a_pin_on_first_use(cipher: SecretCipher) -> None:
    """The same rule as WHM's, from the same function — one table over."""
    row = pmg_server(
        "mail1",
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    probe = recording_probe()
    service, pins, factory, audit = build_pmg_service(rows=[row], cipher=cipher, probe=probe)

    result = await service.validate(row.id, actor_email="admin@example.com")

    assert result.ok is True
    assert [config.host_key_fingerprint for config in probe.calls] == [CAPTURED_FINGERPRINT]
    assert pins.pins == [CAPTURED_FINGERPRINT]
    assert factory.opened == 2
    assert [event.event_type for event in audit.events] == ["pmg_server_validated"]


async def test_a_pmg_row_with_no_credentials_is_named_not_probed(cipher: SecretCipher) -> None:
    """PMG has no second transport to fall back on, so an unconfigured row is the answer."""
    row = pmg_server(
        "unconfigured",
        ssh_password=None,
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    probe = recording_probe()
    service, pins, factory, _ = build_pmg_service(rows=[row], cipher=cipher, probe=probe)

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "ssh_not_configured"
    assert probe.calls == []
    assert pins.pins == []
    assert factory.opened == 1


async def test_a_row_ssh_cannot_be_built_from_is_named_before_any_socket(
    cipher: SecretCipher,
) -> None:
    """`resolve_pmg_ssh_config`'s pre-socket refusals become the validate answer.

    A stored `ssh_host` carrying a scheme is a bad *row*, not an unreachable host, and
    `ssh_invalid_host` says which — the whole reason that function refuses before opening a
    connection. Reachable even though `PMGServerCreateRequest` now rejects such a value at the
    boundary: rows written before this task, or by a fixture or script, still exist.

    Asserted on PMG rather than WHM because PMG's SSH host is a column of its own. WHM derives
    it from `base_url`, which the API probe uses first, so a `base_url` bad enough to fail
    `urlsplit(...).hostname` fails the API check one step earlier — the SSH branch is
    unreachable there, and a test that forced it would be asserting a state production cannot
    produce.
    """
    row = pmg_server(
        "broken",
        ssh_host="ssh://mail1.example.net",
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    probe = recording_probe()
    service, pins, factory, _ = build_pmg_service(rows=[row], cipher=cipher, probe=probe)

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "ssh_invalid_host"
    assert probe.calls == []
    assert pins.pins == []
    assert factory.opened == 1


async def test_a_pmgsh_failure_is_an_answer(cipher: SecretCipher) -> None:
    """`PMGSHCLIError` is in `REMOTE_FAILURES` too: a node that authenticated but could not
    read `mynetworks` reports that, rather than validating green."""
    row = pmg_server(
        "mail1",
        ssh_password=cipher.encrypt_text("ssh-password"),
        ssh_private_key=None,
        ssh_host_key_fingerprint=STORED_FINGERPRINT,
    )
    service, pins, _, _ = build_pmg_service(
        rows=[row],
        cipher=cipher,
        probe=recording_probe(
            PMGSHCLIError(code="pmgsh_command_failed", message="permission denied")
        ),
    )

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "pmgsh_command_failed"
    assert pins.pins == []


async def test_a_pmg_validate_on_an_absent_row_raises(cipher: SecretCipher) -> None:
    service, _, _, _ = build_pmg_service(rows=[], cipher=cipher)

    with pytest.raises(PMGServerNotFoundError):
        await service.validate(uuid4())


# --- V8: nothing in a validate event is a credential ---


async def test_a_validate_event_carries_no_credential(cipher: SecretCipher) -> None:
    """The trail records ids, names and two booleans about the pin. Never material."""
    secret = "ssh-password-plaintext"
    row = pmg_server(
        "mail1",
        ssh_password=cipher.encrypt_text(secret),
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    service, _, _, audit = build_pmg_service(rows=[row], cipher=cipher)

    await service.validate(row.id, actor_email="admin@example.com")

    rendered = repr(audit.events[0].metadata)
    assert secret not in rendered
    assert "enc:v1:fernet:" not in rendered
    assert set(audit.events[0].metadata) == {
        "server_id",
        "server_name",
        "ok",
        "error_code",
        "host_key_was_pinned",
        "fingerprint_captured",
    }
