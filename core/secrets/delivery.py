"""Getting one generated credential to the operator, as a seam (T27 — C15, V49, V50).

`_yopass_store` is the mechanism (T15). This is the *shape* the tool path asks for it in, and it
exists for one reason: `_yopass_store` needs three settings — the base URL, the expiration and
the one-time flag — and `McpToolContext` deliberately holds no `Settings`. T15 removed the
settings singleton `noa-old` imported from three places, and `noa_api.main.build_runtime` is the
single `get_settings()` caller (C7). A tool reaching for its own copy of configuration would be a
second world.

So the settings are bound **once, at startup**, into a callable the tool context carries. Two
consequences worth naming:

- a deployment with no `YOPASS_BASE_URL` still boots, and the reset tool reports
  `yopass_not_configured` when an approved change reaches delivery (C15: a tool error, never a
  crash at import or boot). The check stays inside `_yopass_store`, so it is one answer;
- `transport` is the test seam and it is bound here too, which means a runner test drives the
  real `_yopass_store` — the real PGPy encrypt, the real fragment assembly, the real response
  parsing — with only the socket doubled. A double that replaced the delivery *function* would
  let V50's client-side encryption pass by not running (V69's shape: provenance is not evidence).

**The Protocol is keyword-only on purpose.** `username` and `password` are two strings of the
same type, and a positional call site that swapped them would encrypt a blob naming the password
as the user and hand the operator a login they cannot use — silently, because both halves are
opaque to everything downstream.

Named `SecretDelivery` rather than `YopassDelivery`: DECISIONS §8.5 asked for the yopass helpers
to be shared and internal, "not welded to the password-reset tool". The tool context should not
know which service delivers, and the day a second one exists this is the type that already said
so.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from core.config import Settings
from core.secrets.yopass import _yopass_store


class SecretDelivery(Protocol):
    """Hand `username` + `password` to the operator out of band; answer the URL.

    Raises `YopassError` (a `NoaError`) when delivery fails, which is what lets the caller abort
    **before** it changes anything — the ordering C15 and V62 both rest on.
    """

    async def __call__(self, *, username: str, password: str) -> str: ...


def build_yopass_delivery(
    *,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SecretDelivery:
    """The production `SecretDelivery`, with configuration bound once (C15, V50).

    A closure over `settings` rather than a class, the way the CHANGE runners are closures over
    their tool context: what it needs is three values that do not change for the life of the
    process, and a class would be a constructor plus a method to say the same thing.
    """

    async def deliver(*, username: str, password: str) -> str:
        return await _yopass_store(
            username,
            password,
            settings=settings,
            transport=transport,
        )

    return deliver


__all__ = ["SecretDelivery", "build_yopass_delivery"]
