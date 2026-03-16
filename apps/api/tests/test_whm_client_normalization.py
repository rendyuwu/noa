from __future__ import annotations

import httpx


async def test_whm_client_maps_401_to_auth_failed() -> None:
    from noa_api.whm.integrations.client import WHMClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="unauthorized", request=request)

    client = WHMClient(
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )
    result = await client.applist()
    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


async def test_whm_client_maps_timeout_to_timeout() -> None:
    from noa_api.whm.integrations.client import WHMClient

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = WHMClient(
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
        timeout_seconds=0.01,
    )
    result = await client.applist()
    assert result["ok"] is False
    assert result["error_code"] == "timeout"


async def test_whm_client_maps_non_json_to_invalid_response() -> None:
    from noa_api.whm.integrations.client import WHMClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, text="not json", request=request)

    client = WHMClient(
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )
    result = await client.applist()
    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


async def test_whm_client_maps_metadata_result_zero_to_whm_api_error() -> None:
    from noa_api.whm.integrations.client import WHMClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"metadata": {"result": 0, "reason": "Nope"}},
            request=request,
        )

    client = WHMClient(
        base_url="https://whm.example.com:2087",
        api_username="root",
        api_token="SECRET",
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )
    result = await client.applist()
    assert result["ok"] is False
    assert result["error_code"] == "whm_api_error"
    assert "Nope" in result["message"]
