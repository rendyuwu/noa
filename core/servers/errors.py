"""Server-inventory error taxonomy (T54, V8, V73).

The refusals the admin CRUD surface raises, and nothing else. Written the way
`core.auth.authorization_errors` is, for the same three reasons:

1. Every class derives from `core.errors.NoaError`, so it carries an `error_code` and an
   operator-facing `message`, and `noa_api.api.errors` decides the status once.
   `noa-old`'s three admin route files each built their own `ApiHTTPException` per failure —
   ~40 lines an endpoint — which is how one condition ends up as two statuses.
2. The `error_code` strings are `noa-old`'s verbatim (`api/error_codes.py`):
   `whm_server_not_found`, `whm_server_name_exists`, and the Proxmox and PMG equivalents.
   The admin panel ported at T48 branches on them already
   (`apps/admin-web/src/lib/admin/*/[system]-api.ts` surfaces them to the operator), so a
   new spelling would break a client that exists (C13).
3. One class per code rather than one class with a `system` field. `STATUS_BY_ERROR` maps
   classes, and its subclass-tree test walks this tree asserting every member is mapped
   explicitly — a dynamic `error_code` would satisfy that test while making the mapping
   unreadable.

**Nine classes, and the per-system split is deliberate.** A single
`ServerNotFoundError` would answer `server_not_found` for all three tables, which reads
fine in a body and badly in a log: the three verticals are three panel pages against three
tables, and "which inventory was this" is the first thing anybody asks. `noa-old` made the
same call.

**Why 404 and not 403 for an absent row.** These routes sit behind `require_admin` (V13),
so there is no existence secret to keep from the caller in the V27 sense — an admin may
list every server. 404 is here because it is *true*: the row is gone, and 409 or 403 would
send an operator looking for a permission they already have.

**Why 409 for a duplicate name.** `whm_servers.name`, `proxmox_servers.name` and
`pmg_servers.name` are all `unique=True` (T4), and the name is what
`resolve_*_server_ref` matches on (V18) — two rows with one name would make every
ambiguous-reference answer worse, not better. The request is well-formed and refused
because it would break that, which is what 409 says. Not 422: the value is a valid server
name, it is the *state of the table* that refuses it, and a caller cannot tell from the
schema which names are taken.
"""

from __future__ import annotations

from core.errors import NoaError


class ServerInventoryError(NoaError):
    """Base for every refusal the server-inventory admin surface raises.

    Mapped explicitly (409) rather than left to the handler's 503 fallback, which would read
    as "NOA is down" for something NOA decided. A test walks this tree and asserts each
    subclass has its own entry, so a class added later is a visible failure instead of a
    wrong status in production.
    """

    error_code: str = "server_inventory_failed"
    message: str = "That server change could not be applied."


# --- WHM (`whm_servers`) ---


class WHMServerNotFoundError(ServerInventoryError):
    """No `whm_servers` row for the id in the path.

    Raised by update, delete and validate. The message names the list rather than the id: a
    stale panel link is the common cause, and reloading the page is the remedy.
    """

    error_code: str = "whm_server_not_found"
    message: str = "That WHM server does not exist. Reload the server list and try again."


class WHMServerNameExistsError(ServerInventoryError):
    """Another `whm_servers` row already holds that name."""

    error_code: str = "whm_server_name_exists"
    message: str = "A WHM server with that name already exists. Choose a different name."


# --- Proxmox (`proxmox_servers`) ---


class ProxmoxServerNotFoundError(ServerInventoryError):
    """No `proxmox_servers` row for the id in the path."""

    error_code: str = "proxmox_server_not_found"
    message: str = "That Proxmox server does not exist. Reload the server list and try again."


class ProxmoxServerNameExistsError(ServerInventoryError):
    """Another `proxmox_servers` row already holds that name."""

    error_code: str = "proxmox_server_name_exists"
    message: str = "A Proxmox server with that name already exists. Choose a different name."


# --- PMG (`pmg_servers`) ---


class PMGServerNotFoundError(ServerInventoryError):
    """No `pmg_servers` row for the id in the path."""

    error_code: str = "pmg_server_not_found"
    message: str = "That PMG server does not exist. Reload the server list and try again."


class PMGServerNameExistsError(ServerInventoryError):
    """Another `pmg_servers` row already holds that name."""

    error_code: str = "pmg_server_name_exists"
    message: str = "A PMG server with that name already exists. Choose a different name."


__all__ = [
    "PMGServerNameExistsError",
    "PMGServerNotFoundError",
    "ProxmoxServerNameExistsError",
    "ProxmoxServerNotFoundError",
    "ServerInventoryError",
    "WHMServerNameExistsError",
    "WHMServerNotFoundError",
]
