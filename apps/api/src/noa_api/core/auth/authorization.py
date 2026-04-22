# core/auth/authorization.py — backward-compatible re-exports
from noa_api.core.auth.authorization_errors import (  # noqa: F401
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    ReservedRoleError,
    RoleNotFoundError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    UnknownToolError,
)
from noa_api.core.auth.authorization_repository import (  # noqa: F401
    SQLAuthorizationRepository,
)
from noa_api.core.auth.authorization_service import (  # noqa: F401
    AuthorizationService,
    get_authorization_service,
)
from noa_api.core.auth.authorization_types import (  # noqa: F401
    AuthorizationRepositoryProtocol,
    AuthorizationUser,
    DirectGrantsMigrationSummary,
)
