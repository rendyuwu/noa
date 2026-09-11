"""`whm_servers` row → pinned SSH connection.

The three refusals in `resolve_whm_ssh_config` all happen *before* a socket opens, and each
gets its own code because each has a different remedy. Asserting the code, not just that
something raised, is the point: `ssh_exec` would also refuse an unpinned connection, but by
then the failure names a host rather than the WHM server row an admin has to go fix.

The username default is tested against `requires_escalation` rather than in isolation. The
sudo-prefix rule is a biconditional — `sudo -n` ⟺ user ≠ `root` — and a blank `ssh_username`
column resolving to
`root` is the half of it that lives in this module.
"""

from __future__ import annotations

import httpx
import pytest

from core.integrations.whm.ssh import (
    build_whm_client,
    has_ssh_credentials,
    resolve_whm_ssh_config,
)
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.sudo import requires_escalation
from support.remote_exec import PINNED_FINGERPRINT, SSH_PASSWORD
from support.secrets import build_cipher
from support.whm import FakeWHMServer


def _resolve(server: FakeWHMServer, *, require_host_key_fingerprint: bool = True):  # type: ignore[no-untyped-def]
    return resolve_whm_ssh_config(
        server,
        cipher=build_cipher(),
        require_host_key_fingerprint=require_host_key_fingerprint,
    )


# --- Refuse before connecting, with the code that names the remedy ---


def test_resolve_ssh_config_requires_pinned_fingerprint() -> None:
    server = FakeWHMServer(ssh_host_key_fingerprint=None)

    with pytest.raises(SSHExecutionError) as exc:
        _resolve(server)

    assert exc.value.error_code == "ssh_host_key_not_validated"


def test_blank_fingerprint_counts_as_absent() -> None:
    """An admin who cleared the field left the server unvalidated, never pinned to `""`."""
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakeWHMServer(ssh_host_key_fingerprint="   "))

    assert exc.value.error_code == "ssh_host_key_not_validated"


def test_resolve_ssh_config_allows_an_unpinned_row_for_the_tofu_capture_path() -> None:
    """The admin validate route connects unpinned on purpose, to capture the value it will store."""
    config = _resolve(
        FakeWHMServer(ssh_host_key_fingerprint=None), require_host_key_fingerprint=False
    )

    assert config.host_key_fingerprint is None


def test_resolve_ssh_config_requires_ssh_credentials() -> None:
    server = FakeWHMServer(ssh_password=None, ssh_private_key=None)

    with pytest.raises(SSHExecutionError) as exc:
        _resolve(server)

    assert exc.value.error_code == "ssh_not_configured"


def test_resolve_ssh_config_rejects_base_url_without_hostname() -> None:
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakeWHMServer(base_url="not-a-url"))

    assert exc.value.error_code == "ssh_invalid_host"


def test_hostname_is_taken_from_base_url_and_the_whm_port_is_not() -> None:
    """`2087` is the WHM API port. SSH defaults to 22 unless the row says otherwise."""
    config = _resolve(FakeWHMServer(base_url="https://web16.example.com:2087"))

    assert config.host == "web16.example.com"
    assert config.port == 22


def test_explicit_ssh_port_is_honoured() -> None:
    assert _resolve(FakeWHMServer(ssh_port=2222)).port == 2222


# --- The sudo-prefix rule: the resolved username is what escalation reads ---


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_resolve_ssh_config_defaults_username_to_root(stored: str | None) -> None:
    config = _resolve(FakeWHMServer(ssh_username=stored))

    assert config.username == "root"
    assert requires_escalation(config) is False


def test_non_root_username_escalates() -> None:
    config = _resolve(FakeWHMServer(ssh_username="noa-ops"))

    assert config.username == "noa-ops"
    assert requires_escalation(config) is True


# --- Encrypted at rest: credentials are decrypted here, and tolerate a pre-encryption row ---


def test_resolve_ssh_config_decrypts_stored_credentials() -> None:
    cipher = build_cipher()
    server = FakeWHMServer(
        ssh_password=cipher.encrypt_text(SSH_PASSWORD),
        ssh_private_key=cipher.encrypt_text("-----BEGIN OPENSSH PRIVATE KEY-----"),
        ssh_private_key_passphrase=cipher.encrypt_text("key-passphrase"),
    )

    config = resolve_whm_ssh_config(server, cipher=cipher, require_host_key_fingerprint=True)

    assert config.password == SSH_PASSWORD
    assert config.private_key == "-----BEGIN OPENSSH PRIVATE KEY-----"
    assert config.private_key_passphrase == "key-passphrase"
    assert config.host_key_fingerprint == PINNED_FINGERPRINT


def test_unencrypted_credential_columns_pass_through() -> None:
    config = _resolve(FakeWHMServer(ssh_password="legacy-plaintext"))

    assert config.password == "legacy-plaintext"


def test_absent_optional_credentials_stay_none_rather_than_being_decrypted() -> None:
    config = _resolve(FakeWHMServer(ssh_password=SSH_PASSWORD))

    assert config.private_key is None
    assert config.private_key_passphrase is None


def test_has_ssh_credentials_accepts_either_a_password_or_a_key() -> None:
    assert has_ssh_credentials(FakeWHMServer(ssh_password="p", ssh_private_key=None)) is True
    assert has_ssh_credentials(FakeWHMServer(ssh_password=None, ssh_private_key="k")) is True
    assert has_ssh_credentials(FakeWHMServer(ssh_password=None, ssh_private_key=None)) is False


def test_build_whm_client_decrypts_the_api_token_from_the_row() -> None:
    cipher = build_cipher()
    server = FakeWHMServer(api_token=cipher.encrypt_text("API_TOKEN"))

    client = build_whm_client(server, cipher=cipher)

    assert client._headers()["Authorization"] == "whm root:API_TOKEN"


async def test_build_whm_client_carries_the_configured_read_deadline_to_the_socket() -> None:
    """The row factory threads the deadline too, and that is not free.

    This is the factory the MCP tool path and the admin validate probe both reach for, so a
    deadline it dropped would leave every tool waiting the client's own default while the
    deployment's configured number sat in `core.config` looking authoritative. Asserted on the
    deadlines httpx resolved for the request — the four numbers that would arm the real
    socket — rather than on the attribute the client was built from.
    """
    cipher = build_cipher()
    server = FakeWHMServer(api_token=cipher.encrypt_text("API_TOKEN"))
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    client = build_whm_client(
        server,
        cipher=cipher,
        read_timeout_seconds=3.25,
        transport=httpx.MockTransport(handler),
    )
    await client.applist()

    assert seen["timeout"] == {"connect": 10.0, "read": 3.25, "write": 30.0, "pool": 10.0}
