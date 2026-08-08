"""Server-inventory reads and reference resolution (T19, V18).

`core/integrations/<system>/` knows how to *talk to* a server. This package knows how to
*find* one: which rows exist, and which row an operator meant when they typed a word.
They are kept apart deliberately — the integration modules take a `Protocol`-shaped row
(`WHMServerSecretLike`) precisely so they stay independent of SQLAlchemy and of the session
that loaded it, and putting a `select()` in there would undo that.

Landed: WHM (T19), PMG (T31). Proxmox reference resolution arrives with its own tools
(T27/T28); `noa-old` had one `server_ref.py` per system and they do not share a row shape, so
the third one is what will show whether a shared abstraction is worth having (V66).

What the two that exist already say about that: the *policy* is identical — id, then name,
then host, any tie is `choices` — while the row differs at both ends. WHM matches a hostname
`urlsplit` pulls out of `base_url`; PMG matches the bare `ssh_host` column it stores, and their
`choices` entries carry different fields. Proxmox is what shows whether that difference is two
parameters or a third shape.

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
    "SQLPMGServerRepository",
    "SQLWHMServerRepository",
    "WHMServerReadRepository",
    "WHMServerRefResolution",
    "resolve_pmg_server_ref",
    "resolve_whm_server_ref",
]
