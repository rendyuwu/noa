class AuthError(Exception):
    pass


class AuthInvalidCredentialsError(AuthError):
    pass


class AuthPendingApprovalError(AuthError):
    pass


class AuthConfigurationError(AuthError):
    pass
