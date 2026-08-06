"""Server-side password generation (T15, C15, V49).

Copied from `noa-old` branch `MCP` (C13, V69). Two properties carry the whole file.

**Generated here, never argued in.** `_generate_password` is a code-level helper, ⊥ an MCP
tool and ⊥ reachable from a tool argument. `noa-old` GH #91 is the reason: the reset tool used
to accept `new_password` from the model and echo it back, which put plaintext in the prompt,
the model context, the stored transcript, and potentially the logs. V49 closes that — the
plaintext lives only in the calling `execute()` frame, long enough to be delivered via yopass
and applied to the VM, and the tool returns a `yopass_url` and nothing else.

**The alphabet is a compatibility fix, not taste.** Space, single quote, double quote,
backtick and backslash are excluded because the generated password is later embedded in a
cloud-init payload and passed through a shell-quoted remote command. Each of those characters
has bitten one of those layers. The rest of `string.punctuation` stays, so entropy per
character is barely reduced (~89 symbols instead of ~94).

Length comes from the caller: `settings.secret_password_length` (`SECRET_PASSWORD_LENGTH`,
default 24, floored at 8 by config). `_DEFAULT_PASSWORD_LENGTH` matches that default so a
caller without settings in hand still gets a sane password rather than a short one.
"""

from __future__ import annotations

import secrets
import string
from typing import Final

# Break shell/JSON/cloud-init quoting: space, both quote styles, backtick, backslash.
_FORBIDDEN_SYMBOLS: Final[frozenset[str]] = frozenset(" \"'`\\")
_SAFE_SYMBOLS: Final[str] = "".join(
    char for char in string.punctuation if char not in _FORBIDDEN_SYMBOLS
)
PASSWORD_ALPHABET: Final[str] = string.ascii_letters + string.digits + _SAFE_SYMBOLS

_DEFAULT_PASSWORD_LENGTH: Final[int] = 24


def _generate_password(length: int = _DEFAULT_PASSWORD_LENGTH) -> str:
    """Return a cryptographically random password (V49).

    `secrets.choice`, ⊥ `random`: this value protects a customer VM. Internal helper — the
    plaintext it returns must not be logged, persisted, or returned across the LLM boundary.
    """
    if length < 1:
        raise ValueError("password length must be >= 1")
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


__all__ = ["PASSWORD_ALPHABET", "_generate_password"]
