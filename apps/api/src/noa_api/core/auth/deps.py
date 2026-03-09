from collections.abc import AsyncGenerator

from noa_api.core.auth.auth_service import AuthService, SQLAuthRepository
from noa_api.core.auth.jwt_service import JWTService
from noa_api.core.auth.ldap_service import LDAPService
from noa_api.core.config import settings
from noa_api.storage.postgres.client import create_engine, create_session_factory

_engine = create_engine()
_session_factory = create_session_factory(_engine)
_ldap_service = LDAPService(settings)
_jwt_service = JWTService(settings)


async def get_auth_service() -> AsyncGenerator[AuthService, None]:
    async with _session_factory() as session:
        repository = SQLAuthRepository(session)
        service = AuthService(
            auth_repository=repository,
            ldap_service=_ldap_service,
            jwt_service=_jwt_service,
            bootstrap_admin_emails=settings.auth_bootstrap_admin_emails,
        )
        yield service
        await session.commit()
