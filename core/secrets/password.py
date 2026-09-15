"""Server-side password generation.

Copied from `noa-old` branch `MCP`. Two properties carry the whole file.

**Generated here, never argued in.** `_generate_password` is a code-level helper, never an MCP tool
and never reachable from a tool argument. `noa-old` GH #91 is the reason: the reset tool used to
accept `new_password` from the model and echo it back, which put plaintext in the prompt, the model
context, the stored transcript, and potentially the logs. Server-side generation closes that — the
plaintext lives only in the calling `execute()` frame, long enough to be delivered via yopass and
applied to the VM, and the tool returns a `yopass_url` and nothing else.

**The alphabet is letters and digits because a customer types it.** Owner-stated, and recorded in
`docs/integrations/yopass.md` under `SECRET_PASSWORD_LENGTH` so it stays citable rather than
remembered. NOA hands the operator a yopass link and never reaches the customer itself; the
operator passes it on, and the customer reads the value back and enters it by hand. Punctuation
makes that harder to dictate and to type, so the symbol class is gone rather than filtered. Space,
single quote, double quote, backtick and backslash still must never appear — the password is later
embedded in a cloud-init payload and passed through a shell-quoted remote command, and each of
those characters has bitten one of those layers — but that exclusion is now structurally
satisfied rather than enforced: with no punctuation in the alphabet there is nothing for a
filter to remove. That history is also why punctuation may not creep back in without re-reading
those two layers first. The shorter charset costs about 13 bits at the default length (the
alphabet was 90 characters, so 90^24 ≈ 156 bits against 62^24 ≈ 143 bits); the length setting
below buys that back.

Length comes from the caller: `settings.secret_password_length` (`SECRET_PASSWORD_LENGTH`,
default 24, floored at 8 by config). `_DEFAULT_PASSWORD_LENGTH` matches that default so a
caller without settings in hand still gets a sane password rather than a short one.
"""

from __future__ import annotations

import secrets
import string
from typing import Final

PASSWORD_ALPHABET: Final[str] = string.ascii_letters + string.digits

_DEFAULT_PASSWORD_LENGTH: Final[int] = 24


def _generate_password(length: int = _DEFAULT_PASSWORD_LENGTH) -> str:
    """Return a cryptographically random password.

    `secrets.choice`, never `random`: this value protects a customer VM. Internal helper — the
    plaintext it returns must not be logged, persisted, or returned across the LLM boundary.
    """
    if length < 1:
        raise ValueError("password length must be >= 1")
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


__all__ = ["PASSWORD_ALPHABET", "_generate_password"]
