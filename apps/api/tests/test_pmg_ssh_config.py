"""`pmg_servers` row → pinned SSH connection.

PMG has one transport, so `resolve_pmg_ssh_config` is the only door into a PMG box — every
refusal below is the difference between a command running and not running at all.

Four of them happen *before* a socket opens, and each gets its own code because each has a
different remedy. Asserting the code, not just that something raised, is the point: `ssh_exec`
would also refuse an unpinned connection, but by then the failure names a host rather than the
PMG server row an admin has to go fix.

The host-validation cases are ported verbatim from `noa-old` and are stricter than WHM's, which
gets its hostname out of a parsed `base_url`. A `pmg_servers` row stores the bare host, so
`https://pmg:8006`, `user@pmg`, `pmg/path` and `pmg example.com` all reach this function
unexamined — and each would send the command somewhere other than where the operator meant.

The username default is tested against `requires_escalation` rather than in isolation. The
sudo-escalation rule is a biconditional — `sudo -n` ⟺ user ≠ `root` — and a blank `ssh_username`
column resolving to `root`
is the half of it that lives in this module.
"""

from __future__ import annotations

import pytest

from core.integrations.pmg.ssh import has_ssh_credentials, resolve_pmg_ssh_config
from core.remote_exec.errors import SSHExecutionError
from core.remote_exec.sudo import requires_escalation
from support.pmg import PMG_HOST, FakePMGServer
from support.remote_exec import PINNED_FINGERPRINT, SSH_PASSWORD
from support.secrets import build_cipher


def _resolve(server: FakePMGServer, *, require_host_key_fingerprint: bool = True):  # type: ignore[no-untyped-def]
    return resolve_pmg_ssh_config(
        server,
        cipher=build_cipher(),
        require_host_key_fingerprint=require_host_key_fingerprint,
    )


# --- A ported control needs its own test: refuse before connecting, with the code that names
# the remedy ---


def test_resolve_ssh_config_requires_pinned_fingerprint() -> None:
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakePMGServer(ssh_host_key_fingerprint=None))

    assert exc.value.error_code == "ssh_host_key_not_validated"


def test_blank_fingerprint_counts_as_absent() -> None:
    """An admin who cleared the field left the server unvalidated, never pinned to `""`."""
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakePMGServer(ssh_host_key_fingerprint="   "))

    assert exc.value.error_code == "ssh_host_key_not_validated"


def test_resolve_ssh_config_allows_an_unpinned_row_for_the_tofu_capture_path() -> None:
    """The admin validate flow connects unpinned on purpose, to capture the value it will
    store."""
    config = _resolve(
        FakePMGServer(ssh_host_key_fingerprint=None), require_host_key_fingerprint=False
    )

    assert config.host_key_fingerprint is None


def test_resolve_ssh_config_requires_ssh_credentials() -> None:
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakePMGServer(ssh_password=None, ssh_private_key=None))

    assert exc.value.error_code == "ssh_not_configured"


# --- The stored host is a bare host, and nothing else ---


@pytest.mark.parametrize(
    "ssh_host",
    [
        "",
        "   ",
        "https://pmg.example.com:8006",
        "pmg.example.com/path",
        "pmg.example.com?x=1",
        "pmg.example.com#frag",
        "user@pmg.example.com",
        "pmg example.com",
        "pmg\nexample.com",
        "pmg\texample.com",
        "[2001:db8::1]",
    ],
)
def test_resolve_ssh_config_rejects_anything_that_is_not_a_bare_host(ssh_host: str) -> None:
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakePMGServer(ssh_host=ssh_host))

    assert exc.value.error_code == "ssh_invalid_host"


def test_plain_host_and_bare_ipv4_are_accepted() -> None:
    assert _resolve(FakePMGServer()).host == PMG_HOST
    assert _resolve(FakePMGServer(ssh_host="10.10.10.5")).host == "10.10.10.5"


def test_surrounding_whitespace_is_trimmed_rather_than_rejected() -> None:
    """A pasted trailing newline is an admin artifact, never an unusable row. Interior
    whitespace is
    the hazard, and it is rejected above."""
    assert _resolve(FakePMGServer(ssh_host=f"  {PMG_HOST}  ")).host == PMG_HOST
    assert _resolve(FakePMGServer(ssh_host=f"{PMG_HOST}\n")).host == PMG_HOST


# --- port: `0` is a typo, not "unset" ---


@pytest.mark.parametrize("ssh_port", [0, -1])
def test_resolve_ssh_config_rejects_a_non_positive_port(ssh_port: int) -> None:
    """`server.ssh_port or 22` would read `0` as unset and silently connect to 22."""
    with pytest.raises(SSHExecutionError) as exc:
        _resolve(FakePMGServer(ssh_port=ssh_port))

    assert exc.value.error_code == "ssh_invalid_port"


def test_absent_port_defaults_to_22() -> None:
    assert _resolve(FakePMGServer()).port == 22


def test_explicit_ssh_port_is_honoured() -> None:
    assert _resolve(FakePMGServer(ssh_port=2222)).port == 2222


# --- Sudo escalation: the resolved username is what escalation reads ---


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_resolve_ssh_config_defaults_username_to_root(stored: str | None) -> None:
    config = _resolve(FakePMGServer(ssh_username=stored))

    assert config.username == "root"
    assert requires_escalation(config) is False


def test_non_root_username_escalates() -> None:
    config = _resolve(FakePMGServer(ssh_username="noa-ops"))

    assert config.username == "noa-ops"
    assert requires_escalation(config) is True


# --- Encrypted at rest: credentials are decrypted here, and tolerate a pre-encryption row ---


def test_resolve_ssh_config_decrypts_stored_credentials() -> None:
    cipher = build_cipher()
    server = FakePMGServer(
        ssh_password=cipher.encrypt_text(SSH_PASSWORD),
        ssh_private_key=cipher.encrypt_text("-----BEGIN OPENSSH PRIVATE KEY-----"),
        ssh_private_key_passphrase=cipher.encrypt_text("key-passphrase"),
    )

    config = resolve_pmg_ssh_config(server, cipher=cipher, require_host_key_fingerprint=True)

    assert config.password == SSH_PASSWORD
    assert config.private_key == "-----BEGIN OPENSSH PRIVATE KEY-----"
    assert config.private_key_passphrase == "key-passphrase"
    assert config.host_key_fingerprint == PINNED_FINGERPRINT


def test_unencrypted_credential_columns_pass_through() -> None:
    config = _resolve(FakePMGServer(ssh_password="legacy-plaintext"))

    assert config.password == "legacy-plaintext"


def test_absent_optional_credentials_stay_none_rather_than_being_decrypted() -> None:
    config = _resolve(FakePMGServer(ssh_password=SSH_PASSWORD))

    assert config.private_key is None
    assert config.private_key_passphrase is None


def test_has_ssh_credentials_accepts_either_a_password_or_a_key() -> None:
    assert has_ssh_credentials(FakePMGServer(ssh_password="p", ssh_private_key=None)) is True
    assert has_ssh_credentials(FakePMGServer(ssh_password=None, ssh_private_key="k")) is True
    assert has_ssh_credentials(FakePMGServer(ssh_password=None, ssh_private_key=None)) is False
