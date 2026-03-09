from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from noa_api.core.auth.errors import AuthConfigurationError, AuthInvalidCredentialsError
from noa_api.core.config import Settings

try:
    import jwt
    from jwt import ExpiredSignatureError, InvalidTokenError
except ImportError:  # pragma: no cover
    jwt = None

    class ExpiredSignatureError(Exception):
        pass

    class InvalidTokenError(Exception):
        pass


class JWTService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_access_token(self, email: str, user_id: UUID) -> tuple[str, int]:
        if jwt is None:
            raise AuthConfigurationError("PyJWT dependency is not installed")

        ttl = self._settings.auth_jwt_access_token_ttl_seconds
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": email,
                "uid": str(user_id),
                "iat": now,
                "exp": now + timedelta(seconds=ttl),
            },
            self._settings.auth_jwt_secret.get_secret_value(),
            algorithm=self._settings.auth_jwt_algorithm,
        )
        return str(token), ttl

    def decode_token(self, token: str) -> dict[str, Any]:
        if jwt is None:
            raise AuthConfigurationError("PyJWT dependency is not installed")

        try:
            payload = jwt.decode(
                token,
                self._settings.auth_jwt_secret.get_secret_value(),
                algorithms=[self._settings.auth_jwt_algorithm],
            )
        except ExpiredSignatureError as exc:
            raise AuthInvalidCredentialsError("Token expired") from exc
        except InvalidTokenError as exc:
            raise AuthInvalidCredentialsError("Invalid token") from exc

        return dict(payload)
