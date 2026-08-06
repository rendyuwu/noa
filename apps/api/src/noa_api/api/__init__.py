"""HTTP layer: dependencies, error handling, routers (T8).

`/auth` lands first. `/admin` (T51-T55) and `/action-requests` (T37) follow, and all
of them reuse `deps.require_session_user` so the per-request `users.is_active` re-read
V6 demands cannot be forgotten on a new route.
"""
