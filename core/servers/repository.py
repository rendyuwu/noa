"""One `SELECT`-only inventory repository, parametrised by the table.

`whm_repository.py`, `pmg_repository.py` and `proxmox_repository.py` keep the per-system
Protocols — those differ (`base_url` vs `ssh_host`, `to_safe_dict` or not) and the narrow row
views are what stop a resolver double being handed to something that decrypts. The SQL did
not differ, so there is one of it.

The session belongs to the caller, as everywhere in `core/`: nothing here commits and both
methods are `SELECT`s.
"""

from __future__ import annotations

from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import PMGServer, ProxmoxServer, WHMServer

# Constrained rather than bound: these three tables are the whole population, and naming them
# keeps `model.name` / `model.id` resolvable instead of `Any`.
ServerModelT = TypeVar("ServerModelT", WHMServer, ProxmoxServer, PMGServer)


class SQLServerRepository(Generic[ServerModelT]):
    """`{WHM,PMG,Proxmox}ServerReadRepository[Model]` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession, *, model: type[ServerModelT]) -> None:
        self._session = session
        self._model = model

    async def list_servers(self) -> list[ServerModelT]:
        """Every server of this table, ordered by name.

        Ordered in SQL rather than by the caller so the tool result, the admin list and the
        ambiguity `choices` all present one sequence — an operator picking row three out of a
        candidate list should not find a different row three next time.
        """
        result = await self._session.execute(select(self._model).order_by(self._model.name.asc()))
        return list(result.scalars().all())

    async def get_by_id(self, server_id: UUID) -> ServerModelT | None:
        """One server by primary key, or `None`.

        `None` rather than a raise: the caller is a reference resolver, and "no server with
        that id" is a `host_not_found` *result* the model can act on, not an exception.
        """
        result = await self._session.execute(select(self._model).where(self._model.id == server_id))
        return result.scalar_one_or_none()
