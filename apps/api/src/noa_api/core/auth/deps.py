from collections.abc import AsyncGenerator

from noa_api.core.auth.auth_service import AuthService, SQLAuthRepository
from noa_api.core.auth.jwt_service import JWTService
from noa_api.core.auth.ldap_service import LDAPService
from noa_api.core.auth.login_rate_limiter import LoginRateLimiter
from noa_api.core.config import settings
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.login_rate_limits import SQLLoginRateLimitRepository

_PENDING_APPROVAL_ERROR_CODE = "user_pending_approval"

_ldap_service = LDAPService(settings)
_jwt_service = JWTService(settings)


async def get_auth_service() -> AsyncGenerator[AuthService, None]:
    async with get_session_factory()() as session:
        repository = SQLAuthRepository(session)
        rate_limit_repository = SQLLoginRateLimitRepository(session)
        login_rate_limiter = LoginRateLimiter(
            rate_limit_repository,
            window_seconds=settings.auth_login_rate_limit_window_seconds,
            max_attempts=settings.auth_login_rate_limit_max_attempts,
            block_seconds=settings.auth_login_rate_limit_block_seconds,
        )
        service = AuthService(
            auth_repository=repository,
            ldap_service=_ldap_service,
            jwt_service=_jwt_service,
            bootstrap_admin_emails=settings.auth_bootstrap_admin_emails,
            login_rate_limiter=login_rate_limiter,
        )
        try:
            yield service
            await session.commit()
        except Exception as exc:
            # First-time logins intentionally create an inactive user and then
            # return a 403 (pending approval). That error is expected and should
            # not roll back the user insert.
            if getattr(exc, "error_code", None) == _PENDING_APPROVAL_ERROR_CODE:
                await session.commit()
            else:
                await session.rollback()
            raise


def get_jwt_service() -> JWTService:
    return _jwt_service
