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
        verify_ssl=True,
    )
    safe = server.to_safe_dict()
    assert safe["name"] == "web1"
    assert "api_token" not in safe
