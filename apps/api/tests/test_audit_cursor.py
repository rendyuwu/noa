"""Audit page tokens: encode, decode, refuse (T55 — V8, V73).

Ported alongside `core.audit.cursor` from `noa-old` (C13), and tested here rather than trusted:
upstream provenance is not evidence that a control works (V69), and the two things this module gets
wrong quietly are both here — a naive timestamp silently shifting a page boundary, and a malformed
token answering with something other than one refusal.

The keyset predicate has its own coverage where it matters: `test_tool_run_audit_read.py` asserts it
reaches the statement, and `test_admin_audit_live.py` walks real pages with it. What is asserted
here is its *logic* against known values, which needs no database.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators
from sqlalchemy.sql.sqltypes import Uuid

from core.audit.cursor import (
    ID_FIELD,
    TIMESTAMP_FIELD,
    KeysetCursor,
    decode_cursor,
    encode_cursor,
    keyset_predicate,
)
from core.audit.errors import InvalidAuditCursorError

AT = datetime(2026, 8, 19, 10, 30, 15, 123456, tzinfo=UTC)


def token_for(payload: object) -> str:
    """A token carrying `payload`, however malformed — the encoder only makes valid ones."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_a_cursor_round_trips() -> None:
    """The token decodes to the value it was made from — microseconds included.

    Microseconds because they are the tie-break's whole point: a token truncated to the second would
    put two runs from the same second on the wrong side of the page boundary.
    """
    cursor = KeysetCursor(timestamp=AT, entity_id=uuid4())

    assert decode_cursor(encode_cursor(cursor)) == cursor


def test_the_token_is_url_safe_and_unpadded() -> None:
    """It rides in a query string, so `+`, `/` and `=` would each need escaping somewhere."""
    token = encode_cursor(KeysetCursor(timestamp=AT, entity_id=uuid4()))

    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"), (
        token
    )


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """`tool_runs.created_at` is `timestamptz`, so a naive value has to be given a zone.

    Rejecting it instead would be defensible; silently comparing it is not — the page boundary would
    move by the server's offset, so an operator in a non-UTC deployment would see a page skip hours
    of runs and nothing would error. Upstream's normalisation, kept and asserted.
    """
    decoded = decode_cursor(
        token_for({TIMESTAMP_FIELD: "2026-08-19T10:30:15", ID_FIELD: str(uuid4())})
    )

    assert decoded.timestamp == datetime(2026, 8, 19, 10, 30, 15, tzinfo=UTC)


def test_an_offset_timestamp_is_converted_to_utc() -> None:
    """A `+07:00` instant decodes to the same moment in UTC, not to a shifted wall clock."""
    jakarta = timezone(timedelta(hours=7))
    at = datetime(2026, 8, 19, 17, 30, 15, tzinfo=jakarta)

    decoded = decode_cursor(token_for({TIMESTAMP_FIELD: at.isoformat(), ID_FIELD: str(uuid4())}))

    assert decoded.timestamp == datetime(2026, 8, 19, 10, 30, 15, tzinfo=UTC)
    assert decoded.timestamp.tzinfo is UTC


@pytest.mark.parametrize(
    ("cursor", "why"),
    [
        ("", "empty"),
        ("   ", "whitespace only"),
        ("!!!!", "not base64"),
        (base64.urlsafe_b64encode(b"not json").decode().rstrip("="), "not JSON"),
        (token_for([1, 2]), "not an object"),
        (token_for({}), "no fields"),
        (token_for({TIMESTAMP_FIELD: AT.isoformat()}), "no id"),
        (token_for({ID_FIELD: str(uuid4())}), "no timestamp"),
        (token_for({TIMESTAMP_FIELD: 1234, ID_FIELD: str(uuid4())}), "timestamp not a string"),
        (token_for({TIMESTAMP_FIELD: AT.isoformat(), ID_FIELD: 7}), "id not a string"),
        (
            token_for({TIMESTAMP_FIELD: "yesterday", ID_FIELD: str(uuid4())}),
            "timestamp unparseable",
        ),
        (token_for({TIMESTAMP_FIELD: AT.isoformat(), ID_FIELD: "not-a-uuid"}), "id not a UUID"),
    ],
)
def test_every_malformed_cursor_answers_one_refusal(cursor: str, why: str) -> None:
    """Twelve shapes, one `error_code`: the remedy is the same for all of them (re-read page one).

    A `NoaError` rather than FastAPI's `RequestValidationError`, which is what upstream raised: the
    body is the shared envelope, so the diagnostic string below stays in `detail` and out of the
    response (V8, V73).
    """
    with pytest.raises(InvalidAuditCursorError) as raised:
        decode_cursor(cursor)

    assert raised.value.error_code == "invalid_audit_cursor", why
    assert raised.value.detail, "the refusal carries a diagnostic for the log, not for the body"


def test_a_padded_token_still_decodes() -> None:
    """Padding is stripped on the way out and re-added on the way in, so both spellings work.

    A client that round-trips the token through something that re-pads it — a URL builder, a form —
    must not lose its place in the list.
    """
    cursor = KeysetCursor(timestamp=AT, entity_id=uuid4())
    token = encode_cursor(cursor)

    assert decode_cursor(token + "==") == cursor


def _predicate_sql(cursor: KeysetCursor) -> str:
    created_at = Column("created_at", DateTime(timezone=True))
    row_id = Column("id", Uuid())
    return str(
        keyset_predicate(timestamp_column=created_at, id_column=row_id, cursor=cursor).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


def test_the_predicate_is_a_timestamp_step_or_an_id_tie_break() -> None:
    """`ts < :ts OR (ts = :ts AND id < :id)` — the shape a `(DESC, DESC)` page continues with.

    Read off the compiled clause rather than off the source, and both branches named: a predicate
    that kept only the timestamp step would page correctly right up until two runs shared a
    millisecond, and then it would drop the rest of that group.
    """
    sql = _predicate_sql(KeysetCursor(timestamp=AT, entity_id=uuid4()))

    assert "created_at <" in sql
    assert "created_at =" in sql
    assert "id <" in sql
    assert " OR " in sql
    assert " AND " in sql


def test_the_predicate_composes_as_sqlalchemy_boolean_clauses() -> None:
    """It is an `OR` of a comparison and an `AND`, so it can be handed straight to `.where()`.

    Structural as well as textual, because the statement builder relies on this: a helper returning
    a string would still be accepted by `.where()` and would evaluate differently — the failure mode
    being a page whose cursor filters nothing.
    """
    clause = keyset_predicate(
        timestamp_column=Column("created_at", DateTime(timezone=True)),
        id_column=Column("id", Uuid()),
        cursor=KeysetCursor(timestamp=AT, entity_id=UUID(int=7)),
    )

    assert clause.operator is operators.or_
    assert len(clause.clauses) == 2

    inner = list(clause.clauses)[1]
    assert inner.operator is operators.and_
    assert len(inner.clauses) == 2
