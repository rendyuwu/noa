from __future__ import annotations

from uuid import uuid4


async def test_whm_server_to_safe_dict_excludes_api_token() -> None:
    from noa_api.storage.postgres.models import WHMServer

    server = WHMServer(
        id=uuid4(),
        name="web1",
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        ssh_username="root",
        ssh_port=22,
        ssh_password="SSH_PASSWORD",
        ssh_private_key="PRIVATE_KEY",
        ssh_private_key_passphrase="PASSPHRASE",
        ssh_host_key_fingerprint="SHA256:test",
        verify_ssl=True,
    )
    safe = server.to_safe_dict()
    assert safe["name"] == "web1"
    assert "api_token" not in safe
    assert "ssh_password" not in safe
    assert "ssh_private_key" not in safe
    assert "ssh_private_key_passphrase" not in safe
    assert safe["ssh_username"] == "root"
    assert safe["ssh_port"] == 22
    assert safe["ssh_host_key_fingerprint"] == "SHA256:test"
    assert safe["has_ssh_password"] is True
    assert safe["has_ssh_private_key"] is True
