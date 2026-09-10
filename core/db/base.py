"""Declarative base for the single shared schema.

One DB, one schema, one `MetaData` — Alembic's `target_metadata` is
`Base.metadata`, so a table that is not imported into `core.db.models` does not
exist as far as migrations are concerned.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root of every NOA ORM model."""
