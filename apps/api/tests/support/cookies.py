"""`Set-Cookie` comparison that survives the clock.

`Response.delete_cookie` renders `Expires` as an HTTP-date stamped from *now*, so two
identical clear-cookie calls emit different header strings the moment they straddle a
second boundary — a ~1-in-N flake in any test that compares raw headers for equality
(B4). `cookie_shape` keeps every attribute a browser acts on and drops that one stamp.
"""

from __future__ import annotations

from http.cookies import SimpleCookie

CLOCK_STAMPED_ATTRIBUTES = frozenset({"expires"})


def cookie_shape(header: str) -> tuple[str, str, dict[str, object]]:
    """Name, value, and attributes of one `Set-Cookie` header, minus the clock stamp.

    Everything that carries meaning stays in the comparison: `Max-Age=0` still proves the
    cookie is cleared, `Domain`/`Path`/`SameSite`/`HttpOnly`/`Secure` still prove the
    clear targets the cookie the set path wrote (V6).
    """
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(header)
    assert len(cookie) == 1, f"expected exactly one cookie, got {list(cookie)}"
    name, morsel = next(iter(cookie.items()))
    attributes = {
        key: value for key, value in morsel.items() if key not in CLOCK_STAMPED_ATTRIBUTES
    }
    return name, morsel.value, attributes
