"""`McpTokenService` policy over in-memory doubles (T10 — C5, V2, V3, V8, V14, V73).

`test_mcp_token_repository.py` covers the SQL. Everything here is about the rules: what the
plaintext may touch, what a view may carry, who may revoke what, and what lands in the
audit trail.

The service is the real one; only the repository and the audit sink are doubles, so a pass
here means the production class behaves this way.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields
from uuid import uuid4

import pytest
from fastapi import status

from core.audit.admin_events import EVENT_MCP_TOKEN_MINTED, EVENT_MCP_TOKEN_REVOKED
from core.auth.authorization_errors import UserNotFoundError
from core.auth.mcp_token_errors import (
    InvalidTokenLabelError,
    McpTokenError,
    McpTokenNotFoundError,
)
from core.auth.mcp_token_service import (
    MAX_LABEL_LENGTH,
    TOKEN_MARKER,
    TOKEN_PREFIX_LENGTH,
    McpTokenView,
    generate_mcp_token,
    hash_mcp_token,
)
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, error_body, status_for
from support.mcp_tokens import LABEL, NOW, OTHER_LABEL, build_token_service

ACTOR = "admin@example.com"


# --- V2: the plaintext (mint returns it once, nothing else ever does) ---


async def test_mint_returns_a_marked_high_entropy_plaintext() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.plaintext.startswith(TOKEN_MARKER)
    # 32 urlsafe-base64 bytes render as 43 characters, plus the marker.
    assert len(minted.plaintext) == len(TOKEN_MARKER) + 43


async def test_each_mint_generates_a_distinct_token() -> None:
    """V2: two tokens for one user are two credentials, not the same one twice."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    first = await fixture.service.mint(user_id)
    second = await fixture.service.mint(user_id)

    assert first.plaintext != second.plaintext
    assert len(set(fixture.repository.stored_hashes)) == 2


async def test_mint_stores_the_sha256_digest_and_never_the_plaintext() -> None:
    """V2: hashed at rest. The digest is what the verify path (T11) will look up."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    expected = hashlib.sha256(minted.plaintext.encode("utf-8")).hexdigest()
    assert fixture.repository.stored_hashes == [expected]
    assert len(expected) == 64
    assert minted.plaintext not in fixture.repository.stored_hashes


async def test_list_never_returns_the_plaintext() -> None:
    """V2: `McpTokenView` has no field that could hold it — absent, not redacted."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    minted = await fixture.service.mint(user_id)

    listed = await fixture.service.list_for_user(user_id)

    field_names = {field.name for field in fields(McpTokenView)}
    assert "token_hash" not in field_names
    assert "plaintext" not in field_names
    assert minted.plaintext not in str(listed)


async def test_minted_token_repr_omits_the_plaintext() -> None:
    """V2 "⊥ logged" / V8: structlog and tracebacks both render values with `repr()`."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.plaintext not in repr(minted)


async def test_stored_prefix_is_a_short_non_secret_fragment() -> None:
    """The display fragment matches the head of the plaintext and fits `String(16)` (T4)."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.token.token_prefix == minted.plaintext[:TOKEN_PREFIX_LENGTH]
    assert len(minted.token.token_prefix) <= 16
    assert minted.token.token_prefix != minted.plaintext


def test_hash_is_stable_across_calls() -> None:
    """T11 hashes the presented bearer with this same function, so it must not vary."""
    plaintext = generate_mcp_token()

    assert hash_mcp_token(plaintext) == hash_mcp_token(plaintext)
    assert hash_mcp_token(plaintext) != hash_mcp_token(generate_mcp_token())


# --- V2: token ↔ users.id ---


async def test_mint_binds_the_token_to_the_user() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.token.user_id == user_id


async def test_list_returns_only_that_users_tokens() -> None:
    """V2: a token belongs to one operator, so a colleague's never appears in the list."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    other_user_id = fixture.repository.add_user()
    mine = await fixture.service.mint(user_id, label=LABEL)
    await fixture.service.mint(other_user_id, label=OTHER_LABEL)

    listed = await fixture.service.list_for_user(user_id)

    assert [view.id for view in listed] == [mine.token.id]


async def test_list_is_newest_first() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    first = await fixture.service.mint(user_id)
    second = await fixture.service.mint(user_id)

    listed = await fixture.service.list_for_user(user_id)

    assert [view.id for view in listed] == [second.token.id, first.token.id]


async def test_list_for_an_unknown_user_is_not_found() -> None:
    """Not an empty list: "no tokens" and "no such user" are different answers."""
    fixture = build_token_service()

    with pytest.raises(UserNotFoundError):
        await fixture.service.list_for_user(uuid4())


async def test_mint_for_an_unknown_user_is_not_found() -> None:
    """Refused here rather than by the foreign key, which would surface as a 500."""
    fixture = build_token_service()

    with pytest.raises(UserNotFoundError):
        await fixture.service.mint(uuid4())

    assert fixture.repository.stored_hashes == []


# --- V3 / C20: TOFU binding is T11's, and mint must leave room for it ---


async def test_mint_leaves_librechat_user_id_null() -> None:
    """C20: NULL at mint is the precondition for first-use binding. Set here = no TOFU."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.token.librechat_user_id is None
    assert minted.token.last_used_at is None
    assert minted.token.last_ldap_check_at is None


