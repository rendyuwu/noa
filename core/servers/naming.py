"""Identity fields of a server row: names, base URLs, SSH hosts.

Ported from `noa-old` branch `MCP` (`api/routes/server_validation.py`), which is where the
three admin route files shared it. It sits in `core/` here rather than beside the routes for
the reason V66 gives and one more: these are properties of a *row*, not of an HTTP request.
A future bootstrap script or fixture that inserts a server owes the same normalisation, and
a validator reachable only through pydantic is one such a caller cannot reach.

Every function raises `ValueError`, not a `NoaError`, and that is deliberate: they are used
as pydantic `field_validator`s, so pydantic collects them into a `RequestValidationError`
and the shared handler answers 422 with `request_validation_error` + `request_id` (T64,
V73). The failing *value* never reaches the body or the log — `redacted_validation_errors`
keeps `loc` and `type` only, which matters here because these fields sit in the same
request body as an API token.

Three fields, three different shapes, and the differences are all forced by what reads them
afterwards:

- **`name`** — `[A-Za-z0-9._:-]+`. This is what `resolve_*_server_ref` matches an operator's
  word against, and it travels into `choices` entries a model reads, so it stays to a
  character set that cannot be confused with a URL, a shell token or a UUID. `+` rather than
  `*` is V21's "whitespace-only required strings rejected": there is no blank name that
  passes.
- **`base_url`** — normalised to `https://<host>[:<port>]`. WHM and Proxmox both reach their
  API over it, and `core.integrations.whm.ssh.resolve_whm_ssh_config` additionally takes the
  *SSH* hostname out of it with `urlsplit(...).hostname`. So a value carrying a path, a
  query or credentials is not merely untidy: it is a row whose SSH host is one thing and
  whose API endpoint is another. Stripping happens here, once, before the row is written —
  not at read time, where two readers could strip differently.
- **`ssh_host`** — PMG only (`pmg_servers.ssh_host`), because PMG has no base URL to derive
  one from (I.ext, V58). An IP literal or a hostname, never a URL: a value with `://` in it
  would be handed to `asyncssh.create_connection` as a host name and fail at the socket
  with a message about DNS.

`https` is required rather than merely preferred. Both WHM (`:2087`) and Proxmox (`:8006`)
serve their API over TLS, the token travels in the request, and `verify_ssl=False` is
already an available knob for a self-signed certificate — so a plain-`http` row would send
the credential in clear with no way to notice.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Final
from urllib.parse import urlsplit

# `resolve_*_server_ref`'s vocabulary. Same expression as `noa-old`.
_SERVER_NAME_RE: Final = re.compile(r"^[A-Za-z0-9._:-]+$")

# One DNS label: alphanumeric ends, hyphens inside, 63 characters at most (RFC 1035 §2.3.1).
_HOST_LABEL_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

# Total length of a DNS name (RFC 1035 §2.3.4), checked before the per-label walk so a
# pathological value is refused in one comparison.
_MAX_HOSTNAME_LENGTH: Final = 253


def validate_server_name(value: str, *, label: str = "server") -> str:
    """`value` if it is a usable server name, else raise.

    `label` names the system in the message (`"WHM"`, `"Proxmox"`, `"PMG"`) so an operator
    filling three similar forms is told which one refused.
    """
    if not _SERVER_NAME_RE.fullmatch(value):
        raise ValueError(f"String should be a valid {label} server name")
    return value


def normalize_https_base_url(value: str, *, label: str = "server") -> str:
    """`https://<host>[:<port>]`, with path, query, fragment and credentials refused.

    Refused rather than dropped. A caller who typed `https://whm.example.net/whm-server.html`
    meant something by the path, and silently keeping the host would store a row that works
    while reading as though it does not — the shape of mistake nobody finds later.
    """
    error_message = f"String should be a valid HTTPS {label} base URL"
    parsed = urlsplit(value)

    if parsed.scheme != "https":
        raise ValueError(error_message)
    if parsed.username or parsed.password:
        raise ValueError(error_message)
    if parsed.path not in {"", "/"}:
        raise ValueError(error_message)
    if parsed.query or parsed.fragment:
        raise ValueError(error_message)

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(error_message)

    try:
        # `urlsplit` defers parsing the port, so an out-of-range or non-numeric one raises
        # here rather than at the first connection attempt.
        port_value = parsed.port
    except ValueError as exc:
        raise ValueError(error_message) from exc

    port = f":{port_value}" if port_value is not None else ""
    return f"https://{hostname}{port}"


def normalize_ssh_host(value: str, *, label: str = "server") -> str:
    """A stripped IP literal or DNS name, never a URL (PMG, V58).

    The refusals are the characters that would make `asyncssh` read the value as something
    other than a host: a scheme separator, whitespace, a path or query marker, a userinfo
    `@`, or the brackets an IPv6 literal wears inside a URL but not in a connection call.
    """
    error_message = f"String should be a valid {label} SSH host/IP"
    normalized = value.strip()

    if not normalized:
        raise ValueError(error_message)
    if "://" in normalized or any(character.isspace() for character in normalized):
        raise ValueError(error_message)
    if any(character in normalized for character in "/?#@"):
        raise ValueError(error_message)
    if normalized.startswith("[") or normalized.endswith("]"):
        raise ValueError(error_message)

    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        _require_dns_name(normalized, error_message=error_message)
    return normalized


def normalize_whm_identity(value: str) -> str:
    """`strip().lower()` — the one comparison a WHM account name is ever made under.

    Two sites compare WHM identity strings and they must agree, so the normalisation is a
    function rather than a repeated expression:

    - the admin write refuses a reseller row whose `name` is not its `api_username` (V109(b));
    - an account CHANGE refuses before writing anything when the account's `owner` is not the
      resolved row's `api_username`.

    Lowercase because cPanel usernames are lowercase and an operator types what they read;
    stripped because a trailing space pasted into a form is not a different reseller. Not a
    validator: it normalises and never raises, since both call sites compare rather than admit.
    """
    return value.strip().lower()


def _require_dns_name(value: str, *, error_message: str) -> None:
    """Raise unless `value` is a syntactically valid DNS name.

    A trailing dot is stripped before the walk (`pmg.example.net.` is the same name fully
    qualified), but an *empty* label anywhere else — `pmg..example.net` — is not, because
    that is a typo that resolves to nothing.
    """
    if len(value) > _MAX_HOSTNAME_LENGTH:
        raise ValueError(error_message)

    labels = value.rstrip(".").split(".")
    if any(not label for label in labels):
        raise ValueError(error_message)
    if any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(error_message)


__all__ = [
    "normalize_https_base_url",
    "normalize_ssh_host",
    "normalize_whm_identity",
    "validate_server_name",
]
