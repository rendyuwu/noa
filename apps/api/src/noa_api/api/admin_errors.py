"""Refusals the admin surface owns, that no core service can raise.

One class today, and it is here rather than in `core.auth.authorization_errors` for the
reason that file already records: direct per-user tool grants answer 410, so the RBAC
engine has *no direct-grant path to refuse*. `AuthorizationService` cannot reach this
condition — there is no user-level grant table, no `set_user_tools` method, and nothing to
validate. The refusal is a property of the HTTP surface: a route that used to exist in
`noa-old` and answers "gone" here.

There is a second, sharper reason it is not an `AuthorizationError`. That taxonomy's own
test walks the subclass tree and asserts every member maps to one of 400, 403, 404 or 409
(`test_rbac_routes.py::test_every_authorization_error_is_mapped_explicitly`). A 410 in that
tree would either fail the test or force its status set open, and the set is the assertion —
it is what stops a permission problem answering "service unavailable". So this derives from
`NoaError` directly and declares its own `status_code`.
"""

from __future__ import annotations

from core.errors import NoaError


class DirectGrantsDisabledError(NoaError):
    """`PUT /admin/users/{id}/tools` — direct per-user grants are gone.

    **410, not 404 and not 403.** 410 is the one status that says "this route existed and
    the capability behind it has been withdrawn permanently", which is exactly the fact: the
    ported admin panel and `noa-old`'s API both had it, and NOA's schema has no user-level
    grant table for it to write. A 404 would read as a deployment problem — a route someone
    forgot to mount — and would send an operator looking for the version where it works. A
    403 would read as "you are not allowed", inviting them to ask a colleague with more
    roles, when no role in NOA can do this.

    `error_code` is `noa-old`'s string verbatim (its `api/error_codes.py`), because a client
    branching on it already exists.

    The message names the replacement rather than only the refusal: permissions flow
    role → user, so the remedy is a role with the grant, and an operator who reads this
    should not have to find that out from a second request.
    """

    error_code: str = "direct_tool_grants_disabled"
    message: str = (
        "Direct per-user tool grants are no longer supported. Grant the tools to a role and "
        "assign that role to the user."
    )
    # 410, and it sits outside the authorization group on purpose: `DirectGrantsDisabledError`
    # is not an `AuthorizationError`, because that tree's own test pins its statuses to
    # {400, 403, 404, 409} and the pin is the assertion (see `noa_api.api.admin_errors`). 410
    # rather than 404 or 403: the route existed in `noa-old`, the capability is withdrawn
    # permanently, and neither "missing" nor "not allowed" says that.
    status_code = 410