# --- C5: expiry ---


async def test_mint_without_a_ttl_leaves_expires_at_null() -> None:
    """Default `MCP_TOKEN_TTL_SECONDS` is unset: live until the row is deleted (V2)."""
    fixture = build_token_service(ttl_seconds=None)
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id)

    assert minted.token.expires_at is None


async def test_mint_sets_expires_at_from_the_ttl() -> None:
    fixture = build_token_service(ttl_seconds=3600)
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, now=NOW)

    assert minted.token.expires_at is not None
    assert (minted.token.expires_at - NOW).total_seconds() == 3600


# --- Labels ---


async def test_blank_label_normalizes_to_none() -> None:
    """`label` is nullable; an unnamed token is legitimate, not a malformed request."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, label="   ")

    assert minted.token.label is None


async def test_label_is_stripped() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, label=f"  {LABEL}  ")

    assert minted.token.label == LABEL


async def test_over_long_label_is_refused_before_the_insert() -> None:
    """Otherwise `String(255)` raises in the database and the client sees a 500."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    with pytest.raises(InvalidTokenLabelError):
        await fixture.service.mint(user_id, label="x" * (MAX_LABEL_LENGTH + 1))

    assert fixture.repository.stored_hashes == []


async def test_label_at_the_limit_is_accepted() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, label="x" * MAX_LABEL_LENGTH)

    assert minted.token.label is not None
    assert len(minted.token.label) == MAX_LABEL_LENGTH


# --- V2: revoke ---


async def test_revoke_removes_the_token_from_the_list() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    minted = await fixture.service.mint(user_id)

    await fixture.service.revoke(user_id, minted.token.id)

    assert await fixture.service.list_for_user(user_id) == []
    assert fixture.repository.stored_hashes == []


async def test_revoking_another_users_token_is_not_found() -> None:
    """Scoped by user id in the query, so a guessed id cannot reach a colleague's row.

    Same error as an id that never existed: a distinct 403 would confirm the token is
    real (V27/V76 principle).
    """
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    other_user_id = fixture.repository.add_user()
    theirs = await fixture.service.mint(other_user_id)

    with pytest.raises(McpTokenNotFoundError):
        await fixture.service.revoke(user_id, theirs.token.id)

    assert len(await fixture.service.list_for_user(other_user_id)) == 1


async def test_revoking_an_unknown_token_is_not_found() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    with pytest.raises(McpTokenNotFoundError):
        await fixture.service.revoke(user_id, uuid4())


async def test_second_revoke_of_the_same_token_is_not_found() -> None:
    """A double-click must not report success twice for one deletion."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    minted = await fixture.service.mint(user_id)
    await fixture.service.revoke(user_id, minted.token.id)

    with pytest.raises(McpTokenNotFoundError):
        await fixture.service.revoke(user_id, minted.token.id)


# --- V14 / V8: audit ---


async def test_mint_and_revoke_each_record_an_audit_event() -> None:
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, label=LABEL, actor_email=ACTOR)
    await fixture.service.revoke(user_id, minted.token.id, actor_email=ACTOR)

    assert fixture.audit.event_types == [EVENT_MCP_TOKEN_MINTED, EVENT_MCP_TOKEN_REVOKED]
    assert all(event.actor_email == ACTOR for event in fixture.audit.events)
    assert all(event.target == str(minted.token.id) for event in fixture.audit.events)


async def test_audit_event_carries_no_plaintext_or_hash() -> None:
    """V2 "⊥ logged" / V8: ids, prefix and label only."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, label=LABEL, actor_email=ACTOR)

    event = fixture.audit.events[0]
    rendered = str(event)
    assert minted.plaintext not in rendered
    assert fixture.repository.stored_hashes[0] not in rendered
    assert event.metadata["token_prefix"] == minted.token.token_prefix
    assert event.metadata["target_user_id"] == str(user_id)


