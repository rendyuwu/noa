class AuthError(Exception):
    pass


class AuthInvalidCredentialsError(AuthError):
    pass


class AuthPendingApprovalError(AuthError):
    error_code: str = "user_pending_approval"


class AuthRateLimitedError(AuthError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many login attempts")
        self.retry_after_seconds = retry_after_seconds


class AuthConfigurationError(AuthError):
    pass
