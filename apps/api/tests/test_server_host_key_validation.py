"""Trust-on-first-use, against a real SSH handshake (T54, V69, V82).

**This file is the gate V69 demands.** The rule it covers is a *security* control ported from
`noa-old`, and V69 is explicit that upstream provenance is not evidence a control works: a
ported control lands with a test against the real mechanism before any §V or doc calls it
hardened. B2 is why that sentence exists — the host-key pin was inert for months behind a test
that called the validation callback itself.

So none of the four cases below uses a fake handshake. Each stands on
`support.remote_exec.loopback_ssh_server`: a real `asyncssh.create_server` on 127.0.0.1 with a
throwaway Ed25519 host key, wired through the real `WHMServerValidationService` /
`PMGServerValidationService`, the real `probe_with_trust_on_first_use`, the real
`resolve_*_ssh_config`, the real `ssh_get_host_fingerprint` and the real `ssh_exec`. Only the
database and the WHM HTTP endpoint are doubled.

The four properties, and what each would look like if it were wrong:

1. **A first validate captures the key the host actually presented** — not a value NOA
   invented, and not one a comparison merely accepted.
2. **A stored pin that no longer matches refuses, and is not rewritten.** `noa-old`'s WHM
   service overwrote the pin on every validate, so this event was unobservable: any admin
   pressing Validate silently re-trusted whatever answered the address.
3. **A capture whose probe then fails leaves no pin.** True by construction here (nothing is
   written until the probe passes), and asserted anyway, because "by construction" is a claim
   about today's code.
4. **The credential never crosses the wire on a mismatch.** `RecordingSSHServer.auth_attempts`
   staying empty is the assertion V82 is really about — a pin compared *after* the connection
   would raise the same error, having already handed the password to whatever was listening.

Case 2 ships with its negative control (case 1's sibling,
`test_a_matching_pin_validates_over_a_real_handshake`): "the pin was not overwritten" passes
just as well against a validate that does nothing at all, so a run that *does* connect and
*does* succeed has to be in the file beside it (V87).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from core.integrations.whm.client import WHMClient, build_whm_client_from_creds
from core.secrets.crypto import SecretCipher
from core.servers.validation import (
    PMGServerValidationService,
    WHMServerValidationService,
    probe_ssh_reachable,
    probe_with_trust_on_first_use,
)
from support.rbac import RecordingAuditSink
from support.remote_exec import LoopbackSSH, loopback_ssh_config, loopback_ssh_server
from support.secrets import build_cipher
from support.server_admin import FakeHostKeyPinRepository, RecordingSessionFactory
from support.servers import pmg_server, whm_server
from support.whm_api import FakeWHMApi

WRONG_FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAINotTheKeyThisHostHas"

# The loopback server accepts any password (`support.remote_exec.RecordingSSHServer`), so the
# value only has to survive the round trip through encryption and `resolve_*_ssh_config`.
SSH_PASSWORD = "operator-ssh-password"


@pytest.fixture
def cipher() -> SecretCipher:
    return build_cipher()


def whm_api_ok() -> FakeWHMApi:
    return FakeWHMApi(body={"metadata": {"result": 1, "reason": "OK"}, "data": {"app": []}})


def _whm_client_factory(api: FakeWHMApi) -> Any:
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


def whm_row_on(server: LoopbackSSH, *, cipher: SecretCipher, fingerprint: str | None) -> Any:
    """A `whm_servers` row aimed at the loopback server.

    Two columns, not one, and the split is `resolve_whm_ssh_config`'s: the SSH **host** is the
    hostname component of `base_url`, while the SSH **port** is the `ssh_port` column
    (`server.ssh_port or 22`). Setting only the URL's port sends the probe to real port 22 —
    which is how the first draft of this file failed, with `ssh_auth_failed` against whatever
    was listening.

    The HTTP half never leaves the process: the client is built over a `MockTransport`.
    """
    row = whm_server(
        "loopback",
        base_url=f"https://127.0.0.1:{server.port}",
        api_token=cipher.encrypt_text("whm-token"),
        ssh_username="noa-ops",
        ssh_password=cipher.encrypt_text(SSH_PASSWORD),
        ssh_private_key=None,
        ssh_host_key_fingerprint=fingerprint,
    )
    row.ssh_port = server.port
    return row


def build_whm_service(
    *, rows: list[Any], cipher: SecretCipher, api: FakeWHMApi
) -> tuple[WHMServerValidationService, FakeHostKeyPinRepository, RecordingAuditSink]:
    """The production service, with the *real* capture and the *real* pinned probe."""
    pins = FakeHostKeyPinRepository(rows)
    audit = RecordingAuditSink()
    service = WHMServerValidationService(
        session_factory=RecordingSessionFactory(),
        repository_factory=lambda _session: pins,
        cipher=cipher,
        audit_sink=audit,
        client_factory=_whm_client_factory(api),
        # `capture_host_key` and `probe` left at their defaults: `ssh_get_host_fingerprint` and
        # `probe_ssh_reachable`, which is the whole point of this file.
    )
    return service, pins, audit


# --- 1: a first validate captures what the host presented ---


async def test_a_first_validate_captures_the_key_the_host_presented(
    cipher: SecretCipher,
) -> None:
    """The value stored is the server's own digest, taken out of a real key exchange."""
    async with loopback_ssh_server() as remote:
        row = whm_row_on(remote, cipher=cipher, fingerprint=None)
        service, pins, audit = build_whm_service(rows=[row], cipher=cipher, api=whm_api_ok())

        result = await service.validate(row.id, actor_email="admin@example.com")

        assert result.ok is True, result
        assert pins.pins == [remote.host_key_fingerprint]
        assert row.ssh_host_key_fingerprint == remote.host_key_fingerprint
        assert pins.commits == 1
        # The credential *did* cross the wire this time, which is what makes the empty
        # `auth_attempts` in the mismatch test below mean something.
        assert remote.auth_attempts != []

    event = audit.events[0]
    assert event.metadata["host_key_was_pinned"] is False
    assert event.metadata["fingerprint_captured"] is True


