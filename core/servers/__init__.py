"""Server-inventory reads and reference resolution (T19, V18).

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

Reads only. Server CRUD belongs to the admin routes (T54) and lands with them.
"""

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
from core.servers.whm_ref import (
    WHMServerRefResolution,
    resolve_whm_server_ref,
)
from core.servers.whm_repository import (
    SQLWHMServerRepository,
    WHMServerReadRepository,
)

__all__ = [
    "PMGServerReadRepository",
    "PMGServerRefResolution",
    "ProxmoxServerReadRepository",
    "ProxmoxServerRefResolution",
    "SQLPMGServerRepository",
    "SQLProxmoxServerRepository",
    "SQLWHMServerRepository",
    "ServerRefRepository",
    "ServerRefResolution",
    "ServerRowLike",
    "WHMServerReadRepository",
    "WHMServerRefResolution",
    "resolve_pmg_server_ref",
    "resolve_proxmox_server_ref",
    "resolve_server_ref",
    "resolve_whm_server_ref",
]
