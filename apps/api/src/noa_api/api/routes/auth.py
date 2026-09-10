"""Session routes: login, logout, current user (T8, I.admin-api).

Three routes, and the asymmetry between them is the design:

- `POST /auth/login` — the only route that authenticates. The session token leaves in
  an httpOnly cookie and **not** in the response body: a body-borne token is
  reachable from JavaScript, which is the whole reason V6 specifies httpOnly.
- `POST /auth/logout` — no dependencies at all. V6 makes logout idempotent and callable
  without authentication, so requiring a valid session here would mean an operator whose
  token just expired cannot clear their own cookie.
- `GET /auth/me` — cookie-only, and it re-reads the row through
  `deps.require_session_user`.

Failures raise `AuthError` subclasses; `api.errors` owns the status mapping and the
body shape, so no route here builds an `HTTPException`.

`source_ip` for the rate limiter is `request.client.host`. `X-Forwarded-For` is
deliberately **not** consulted: it is client-supplied, so trusting it would let an
attacker mint a fresh rate-limit bucket per request and defeat V9 entirely. Behind a
reverse proxy that means every request shares the proxy's bucket — the email-scoped
bucket still bounds per-account guessing. Reading a forwarded header requires a
trusted-proxy allowlist, which arrives with the deployment topology in T60.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field

from core.auth.auth_service import AuthenticatedSession, SessionUser
from noa_api.api.deps import AuthServiceDep, JWTServiceDep, SessionUserDep

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Credentials as typed. Normalization and validation live in `AuthService`.

    No length or format constraints: rejecting a malformed email here would answer
    "that address cannot exist" faster than a real attempt, which is a timing-cheap
    enumeration signal. Everything fails the same way instead.
    """

    email: str
    password: str = Field(repr=False)


class SessionUserResponse(BaseModel):
    """Who the caller is. Carries no token and no credential."""

    id: str
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]

    @classmethod
    def from_session_user(cls, user: SessionUser) -> SessionUserResponse:
        return cls(
            id=str(user.user_id),
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=user.roles,
        )


class SessionResponse(BaseModel):
    """Body of `/auth/login` and `/auth/me`. Same shape, so clients parse one thing."""

    user: SessionUserResponse


@router.post("/login", response_model=SessionResponse)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    auth_service: AuthServiceDep,
    jwt_service: JWTServiceDep,
) -> SessionResponse:
    """LDAP authenticate → provision/activate → set the session cookie.

    Rejections all travel as `AuthError`: 429 with `Retry-After` when rate limited, 401
    for bad credentials, 403 when NOA has not activated the row, 503 when the directory
    is unreachable.
    """
    session: AuthenticatedSession = await auth_service.authenticate(
        email=payload.email,
        password=payload.password,
        source_ip=request.client.host if request.client else None,
    )

    # Cookie on the injected `Response`, so FastAPI still serializes the model. The
    # token exists in exactly one place in this reply: the `Set-Cookie` header.
    jwt_service.set_session_cookie(response, session.issued)

    return SessionResponse(user=SessionUserResponse.from_session_user(session.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(jwt_service: JWTServiceDep) -> Response:
    """Clear the session cookie.

    No session dependency and no DB access: idempotent, and it works for a caller whose
    token already expired — that operator most needs the stale cookie gone.

    V6 is explicit that this kills the *cookie*, not the token. A copy captured before
    logout keeps verifying until `exp`, because the session JWT has no `jti` and no
    denylist. That is a recorded deviation, which is why every authenticated route
    re-reads `users.is_active` instead of trusting that logout ended anything.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    jwt_service.clear_session_cookie(response)
    return response


@router.get("/me", response_model=SessionResponse)
async def me(current_user: SessionUserDep) -> SessionResponse:
    """Current operator, read fresh from the database on every call.

    Cookie-only per I.admin-api. The row read is in `require_session_user`, shared with
    every other session-authed route so the check cannot be forgotten on one of them.
    """
    return SessionResponse(user=SessionUserResponse.from_session_user(current_user))


__all__ = ["LoginRequest", "SessionResponse", "SessionUserResponse", "router"]
