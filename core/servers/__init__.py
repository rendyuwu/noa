"""Server inventory: reads, reference resolution, and the admin write path (T19, T54, V18).

`core/integrations/<system>/` knows how to *talk to* a server. This package knows how to
*find* one: which rows exist, and which row an operator meant when they typed a word.
They are kept apart deliberately — the integration modules take a `Protocol`-shaped row
(`WHMServerSecretLike`) precisely so they stay independent of SQLAlchemy and of the session
that loaded it, and putting a `select()` in there would undo that.

Landed: WHM (T19), PMG (T31), Proxmox (T27).

**The question this docstring carried is answered.** T19 noted that `noa-old` had one
`server_ref.py` per system and that the third would show "whether that difference is two
parameters or a third shape". Proxmox arrived at T27 and is WHM's shape exactly — a hostname
parsed out of `base_url`, a candidate described by id/name/`base_url` — so it was two
parameters: how a row yields its host, and what a `choices` entry carries. The policy moved to
`core.servers.reference` (id, then name, then host; any tie is `choices`; a well-formed id stops
rather than falling through) and the three per-system modules shrank to their row's own facts
(V66). Codes and messages are unchanged, which is what the two existing resolver test files hold.

**The write half landed at T54**, in three modules kept apart from the three read ones on
purpose: the MCP tool path holds a read repository to resolve a reference, and it must not hold
an object that can delete a server (`admin_repository`). `admin_service` owns the rules — the
case-insensitive name check, the one encryption site, an audit event per mutation, and the
commit V100 requires. `validation` owns `POST …/validate`, which is separate again because it
opens a socket to somebody else's host and may write exactly one column: the host-key pin
(V82). `naming` and `errors` are shared by all of them.
"""

from core.servers.admin_repository import (
    PMGServerAdminRepository,
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerAdminRepository,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    SQLPMGHostKeyPinRepository,
    SQLPMGServerAdminRepository,
    SQLProxmoxServerAdminRepository,
    SQLWHMHostKeyPinRepository,
    SQLWHMServerAdminRepository,
    SSHCredentials,
    SSHCredentialsPatch,
    WHMServerAdminRepository,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.admin_service import (
    PMGServerAdminService,
    ProxmoxServerAdminService,
    WHMServerAdminService,
)
from core.servers.errors import (
    PMGServerNameExistsError,
    PMGServerNotFoundError,
    ProxmoxServerNameExistsError,
    ProxmoxServerNotFoundError,
    ServerInventoryError,
    WHMServerNameExistsError,
    WHMServerNotFoundError,
)
from core.servers.naming import (
    normalize_https_base_url,
    normalize_ssh_host,
    validate_server_name,
)
from core.servers.pmg_ref import (
    PMGServerRefResolution,
    resolve_pmg_server_ref,
)
from core.servers.pmg_repository import (
    PMGServerReadRepository,
    SQLPMGServerRepository,
)
from core.servers.proxmox_ref import (
    ProxmoxServerRefResolution,
    resolve_proxmox_server_ref,
)
from core.servers.proxmox_repository import (
    ProxmoxServerReadRepository,
    SQLProxmoxServerRepository,
)
from core.servers.reference import (
    ServerRefRepository,
    ServerRefResolution,
    ServerRowLike,
    resolve_server_ref,
)
from core.servers.validation import (
    PMGServerValidationService,
    ProxmoxServerValidationService,
    ServerValidationResult,
    WHMServerValidationService,
)
from core.servers.whm_ref import (
    WHMServerRefResolution,
    resolve_whm_server_ref,
)
from core.servers.whm_repository import (
    SQLWHMServerRepository,
    WHMServerReadRepository,
)

__all__ = [
    "PMGServerAdminRepository",
    "PMGServerAdminService",
    "PMGServerCreate",
    "PMGServerNameExistsError",
    "PMGServerNotFoundError",
    "PMGServerReadRepository",
    "PMGServerRefResolution",
    "PMGServerUpdate",
    "PMGServerValidationService",
    "ProxmoxServerAdminRepository",
    "ProxmoxServerAdminService",
    "ProxmoxServerCreate",
    "ProxmoxServerNameExistsError",
    "ProxmoxServerNotFoundError",
    "ProxmoxServerReadRepository",
    "ProxmoxServerRefResolution",
    "ProxmoxServerUpdate",
    "ProxmoxServerValidationService",
    "SQLPMGHostKeyPinRepository",
    "SQLPMGServerAdminRepository",
    "SQLPMGServerRepository",
    "SQLProxmoxServerAdminRepository",
    "SQLProxmoxServerRepository",
    "SQLWHMHostKeyPinRepository",
    "SQLWHMServerAdminRepository",
    "SQLWHMServerRepository",
    "SSHCredentials",
    "SSHCredentialsPatch",
    "ServerInventoryError",
    "ServerRefRepository",
    "ServerRefResolution",
    "ServerRowLike",
    "ServerValidationResult",
    "WHMServerAdminRepository",
    "WHMServerAdminService",
    "WHMServerCreate",
    "WHMServerNameExistsError",
    "WHMServerNotFoundError",
    "WHMServerReadRepository",
    "WHMServerRefResolution",
    "WHMServerUpdate",
    "WHMServerValidationService",
    "normalize_https_base_url",
    "normalize_ssh_host",
    "resolve_pmg_server_ref",
    "resolve_proxmox_server_ref",
    "resolve_server_ref",
    "resolve_whm_server_ref",
    "validate_server_name",
]
