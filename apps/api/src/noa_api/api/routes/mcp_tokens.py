"""MCP token management: list, mint, revoke — admin and self-service.

**Six routes and no policy**, the rule `admin_users.py` and `admin_roles.py` state one file
over: every one of them lives in `core.auth.mcp_token_service` — the 256-bit generator, the
SHA-256 digest, the label cap, the user-scoped delete. Those are properties of the credential,
so they must hold for a future CLI or a bootstrap script too, not only for whoever calls these
paths. A handler here resolves the actor, calls one service method, and shapes the answer.

**Two routers, one module.** `admin_router` acts on `{user_id}` from the path behind
`require_admin`; `me_router` acts on the caller's own id behind `require_session_user`. They
share `McpTokenResponse` and `_to_token_response` rather than copying them, and that is
the point of keeping them together: the shape an operator sees of their own token and the shape
an admin sees of someone else's are the same shape, so a field added to one cannot appear in
one surface and not the other.

**The `/me` handlers take no id from the request.** `current_user.user_id` comes off the
session the cookie resolved to, so there is no path segment, query parameter or body field a
caller could substitute to reach a colleague's tokens — the scoping is structural rather than
checked. On the revoke route the *token* id is still a path parameter, and the service puts
both ids in the WHERE clause, so a colleague's token id answers exactly as a fabricated one
does: 404 `mcp_token_not_found`, never 403.

**The plaintext exists in exactly one response.** `MintedTokenResponse` is the only model in
this app with a `plaintext` field, and only `POST` returns it (show-once). Every read path
returns `McpTokenView`, which has no field that could hold one — a mistake here is not
"remember to strip it" but "there is nothing to strip". `token_hash` is likewise absent rather
than redacted.

**Nothing here builds an `HTTPException`.** Refusals are `McpTokenError` /
`UserNotFoundError` subclasses and `noa_api.api.errors` owns status, body and `request_id`,
so a 404 for an unknown user and a 404 for an unknown token cannot drift apart.

**An inactive user may still be minted a token, and that is deliberate** — the service's call,
documented there: the credential grants nothing while `is_active=False` (permissions zero out
while inactive, re-checked per request), and setting an operator up before activating them is a
legitimate order of operations. Note the asymmetry: `/me` is unreachable for such an operator
anyway, because `require_session_user` refuses their session first.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from core.auth.mcp_token_service import McpTokenView
from noa_api.api.deps import AdminUserDep, McpTokenServiceDep, SessionUserDep
from noa_api.api.serialization import iso_or_none

admin_router = APIRouter(prefix="/admin", tags=["admin"])
me_router = APIRouter(prefix="/me", tags=["me"])


class McpTokenResponse(BaseModel):
    """One `mcp_tokens` row as every read path returns it.

    `token_prefix` is the display fragment — the public marker plus eight characters, with
    ~208 bits unrevealed — so an admin can match a row to the credential in a config file
    without the row ever carrying the credential.

    The three nullable timestamps are the ones that make a token worth revoking or not:
    `last_used_at` NULL means it has never authenticated a request, `librechat_user_id` NULL
    means TOFU binding has not happened yet, and `expires_at` NULL means the row is
    the only thing retiring it.
    """

    id: str
    user_id: str
    token_prefix: str
    label: str | None
    librechat_user_id: str | None
    last_used_at: str | None
    last_ldap_check_at: str | None
    expires_at: str | None
    # Not nullable, unlike the four above: `created_at` is stamped at insert by the column's
    # default, never by a caller, so a row that exists has one.
    created_at: str


class McpTokensResponse(BaseModel):
    """`GET` on either router. Newest first, ordered in the statement."""

    tokens: list[McpTokenResponse]


class MintTokenRequest(BaseModel):
    """`POST` on either router. A label, or nothing.

    Optional because `mcp_tokens.label` is nullable: an unnamed token is a legitimate thing to
    mint, and requiring a name here would be a rule the schema does not have. Blank normalizes
    to `None` and over-long is 400 `invalid_token_label` — both in the service, so a bootstrap
    script gets the same treatment as this route.
    """

    label: str | None = None


class MintedTokenResponse(BaseModel):
    """`POST` answer: the row, plus the plaintext, once.

    The one place in NOA a token plaintext crosses a response boundary. It is not stored, not
    logged, and not recoverable — `MintedMcpToken` even hides it from `repr()` so a traceback
    or a structlog line cannot publish it. A caller who loses it mints another and revokes
    this one.
    """

    token: McpTokenResponse
    plaintext: str


class RevokeTokenResponse(BaseModel):
    """`{ok: true}`. The row is gone, so there is nothing to return (revoke = delete)."""

    ok: bool


def _to_token_response(view: McpTokenView) -> McpTokenResponse:
    """Shape one `McpTokenView` for the wire.

    Field-by-field rather than by `model_validate`, so adding a column to `McpTokenView` does
    not publish it here by default — the next person has to decide.
    """
    return McpTokenResponse(
        id=str(view.id),
        user_id=str(view.user_id),
        token_prefix=view.token_prefix,
        label=view.label,
        librechat_user_id=view.librechat_user_id,
        last_used_at=iso_or_none(view.last_used_at),
        last_ldap_check_at=iso_or_none(view.last_ldap_check_at),
        expires_at=iso_or_none(view.expires_at),
        created_at=view.created_at.isoformat(),
    )


def _to_tokens_response(views: list[McpTokenView]) -> McpTokensResponse:
    return McpTokensResponse(tokens=[_to_token_response(view) for view in views])


# --- Admin surface: any operator's tokens ---


@admin_router.get("/users/{user_id}/tokens", response_model=McpTokensResponse)
async def list_user_tokens(
    user_id: UUID,
    admin_user: AdminUserDep,
    tokens: McpTokenServiceDep,
) -> McpTokensResponse:
    """One operator's MCP tokens.

    404 `user_not_found` for an absent operator rather than an empty list: "holds no tokens"
    and "no such user" are different answers, and collapsing them would render a stale panel
    link as an empty page instead of a dead one.
    """
    return _to_tokens_response(await tokens.list_for_user(user_id))


@admin_router.post("/users/{user_id}/tokens", response_model=MintedTokenResponse)
async def mint_user_token(
    user_id: UUID,
    payload: MintTokenRequest,
    admin_user: AdminUserDep,
    tokens: McpTokenServiceDep,
) -> MintedTokenResponse:
    """Mint a token for one operator; return the plaintext once.

    200 rather than 201: there is no `Location` for the created row — no route reads a token by
    id, by design, because the only useful read is the list and the only useful body is the one
    below.

    `actor_email` is the admin's, not the target's: the audit event answers "who issued this
    credential", which for a token minted on someone else's behalf is the question worth
    logging. The event carries ids, prefix and label, never the digest or the plaintext.
    """
    minted = await tokens.mint(user_id, label=payload.label, actor_email=admin_user.email)
    return MintedTokenResponse(
        token=_to_token_response(minted.token),
        plaintext=minted.plaintext,
    )


@admin_router.delete("/users/{user_id}/tokens/{token_id}", response_model=RevokeTokenResponse)
async def revoke_user_token(
    user_id: UUID,
    token_id: UUID,
    admin_user: AdminUserDep,
    tokens: McpTokenServiceDep,
) -> RevokeTokenResponse:
    """Revoke one token.

    Both ids reach the WHERE clause, so a `token_id` belonging to a different operator is not
    deleted and answers 404 — the same 404 an id that never existed gets. That matters even
    on an admin-only surface: the status must not tell a caller which ids are real, or a
    future non-admin caller of the same service would inherit an oracle.
    """
    await tokens.revoke(user_id, token_id, actor_email=admin_user.email)
    return RevokeTokenResponse(ok=True)


# --- Self-service surface: the caller's own tokens ---


@me_router.get("/mcp-tokens", response_model=McpTokensResponse)
async def list_own_tokens(
    current_user: SessionUserDep,
    tokens: McpTokenServiceDep,
) -> McpTokensResponse:
    """The caller's own MCP tokens.

    The id comes off the resolved session, never off the request, so there is nothing to
    substitute. `require_session_user` re-read the `users` row on the way in, which is
    what stops a disabled operator from listing credentials with a cookie that has not expired.
    """
    return _to_tokens_response(await tokens.list_for_user(current_user.user_id))


@me_router.post("/mcp-tokens", response_model=MintedTokenResponse)
async def mint_own_token(
    payload: MintTokenRequest,
    current_user: SessionUserDep,
    tokens: McpTokenServiceDep,
) -> MintedTokenResponse:
    """Mint a token for the caller; return the plaintext once.

    Self-service, and it grants no privilege: an MCP token authenticates *as* the operator and
    nothing more, so what it can call is whatever their roles already permit — zero for an
    operator with none (re-checked per call, zero for a disabled account). The escalation
    a self-mint route would have to enable does not exist, because the token carries no scope
    of its own.
    """
    minted = await tokens.mint(
        current_user.user_id, label=payload.label, actor_email=current_user.email
    )
    return MintedTokenResponse(
        token=_to_token_response(minted.token),
        plaintext=minted.plaintext,
    )


@me_router.delete("/mcp-tokens/{token_id}", response_model=RevokeTokenResponse)
async def revoke_own_token(
    token_id: UUID,
    current_user: SessionUserDep,
    tokens: McpTokenServiceDep,
) -> RevokeTokenResponse:
    """Revoke one of the caller's own tokens.

    The only id a caller supplies on this surface, and the user id it is scoped by is the
    session's — so a colleague's `token_id` deletes nothing and answers 404, exactly as a
    fabricated one does. Ownership is a clause in the statement, not a check above it.
    """
    await tokens.revoke(current_user.user_id, token_id, actor_email=current_user.email)
    return RevokeTokenResponse(ok=True)
