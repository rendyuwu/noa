import logging

from fastapi import Depends, Request, status

from noa_api.api.auth_dependencies import get_current_auth_user
from noa_api.api.error_codes import ADMIN_ACCESS_REQUIRED
from noa_api.api.error_handling import ApiHTTPException
from noa_api.api.route_telemetry import record_route_outcome
from noa_api.core.auth.authorization import AuthorizationUser

logger = logging.getLogger(__name__)
ADMIN_OUTCOMES_TOTAL = "admin.outcomes.total"


def _record_admin_outcome(
    request: Request,
    *,
    event_name: str,
    status_code: int,
    trace_attributes: dict[str, str | int | bool],
    error_code: str | None = None,
) -> None:
    record_route_outcome(
        request,
        metric_name=ADMIN_OUTCOMES_TOTAL,
        event_name=event_name,
        status_code=status_code,
        trace_attributes=trace_attributes,
        error_code=error_code,
    )


async def _require_admin(
    request: Request,
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> AuthorizationUser:
    if not current_user.is_active or "admin" not in current_user.roles:
        logger.info(
            "admin_access_denied",
            extra={
                "is_active": current_user.is_active,
                "roles": current_user.roles,
                "user_id": str(current_user.user_id),
            },
        )
        _record_admin_outcome(
            request,
            event_name="admin_access_denied",
            status_code=status.HTTP_403_FORBIDDEN,
            trace_attributes={
                "is_active": current_user.is_active,
                "user_id": str(current_user.user_id),
            },
            error_code=ADMIN_ACCESS_REQUIRED,
        )
        raise ApiHTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
            error_code=ADMIN_ACCESS_REQUIRED,
        )
    return current_user
