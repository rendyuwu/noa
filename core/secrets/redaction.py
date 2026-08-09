"""Key-name redaction for anything persisted or logged (T73, V8, V45).

Ported from `noa-old` branch `MCP` (`core/secrets/redaction.py`) per C13 — the key list and
the recursive walk are the source's, unchanged. T15 deliberately left it behind: it lands
with the code that *writes* redacted audit args, which is T73, because a redactor with no
caller is a control no test exercises, and that is how B2 shipped (V69).

**Redaction by key name, not by value.** Nothing here inspects a string for entropy or for
an `enc:v1:fernet:` prefix. A tool argument is named by its schema, the schema is ours, and
the names below are the ones that carry credentials. Guessing from values would both miss a
secret with a boring shape and mangle an ordinary field that happens to look random.

**One departure from `noa-old`, and it is a real one.** There the audit writer *encrypted*
sensitive args (`_encrypt_sensitive_args` in `storage/postgres/action_tool_runs.py`) so the
admin UI could decrypt them back. §V45/V47 say **redacted**, and an encrypted argument is
recoverable — a Fernet key compromise turns the whole audit table into a credential dump.
So the replacement is one-way and the plaintext never reaches `tool_runs` at all. That
choice costs nothing NOA needs: the audit surface (T55) shows an operator *what was asked
for*, and `[redacted]` answers that for a password field.

Applied to results as well as arguments (`noa_api.mcp_audit`). `whm_list_servers` already
renders through `to_safe_dict()` and carries no credential material (V2, V8), but the
summary path is shared by every later tool and a per-tool exemption is how one of them
eventually writes a secret into the audit trail.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

REDACTED: Final[str] = "[redacted]"

# `noa-old`'s list verbatim. It covers the credential columns of schema v1
# (`api_token`, `api_token_secret`, `ssh_*`) and the argument names of the CHANGE tools
# that carry a secret (`cipassword` for T27's cloud-init write). Comparison is
# case-insensitive and whitespace-stripped, so a schema that spells one differently still
# matches.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_token",
        "api_token_secret",
        "token",
        "password",
        "secret",
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
        "private_key",
        "passphrase",
        "new_password",
        "cipassword",
    }
)


def is_sensitive_key(key: str) -> bool:
    """True when a value under `key` must never be stored or logged (V8)."""
    return key.strip().lower() in SENSITIVE_KEYS


def redact_sensitive_data(value: object, *, replacement: str = REDACTED) -> object:
    """`value` with every sensitive-keyed entry replaced, at any depth (V8).

    Recurses through mappings and sequences so a credential nested inside a structured
    argument is caught too — a flat top-level scan would miss
    `{"server": {"ssh_password": ...}}`.

    `str`, `bytes` and `bytearray` are excluded from the sequence branch on purpose: they
    are `Sequence`s, and walking one would explode a password into a list of characters,
    which is both unreadable and unredacted.
    """
    if isinstance(value, Mapping):
        return {
            str(key): (
                replacement
                if is_sensitive_key(str(key))
                else redact_sensitive_data(item, replacement=replacement)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_sensitive_data(item, replacement=replacement) for item in value]
    return value


def sensitive_key_paths(value: object, *, _prefix: str = "") -> list[str]:
    """Every location inside `value` that `redact_sensitive_data` would replace (V8).

    The same walk as the redactor, answering "where" instead of rewriting — so a caller that
    has to *refuse* a redacted payload rather than produce one (T38's executor, which will
    not run a change whose arguments came back as `[redacted]`) asks the same question the
    redactor answered and cannot disagree with it (V66).

    A flat top-level scan is the failure this exists to prevent: the redactor recurses, so
    `{"server": {"ssh_password": ...}}` is stored redacted and a top-level check sees nothing
    wrong with it.

    Paths are dotted, with list positions as `[i]`, so a refusal names where the value was
    without carrying it.
    """
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{_prefix}.{key}" if _prefix else str(key)
            if is_sensitive_key(str(key)):
                found.append(path)
            else:
                found.extend(sensitive_key_paths(item, _prefix=path))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            found.extend(sensitive_key_paths(item, _prefix=f"{_prefix}[{index}]"))
    return found


__all__ = [
    "REDACTED",
    "SENSITIVE_KEYS",
    "is_sensitive_key",
    "redact_sensitive_data",
    "sensitive_key_paths",
]