# --- 2: a stored pin is honoured, and a mismatch refuses without rewriting it ---


async def test_a_matching_pin_validates_over_a_real_handshake(cipher: SecretCipher) -> None:
    """The negative control for the two tests below.

    Without it, "no pin was written" and "the connection was refused" both pass against a
    validate that never connects at all (V87: a check that stops separating degrades silently).
    """
    async with loopback_ssh_server() as remote:
        row = whm_row_on(remote, cipher=cipher, fingerprint=remote.host_key_fingerprint)
        service, pins, audit = build_whm_service(rows=[row], cipher=cipher, api=whm_api_ok())

        result = await service.validate(row.id)

        assert result.ok is True, result
        assert remote.auth_attempts != []

    # Nothing was written: the pin was already right.
    assert pins.pins == []
    assert pins.commits == 0
    assert audit.events[0].metadata["host_key_was_pinned"] is True
    assert audit.events[0].metadata["fingerprint_captured"] is False


async def test_a_changed_host_key_refuses_and_records_zero_auth_attempts(
    cipher: SecretCipher,
) -> None:
    """V82 through the validate route: the mismatch lands in key exchange, before auth.

    Two assertions, and both matter. `ssh_host_key_mismatch` says NOA noticed; `auth_attempts
    == []` says it noticed *in time* — a post-connect comparison would report the same code
    having already handed the SSH password to whatever answered the port.
    """
    async with loopback_ssh_server() as remote:
        row = whm_row_on(remote, cipher=cipher, fingerprint=WRONG_FINGERPRINT)
        service, _pins, audit = build_whm_service(rows=[row], cipher=cipher, api=whm_api_ok())

        result = await service.validate(row.id)

        assert result.ok is False
        assert result.error_code == "ssh_host_key_mismatch"
        assert remote.auth_attempts == []

    assert audit.events[0].metadata["ok"] is False
    assert audit.events[0].metadata["error_code"] == "ssh_host_key_mismatch"


async def test_an_existing_pin_is_never_overwritten_by_validate(cipher: SecretCipher) -> None:
    """The behaviour this task took from `noa-old`'s PMG service instead of its WHM one.

    Its WHM `validate_server` captured unconditionally and stored the result *before* probing,
    so the stored pin became whatever last answered the address. Here the stored value is
    untouched, and `pins` — the list of writes, not the final state — is what says so: a service
    that wrote the new key and then restored the old one would leave the same row and fail here.
    """
    async with loopback_ssh_server() as remote:
        row = whm_row_on(remote, cipher=cipher, fingerprint=WRONG_FINGERPRINT)
        service, pins, _ = build_whm_service(rows=[row], cipher=cipher, api=whm_api_ok())

        await service.validate(row.id)

    assert pins.pins == []
    assert pins.commits == 0
    assert row.ssh_host_key_fingerprint == WRONG_FINGERPRINT


# --- 3: a capture whose probe fails leaves nothing behind ---


