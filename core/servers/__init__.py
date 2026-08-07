"""Server-inventory reads and reference resolution (T19, V18).

`core/integrations/<system>/` knows how to *talk to* a server. This package knows how to
*find* one: which rows exist, and which row an operator meant when they typed a word.
They are kept apart deliberately — the integration modules take a `Protocol`-shaped row
(`WHMServerSecretLike`) precisely so they stay independent of SQLAlchemy and of the session
that loaded it, and putting a `select()` in there would undo that.

Landed: WHM (T19). PMG and Proxmox reference resolution arrive with their own tools
(T27-T31); `noa-old` had one `server_ref.py` per system and they do not share a row shape,
so the third one is what will show whether a shared abstraction is worth having (V66).

Reads only. Server CRUD belongs to the admin routes (T54) and lands with them.
"""

from core.servers.whm_ref import (
    WHMServerRefResolution,
    resolve_whm_server_ref,
)
from core.servers.whm_repository import (
    SQLWHMServerRepository,
    WHMServerReadRepository,
)

__all__ = [
    "SQLWHMServerRepository",
    "WHMServerReadRepository",
    "WHMServerRefResolution",
    "resolve_whm_server_ref",
]
