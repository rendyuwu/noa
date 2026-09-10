"""HTTP layer: dependencies, error handling, routers.

`/auth` and `/action-requests` are live; `/admin` follows. All of them reuse
`deps.require_session_user` so the per-request `users.is_active` re-read — the cookie's claims are
not revocable — cannot be forgotten on a new route, and on the decision routes it is the only thing
standing between a disabled operator and an approved production change.
"""