async def test_a_refused_mint_records_nothing() -> None:
    """An audit trail that logs attempts nobody made is one nobody trusts."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    with pytest.raises(InvalidTokenLabelError):
        await fixture.service.mint(user_id, label="x" * (MAX_LABEL_LENGTH + 1))

    assert fixture.audit.events == []


# --- V73: status mapping ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (McpTokenNotFoundError(), status.HTTP_404_NOT_FOUND),
        (InvalidTokenLabelError(), status.HTTP_400_BAD_REQUEST),
        (McpTokenError(), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_status_for_every_mcp_token_error(error: McpTokenError, expected_status: int) -> None:
    assert status_for(error) == expected_status


def test_every_mcp_token_error_is_mapped_explicitly() -> None:
    """No token error may reach the 503 fallback — that answer means "NOA is down"."""

    def subclasses(klass: type[McpTokenError]) -> set[type[McpTokenError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(McpTokenError):
        status_code = status_for(klass.__new__(klass))
        assert status_code != FALLBACK_STATUS, f"{klass.__name__} falls back to 503"
        assert status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}


def test_mcp_token_error_codes_are_unique() -> None:
    """Clients branch on `error_code`, so two classes sharing one string is a bug."""
    codes = [klass.error_code for klass in STATUS_BY_ERROR if issubclass(klass, McpTokenError)]
    assert len(codes) == len(set(codes))


def test_error_body_omits_internal_detail_for_token_errors() -> None:
    """V8: `detail` names the ids that missed. Logs only."""
    error = McpTokenNotFoundError("no `mcp_tokens` row `deadbeef` for user `cafebabe`")

    body = error_body(error)

    assert body == {"error_code": "mcp_token_not_found", "message": error.message}
    assert "deadbeef" not in str(body)


# --- V100: the transaction boundary this service owns (T53) ---


async def test_mint_commits_once_and_last() -> None:
    """V100(a): the commit is the last statement, so the plaintext is not handed back until
    the row it hashes to is durable.

    `committed` rather than `tokens` is the assertion surface: the double cannot roll back, so
    the mutable dict holds the row whether or not a boundary exists — which is precisely why
    B10 went unnoticed for two tasks.
    """
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    minted = await fixture.service.mint(user_id, actor_email=ACTOR)

    assert fixture.repository.commits == 1
    assert list(fixture.repository.committed) == [minted.token.id]


async def test_revoke_commits_once_and_last() -> None:
    """V100(b): the *class* of mutations commits, not the one a caller reaches first.

    The dangerous half of the pair: an uncommitted revoke reports the credential gone while the
    row, and every request it authenticates, survives.
    """
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    minted = await fixture.service.mint(user_id, actor_email=ACTOR)

    await fixture.service.revoke(user_id, minted.token.id, actor_email=ACTOR)

    assert fixture.repository.commits == 2
    assert fixture.repository.committed == {}


async def test_a_refused_mint_commits_nothing() -> None:
    """V100(a): the `user_exists` guard raises before any write, so nothing persists."""
    fixture = build_token_service()

    with pytest.raises(UserNotFoundError):
        await fixture.service.mint(uuid4(), actor_email=ACTOR)

    assert fixture.repository.commits == 0
    assert fixture.repository.committed == {}
    assert fixture.repository.tokens == {}


async def test_a_refused_label_commits_nothing_and_writes_no_row() -> None:
    """The second guard, between the user lookup and the insert (V100(a)).

    Ordered deliberately: a validator moved *after* `insert` would leave a row behind for every
    over-long label a panel ever submitted, while still answering 400.
    """
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    with pytest.raises(InvalidTokenLabelError):
        await fixture.service.mint(user_id, label="x" * (MAX_LABEL_LENGTH + 1), actor_email=ACTOR)

    assert fixture.repository.commits == 0
    assert fixture.repository.tokens == {}


async def test_a_refused_revoke_commits_nothing() -> None:
    """A token id that matched nothing deleted nothing, so there is no transaction to end."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()
    await fixture.service.mint(user_id, actor_email=ACTOR)
    committed_before = dict(fixture.repository.committed)

    with pytest.raises(McpTokenNotFoundError):
        await fixture.service.revoke(user_id, uuid4(), actor_email=ACTOR)

    assert fixture.repository.commits == 1  # the mint's, and no second one
    assert fixture.repository.committed == committed_before


async def test_a_read_owns_no_transaction_boundary() -> None:
    """`list_for_user` commits nothing: a read that ended a transaction would end whatever the
    caller had open around it."""
    fixture = build_token_service()
    user_id = fixture.repository.add_user()

    await fixture.service.list_for_user(user_id)

    assert fixture.repository.commits == 0
