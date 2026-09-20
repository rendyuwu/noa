"""Every `NoaError` tree, and the closed set of statuses its members may answer with.

One table rather than a copy of this walk in each surface's test file: since statuses became
class attributes the thing under test is the taxonomy, not any one route. Closed sets rather
than "every class declares its own `status_code`" — seven classes deliberately inherit one
(`SelfDeleteAdminError`, `CSFCLIError`, `ImunifyCLIError`, `SecretDecryptError`,
`SecretKeyUnavailableError`, `YopassStoreError`, and `AuthError` itself), so a declared-vs-
inherited assertion would have to carry a per-tree exception list. The set is the guard: it is
what stops a permission problem answering "service unavailable".

`TREES` is a hand-kept set, so `test_every_taxonomy_has_a_row` binds it to the code. Importing
`noa_api.main` is what makes that binding real: `__subclasses__()` only sees classes whose
module has been imported, and `main` transitively imports every module the API raises from.

The gap this does not close, stated rather than implied: three trees legitimately allow 503
(`AuthError`, `ChangeGateError`, `ResultTableError`), so a class added to one of those with no
status of its own inherits the unclassified default and passes here. Nothing catches that.
"""

from __future__ import annotations

from typing import Final

import pytest

import noa_api.main  # noqa: F401  — imports every error module so `__subclasses__()` sees it
from core.approvals.errors import ActionDecisionError, ChangeGateError
from core.audit.errors import ToolRunAuditError
from core.auth.authorization_errors import AuthorizationError
from core.auth.errors import AuthError
from core.auth.mcp_auth_errors import McpAuthError
from core.auth.mcp_token_errors import McpTokenError
from core.errors import NoaError
from core.integrations.pmg.errors import PMGSHCLIError
from core.integrations.whm.errors import WHMFirewallCLIError
from core.remote_exec.errors import SSHExecutionError
from core.results.errors import ResultTableError
from core.secrets.errors import SecretCryptoError, YopassError
from core.servers.errors import ServerInventoryError
from noa_api.api.admin_errors import DirectGrantsDisabledError
from support.errors import error_subclasses

TREES: Final[list[tuple[type[NoaError], set[int]]]] = [
    (ActionDecisionError, {403, 404, 409}),
    (AuthError, {401, 403, 429, 500, 503}),
    (AuthorizationError, {400, 403, 404, 409}),
    (ChangeGateError, {500, 503}),
    (DirectGrantsDisabledError, {410}),
    (McpAuthError, {400, 401, 403, 429}),
    (McpTokenError, {400, 404}),
    (PMGSHCLIError, {502}),
    (ResultTableError, {404, 503}),
    (SSHExecutionError, {502}),
    (SecretCryptoError, {500}),
    (ServerInventoryError, {404, 409}),
    (ToolRunAuditError, {400, 404}),
    (WHMFirewallCLIError, {502}),
    (YopassError, {500, 502}),
]


@pytest.mark.parametrize("root, allowed", TREES, ids=[root.__name__ for root, _ in TREES])
def test_every_member_answers_one_of_its_trees_statuses(
    root: type[NoaError], allowed: set[int]
) -> None:
    for klass in error_subclasses(root):
        assert klass.status_code in allowed, f"{klass.__name__} answers {klass.status_code}"


def test_every_taxonomy_has_a_row() -> None:
    """A sixteenth tree fails here rather than shipping untested.

    Filtered to `core.` and `noa_api.`: `test_approved_change_execution` defines a `NoaError`
    subclass inside a function body, and whether it is still in `__subclasses__()` when this
    runs depends on garbage collection and on test order.
    """
    live = {
        klass
        for klass in NoaError.__subclasses__()
        if klass.__module__.startswith(("core.", "noa_api."))
    }
    assert live == {root for root, _ in TREES}
