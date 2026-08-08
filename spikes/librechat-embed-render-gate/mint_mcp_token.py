"""Mint the MCP bearer token LibreChat authenticates with (harness support for T59).

Direct service call rather than an HTTP request because the routes that will do this —
`/admin/users/{id}/tokens` and `/me/mcp-tokens` (I.admin-api) — are T51-T55 and not built
yet. The token itself is the real thing: `McpTokenService.mint` (T10), hashed at rest,
plaintext returned once (V2), `librechat_user_id` NULL so LibreChat's first call binds it
(V3 TOFU).

The operator row is created the way a first login creates it: through `AuthService` with
`AUTH_DEV_BYPASS_LDAP=true`, so bootstrap activation (V7) is what activates it rather than
a hand-written INSERT.

    uv run python spikes/librechat-embed-render-gate/mint_mcp_token.py operator@noa.internal
"""

from __future__ import annotations

import asyncio
import sys

from core.audit.admin_events import StructlogAdminAuditSink
from core.auth.auth_repository import SQLAuthRepository, SQLLoginRateLimitRepository
from core.auth.auth_service import AuthService
from core.auth.jwt_service import JWTService
from core.auth.ldap_service import LDAPService
from core.auth.login_rate_limiter import LoginRateLimiter
from core.auth.mcp_token_repository import SQLMcpTokenRepository
from core.auth.mcp_token_service import McpTokenService
from core.config import get_settings
from core.db.session import create_engine, create_session_factory


async def main(email: str, password: str) -> int:
    settings = get_settings()
    if not settings.auth_dev_bypass_ldap:
        print("refusing: AUTH_DEV_BYPASS_LDAP is not on — this harness does not talk to LDAP")
        return 2

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            auth_service = AuthService(
                repository=SQLAuthRepository(session),
                directory=LDAPService(settings),
                jwt_service=JWTService(settings),
                rate_limiter=LoginRateLimiter(
                    SQLLoginRateLimitRepository(session),
                    window_seconds=settings.auth_login_rate_limit_window_seconds,
                    max_attempts=settings.auth_login_rate_limit_max_attempts,
                    block_seconds=settings.auth_login_rate_limit_block_seconds,
                ),
                bootstrap_admin_emails=settings.auth_bootstrap_admin_emails,
            )
            authenticated = await auth_service.authenticate(
                email=email, password=password, source_ip="127.0.0.1"
            )
            await session.commit()

            token_service = McpTokenService(
                repository=SQLMcpTokenRepository(session),
                audit_sink=StructlogAdminAuditSink(),
                ttl_seconds=settings.mcp_token_ttl_seconds,
            )
            minted = await token_service.mint(
                authenticated.user.user_id,
                label="librechat-embed-render-gate",
                actor_email=email,
            )
            await session.commit()
    finally:
        await engine.dispose()

    # Plaintext to stdout once, by design (V2). The harness README says to paste it into
    # `librechat.yaml`; nothing here writes it to disk.
    print(minted.plaintext)
    return 0


if __name__ == "__main__":
    argv_email = sys.argv[1] if len(sys.argv) > 1 else "operator@noa.internal"
    argv_password = sys.argv[2] if len(sys.argv) > 2 else "dev-bypass"
    raise SystemExit(asyncio.run(main(argv_email, argv_password)))