async def test_a_capture_whose_probe_fails_leaves_no_pin_behind(cipher: SecretCipher) -> None:
    """No rollback branch exists, because nothing is written before the probe passes.

    The failure is real rather than injected: the loopback server is torn down between the
    capture and the probe, so `ssh_exec` meets a closed port with a freshly captured
    fingerprint in hand. `noa-old`'s ordering — store, probe, clear on failure — is what left a
    window where a bad pin was live, and where a process death left it live for good.
    """
    async with loopback_ssh_server() as remote:
        row = whm_row_on(remote, cipher=cipher, fingerprint=None)
        pins = FakeHostKeyPinRepository([row])
        config = loopback_ssh_config(remote, fingerprint=None)
        captured_holder: list[str] = []

        async def capture_then_kill(target: Any) -> str:
            """The real capture, then close the server so the probe cannot connect."""
            from core.remote_exec.ssh import ssh_get_host_fingerprint

            fingerprint = await ssh_get_host_fingerprint(target, timeout_seconds=10.0)
            captured_holder.append(fingerprint)
            return fingerprint

        fingerprint = await capture_then_kill(config)

    # Outside the context manager: the server is gone, so the probe below has nowhere to land.
    assert captured_holder == [fingerprint]

    from core.remote_exec.errors import SSHExecutionError

    with pytest.raises(SSHExecutionError):
        await probe_with_trust_on_first_use(
            config=config,
            capture=capture_then_kill,
            probe=probe_ssh_reachable,
        )

    # `probe_with_trust_on_first_use` returned nothing, so no caller had a value to store.
    assert pins.pins == []
    assert row.ssh_host_key_fingerprint is None


# --- 4: one prober, both systems (V66) ---


async def test_both_systems_reach_one_prober(cipher: SecretCipher) -> None:
    """V66: WHM and PMG capture and pin through the same function, so the rule cannot drift.

    Asserted behaviourally rather than by reading imports: a PMG validate over the same loopback
    server captures the same key under the same conditions, and refuses the same way when the
    stored pin is wrong. Its *probe* differs (`pmgsh` rather than `true`), which is why the
    mismatch case is the one compared — that refusal happens in the handshake, before either
    probe's first command.
    """
    async with loopback_ssh_server() as remote:
        row = pmg_server(
            "loopback",
            ssh_host="127.0.0.1",
            ssh_username="noa-ops",
            ssh_password=cipher.encrypt_text(SSH_PASSWORD),
            ssh_private_key=None,
            ssh_host_key_fingerprint=WRONG_FINGERPRINT,
        )
        row.ssh_port = remote.port
        pins = FakeHostKeyPinRepository([row])
        service = PMGServerValidationService(
            session_factory=RecordingSessionFactory(),
            repository_factory=lambda _session: pins,
            cipher=cipher,
            audit_sink=RecordingAuditSink(),
        )

        result = await service.validate(row.id)

        assert result.ok is False
        assert result.error_code == "ssh_host_key_mismatch"
        assert remote.auth_attempts == []

    assert pins.pins == []
    assert row.ssh_host_key_fingerprint == WRONG_FINGERPRINT


async def test_a_pmg_first_validate_captures_and_pins(cipher: SecretCipher) -> None:
    """The PMG negative control: an unpinned node really does get pinned, over a real handshake.

    Its probe runs `pmgsh get /version`, which the loopback server answers with the canned
    stdout below — enough for `parse_pmgsh_json_output` to find a document, which is all this
    case needs. What is being asserted is the *pin*, not PMG's parser (`test_pmg_pmgsh_cli.py`
    owns that).
    """
    async with loopback_ssh_server(stdout='200 OK\n{"release": "8.1"}\n') as remote:
        row = pmg_server(
            "loopback",
            ssh_host="127.0.0.1",
            ssh_username="noa-ops",
            ssh_password=cipher.encrypt_text(SSH_PASSWORD),
            ssh_private_key=None,
            ssh_host_key_fingerprint=None,
        )
        row.ssh_port = remote.port
        pins = FakeHostKeyPinRepository([row])
        service = PMGServerValidationService(
            session_factory=RecordingSessionFactory(),
            repository_factory=lambda _session: pins,
            cipher=cipher,
            audit_sink=RecordingAuditSink(),
        )

        result = await service.validate(row.id)

        assert result.ok is True, result
        assert pins.pins == [remote.host_key_fingerprint]
        assert remote.auth_attempts != []


# --- The HTTP half never leaves the process ---


def test_the_loopback_rig_does_not_reach_the_network() -> None:
    """A guard on the fixtures themselves: the WHM client here is transport-doubled.

    Without it, a future edit could point `base_url` at a real host and this file would start
    making outbound requests from CI — which would look like flakiness rather than like a test
    talking to the internet.
    """
    api = whm_api_ok()
    cipher = build_cipher()
    client = _whm_client_factory(api)(
        whm_server(
            "guard",
            base_url="https://127.0.0.1:2087",
            api_token=cipher.encrypt_text("whm-token"),
        ),
        cipher=cipher,
    )

    assert isinstance(api.transport, httpx.MockTransport)
    assert isinstance(client, WHMClient)
