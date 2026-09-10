"""Out-of-band secret delivery via yopass.

Ported from `noa-old` branch `MCP` alongside the helper, rewired to this repo's
injected `Settings`.

V50 is a chain, and each link is asserted separately because any one of them silently
defeats the others: the blob is username+password, it is encrypted **before** the POST, the
POST goes to `<base>/secret`, and the decryption passphrase comes back in the URL fragment
and never in the request. `test_passphrase_never_sent_to_server` is the load-bearing one — a
refactor that "simplifies" by letting the server generate the key would still pass every
other test here while making the yopass instance able to read every secret NOA sends it.

The HTTP boundary is driven with `httpx.MockTransport`, so the real client, the real PGPy
encrypt and the real URL assembly all run; only the socket is faked (C15: every failure is a
tool error, never a crash).
"""

from __future__ import annotations

import json
import logging

import httpx
import pgpy
import pytest

from core.config import Settings
from core.secrets.errors import YopassNotConfiguredError, YopassStoreError
from core.secrets.yopass import _yopass_store
from support.auth import build_settings

BASE_URL = "https://yopass.example.com"
SECRET_ID = "11111111-2222-3333-4444-555555555555"
USERNAME = "operator@example.com"
PASSWORD = "s3cr3t-pw"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "yopass_base_url": BASE_URL,
        "yopass_secret_expiration_seconds": 604800,
        "yopass_one_time": False,
    }
    return build_settings(**{**defaults, **overrides})


def _capturing_transport(captured: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": SECRET_ID})

    return httpx.MockTransport(handler)


def _failing_transport(response: httpx.Response | Exception) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    return httpx.MockTransport(handler)


# --- V50: request shape ---


async def test_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    assert captured["url"] == f"{BASE_URL}/secret"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["expiration"] == 604800
    assert body["one_time"] is False
    assert isinstance(body["secret"], str) and body["secret"].strip()


async def test_expiration_and_one_time_come_from_settings() -> None:
    """Operators tune these per deployment; a hardcoded value would ignore the config."""
    captured: dict[str, object] = {}

    await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(yopass_secret_expiration_seconds=3600, yopass_one_time=True),
        transport=_capturing_transport(captured),
    )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["expiration"] == 3600
    assert body["one_time"] is True


async def test_body_carries_ciphertext_not_plaintext() -> None:
    """Encryption happens client-side, before the POST — the server sees a blob."""
    captured: dict[str, object] = {}

    await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    serialized = json.dumps(captured["body"])
    assert PASSWORD not in serialized
    assert USERNAME not in serialized
    assert "-----BEGIN PGP MESSAGE-----" in serialized


# --- V50: URL shape and the fragment ---


async def test_builds_fragment_url() -> None:
    captured: dict[str, object] = {}

    url = await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    prefix = f"{BASE_URL}/#/s/{SECRET_ID}/"
    assert url.startswith(prefix)
    passphrase = url[len(prefix) :]
    assert passphrase and "/" not in passphrase


async def test_round_trip_decrypts_blob() -> None:
    """The operator's side of the contract: the link's key opens the stored ciphertext."""
    captured: dict[str, object] = {}

    url = await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    passphrase = url.rsplit("/", 1)[-1]
    body = captured["body"]
    assert isinstance(body, dict)
    cleartext = pgpy.PGPMessage.from_blob(body["secret"]).decrypt(passphrase).message
    if isinstance(cleartext, (bytes, bytearray)):
        cleartext = cleartext.decode("utf-8")

    assert USERNAME in cleartext
    assert PASSWORD in cleartext


async def test_passphrase_never_sent_to_server() -> None:
    """The whole point of client-side encryption: yopass holds a blob it cannot read."""
    captured: dict[str, object] = {}

    url = await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    passphrase = url.rsplit("/", 1)[-1]
    assert passphrase not in json.dumps(captured["body"])
    assert passphrase not in str(captured["url"])


async def test_trailing_slash_in_base_url_does_not_double_up() -> None:
    captured: dict[str, object] = {}

    url = await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(yopass_base_url=f"{BASE_URL}/"),
        transport=_capturing_transport(captured),
    )

    assert captured["url"] == f"{BASE_URL}/secret"
    assert url.startswith(f"{BASE_URL}/#/s/")


# --- C15: unconfigured is a tool error, not a crash ---


async def test_missing_base_url_raises_not_configured() -> None:
    with pytest.raises(YopassNotConfiguredError) as excinfo:
        await _yopass_store(USERNAME, PASSWORD, settings=_settings(yopass_base_url=""))

    assert excinfo.value.error_code == "yopass_not_configured"


async def test_unset_settings_raises_not_configured() -> None:
    with pytest.raises(YopassNotConfiguredError):
        await _yopass_store(USERNAME, PASSWORD, settings=build_settings())


# --- Every remote failure is a `YopassStoreError`, so the caller aborts unchanged ---


async def test_http_error_status_raises() -> None:
    with pytest.raises(YopassStoreError):
        await _yopass_store(
            USERNAME,
            PASSWORD,
            settings=_settings(),
            transport=_failing_transport(httpx.Response(500)),
        )


async def test_transport_failure_raises() -> None:
    with pytest.raises(YopassStoreError):
        await _yopass_store(
            USERNAME,
            PASSWORD,
            settings=_settings(),
            transport=_failing_transport(httpx.ConnectError("boom")),
        )


async def test_non_json_response_raises() -> None:
    with pytest.raises(YopassStoreError):
        await _yopass_store(
            USERNAME,
            PASSWORD,
            settings=_settings(),
            transport=_failing_transport(httpx.Response(200, text="not json")),
        )


@pytest.mark.parametrize("body", [{"foo": "bar"}, {"message": ""}, {"message": 42}, []])
async def test_missing_secret_id_raises(body: object) -> None:
    """A 200 with no usable id must not become a share URL pointing at nothing."""
    with pytest.raises(YopassStoreError):
        await _yopass_store(
            USERNAME,
            PASSWORD,
            settings=_settings(),
            transport=_failing_transport(httpx.Response(200, json=body)),
        )


# --- V49: nothing sensitive reaches the logs ---


async def test_logs_carry_no_plaintext_or_passphrase(caplog: pytest.LogCaptureFixture) -> None:
    captured: dict[str, object] = {}
    caplog.set_level(logging.DEBUG)

    url = await _yopass_store(
        USERNAME,
        PASSWORD,
        settings=_settings(),
        transport=_capturing_transport(captured),
    )

    passphrase = url.rsplit("/", 1)[-1]
    assert PASSWORD not in caplog.text
    assert passphrase not in caplog.text
