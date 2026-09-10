"""Out-of-band secret delivery via yopass.

Copied from `noa-old` branch `MCP`. Reference doc: `docs/integrations/yopass.md`.

`_yopass_store` is a code-level helper, never an MCP tool. It exists so a generated credential can
reach the operator without ever crossing the LLM boundary: the tool returns a URL, the
operator opens it, and the plaintext is never in a prompt, a transcript, or a log.

The encryption is **client-side**, and that is the point of using PGPy here rather than
trusting the yopass server. A random passphrase is generated in this process, the
`username`+`password` blob is symmetrically encrypted with it (AES-256), and only the
ciphertext is POSTed. The passphrase then rides in the returned URL's **fragment**:

    <base_url>/#/s/<uuid>/<passphrase>

Browsers do not send the fragment to the server, so the yopass instance holds a blob it
cannot read. Whoever holds the whole link can. `_encrypt_blob` before the POST, and the
passphrase never entering the payload, are therefore load-bearing, not stylistic — a test
asserts the passphrase is absent from the serialized request body.

**Deliver first, then mutate.** Callers store the secret *before* touching the VM. A
failure here raises with nothing changed and nobody locked out; a failure after this point
leaves the old credentials working and must not relay the now-unapplied URL.

Config is injected, never read from a module global: `noa-old` imported a `settings` singleton
this repo does not have (see `core.secrets.crypto` for the same note). `transport` and
`timeout_seconds` stay as keyword arguments so a test can drive the HTTP boundary without a
server. Absent `yopass_base_url` raises `YopassNotConfiguredError` — a tool error the caller
reports, never a crash at import or boot.
"""

from __future__ import annotations

import secrets
import string
from typing import Final

import httpx
import pgpy
from pgpy.constants import SymmetricKeyAlgorithm

from core.config import Settings
from core.secrets.errors import YopassNotConfiguredError, YopassStoreError

# The passphrase travels in a URL fragment, so it must survive being pasted into chat or
# email untouched. Plain alphanumerics need no escaping anywhere.
_PASSPHRASE_ALPHABET: Final[str] = string.ascii_letters + string.digits
_PASSPHRASE_LENGTH: Final[int] = 32

# Named for the route, not the payload: `_SECRET_PATH` trips ruff S105.
_STORE_PATH: Final[str] = "/secret"
_REQUEST_TIMEOUT_SECONDS: Final[float] = 20.0


def _generate_passphrase(length: int = _PASSPHRASE_LENGTH) -> str:
    return "".join(secrets.choice(_PASSPHRASE_ALPHABET) for _ in range(length))


def _build_blob(username: str, password: str) -> str:
    """The cleartext an operator sees after decrypting: username + password, nothing else."""
    return f"username: {username}\npassword: {password}\n"


def _encrypt_blob(plaintext: str, passphrase: str) -> str:
    """Client-side OpenPGP symmetric encrypt. Returns ASCII-armored text."""
    message = pgpy.PGPMessage.new(plaintext)
    encrypted = message.encrypt(passphrase, cipher=SymmetricKeyAlgorithm.AES256)
    return str(encrypted)


async def _yopass_store(
    username: str,
    password: str,
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_seconds: float = _REQUEST_TIMEOUT_SECONDS,
) -> str:
    """Encrypt `username`+`password` client-side, store via yopass, return the share URL.

    Raises `YopassNotConfiguredError` when no base URL is configured, and `YopassStoreError`
    for every transport, status, and response-shape failure — the caller aborts before
    changing anything either way.
    """
    base_url = settings.yopass_base_url
    if not base_url:
        raise YopassNotConfiguredError("yopass_base_url is not configured")
    base_url = base_url.rstrip("/")

    passphrase = _generate_passphrase()
    ciphertext = _encrypt_blob(_build_blob(username, password), passphrase)

    payload = {
        "secret": ciphertext,
        "expiration": settings.yopass_secret_expiration_seconds,
        "one_time": settings.yopass_one_time,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, transport=transport) as client:
            response = await client.post(f"{base_url}{_STORE_PATH}", json=payload)
    except httpx.HTTPError as exc:
        raise YopassStoreError(f"yopass request failed: {exc}") from exc

    if response.status_code >= 400:
        raise YopassStoreError(f"yopass returned HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:
        raise YopassStoreError("yopass returned a non-JSON response") from exc

    secret_id = body.get("message") if isinstance(body, dict) else None
    if not isinstance(secret_id, str) or not secret_id.strip():
        raise YopassStoreError("yopass response carried no secret id")

    # After the `#`: browsers never send it to the yopass server, so the instance stores a
    # blob it cannot decrypt.
    return f"{base_url}/#/s/{secret_id}/{passphrase}"


__all__ = ["_yopass_store"]
