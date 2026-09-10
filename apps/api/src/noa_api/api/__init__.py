"""HTTP layer: dependencies, error handling, routers.

`/auth` and `/action-requests` are live; `/admin` follows. All of
them reuse `deps.require_session_user` so the per-request `users.is_active` re-read
V6 demands cannot be forgotten on a new route — which on the decision routes is the only
thing standing between a disabled operator and an approved production change.
"""
