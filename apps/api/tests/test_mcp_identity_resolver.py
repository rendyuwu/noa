"""`McpIdentityResolver` policy over in-memory doubles (T11 — C5, C20, V1, V2, V3, V4).

`test_mcp_identity_repository.py` covers the SQL and `test_mcp_token_verifier.py` covers the
fastmcp adapter. Everything here is about the rules: which gate closes first, what TOFU
binds and refuses, when the directory is consulted, and what a directory outage may and may
not do.

The resolver is the real one; only the repository and the directory are doubles, so a pass
here means the production class behaves this way.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta

import pytest

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import (
    LibreChatUserHeaderMissingError,
    LibreChatUserMismatchError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
    McpUserInactiveError,
    McpUserNotInDirectoryError,
)
from core.auth.mcp_identity import McpIdentity
from core.auth.mcp_token_service import generate_mcp_token, hash_mcp_token
from support.mcp_identity import (
    DISPLAY_NAME,
    EMAIL,
    LIBRECHAT_USER,
    NOW,
    OTHER_LIBRECHAT_USER,
    REVALIDATE_SECONDS,
    build_resolver,
    stale_check,
)

# --- V2: the digest is the lookup key ---


async def test_lookup_is_by_sha256_digest() -> None:
    """V2: what the row holds is `sha256(plaintext)`, and that is what resolves a caller."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token()

    assert stored.token_hash == hash_mcp_token(plaintext)

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert identity.token_id == stored.token_id
    assert identity.user_id == stored.user_id


async def test_an_unknown_token_is_invalid() -> None:
    fixture = build_resolver()
    fixture.repository.add_token()

    with pytest.raises(McpTokenInvalidError):
        await fixture.resolver.resolve(
            presented_token=generate_mcp_token(), librechat_user_id=LIBRECHAT_USER, now=NOW
        )


@pytest.mark.parametrize("presented", [None, "", "   "])
async def test_absent_or_blank_bearer_is_missing_not_invalid(presented: str | None) -> None:
    """Different remedy: `NOA_MCP_TOKEN` is unset, not wrong."""
    fixture = build_resolver()

    with pytest.raises(McpTokenMissingError):
        await fixture.resolver.resolve(
            presented_token=presented, librechat_user_id=LIBRECHAT_USER, now=NOW
        )


async def test_identity_carries_no_token_material() -> None:
    """V2/V8: no plaintext and no digest field — absent, not redacted."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token()

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    field_names = {field.name for field in fields(McpIdentity)}
    assert "token_hash" not in field_names
    assert "plaintext" not in field_names
    assert plaintext not in str(identity)


async def test_resolved_identity_carries_the_user_facts() -> None:
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token()

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert identity.email == EMAIL
    assert identity.display_name == DISPLAY_NAME
    assert identity.user_id == stored.user_id
    assert identity.librechat_user_id == LIBRECHAT_USER


# --- C5: expiry ---


async def test_expired_token_is_denied() -> None:
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(expires_at=NOW - timedelta(seconds=1))

    with pytest.raises(McpTokenExpiredError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )


async def test_token_is_dead_at_its_expiry_instant() -> None:
    """`<=`, not `<`: `expires_at == now` is expired, not the last valid moment."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(expires_at=NOW)

    with pytest.raises(McpTokenExpiredError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )


async def test_a_non_expiring_token_resolves() -> None:
    """Default mint leaves `expires_at` NULL — live until the row is deleted (V2)."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(expires_at=None)

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert identity.expires_at is None


async def test_an_expired_token_is_not_stamped_or_bound() -> None:
    """The expiry gate runs before every write, so a dead token leaves no trace behind."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(
        expires_at=NOW - timedelta(seconds=1), librechat_user_id=None
    )

    with pytest.raises(McpTokenExpiredError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert fixture.repository.binds == 0
    assert fixture.repository.used_touches == 0
    assert fixture.repository.commits == 0


# --- V1: is_active, re-read every request ---


async def test_inactive_user_is_denied() -> None:
    """V1/V11: the row decides, on every MCP request."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(is_active=False)

    with pytest.raises(McpUserInactiveError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )


async def test_every_resolve_rereads_the_row() -> None:
    """No caching: a disable between two calls takes effect on the second one."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token()

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )
    stored.is_active = False

    with pytest.raises(McpUserInactiveError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )


async def test_a_disabled_operator_cannot_bind_a_token() -> None:
    """The `is_active` gate precedes TOFU, so a disable cannot be raced by a first call."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(is_active=False, librechat_user_id=None)

    with pytest.raises(McpUserInactiveError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert stored.librechat_user_id is None
    assert fixture.repository.binds == 0


# --- C20 / V3: TOFU binding ---


async def test_first_use_binds_the_librechat_user() -> None:
    """C20: minted NULL, pinned by the first call that carries the header."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(librechat_user_id=None)

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert identity.librechat_user_id == LIBRECHAT_USER
    assert stored.librechat_user_id == LIBRECHAT_USER


async def test_a_bound_token_accepts_the_matching_header() -> None:
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(librechat_user_id=LIBRECHAT_USER)

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert identity.librechat_user_id == LIBRECHAT_USER
    # Already bound, so no second write.
    assert fixture.repository.binds == 0


async def test_bound_token_with_a_different_header_is_denied() -> None:
    """V3: the copied-credential case. Code string is fixed by the spec."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with pytest.raises(LibreChatUserMismatchError) as raised:
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=OTHER_LIBRECHAT_USER, now=NOW
        )

    assert raised.value.error_code == "librechat_user_mismatch"
    # The binding is unchanged: a mismatch must not re-pin the token to the caller.
    assert stored.librechat_user_id == LIBRECHAT_USER


async def test_absent_header_is_denied_when_unbound() -> None:
    """C24: LibreChat is the sole client, so a headerless call is unsupported, not new."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(librechat_user_id=None)

    with pytest.raises(LibreChatUserHeaderMissingError) as raised:
        await fixture.resolver.resolve(presented_token=plaintext, librechat_user_id=None, now=NOW)

    assert raised.value.error_code == "librechat_user_header_missing"
    assert stored.librechat_user_id is None


async def test_absent_header_is_denied_when_bound() -> None:
    """V3: absent counts against a bound token too — there is no unbound-client path."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with pytest.raises(LibreChatUserHeaderMissingError):
        await fixture.resolver.resolve(presented_token=plaintext, librechat_user_id=None, now=NOW)


@pytest.mark.parametrize("header", ["", "   "])
async def test_a_blank_header_counts_as_absent(header: str) -> None:
    """A client that failed to interpolate `{{LIBRECHAT_USER_ID}}` must not pin a token."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(librechat_user_id=None)

    with pytest.raises(LibreChatUserHeaderMissingError):
        await fixture.resolver.resolve(presented_token=plaintext, librechat_user_id=header, now=NOW)

    assert stored.librechat_user_id is None


async def test_the_header_is_stripped_before_comparison() -> None:
    """Whitespace from a header value must not read as a different LibreChat account."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(librechat_user_id=LIBRECHAT_USER)

    identity = await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=f"  {LIBRECHAT_USER}  ", now=NOW
    )

    assert identity.librechat_user_id == LIBRECHAT_USER


async def test_losing_the_bind_race_is_a_mismatch() -> None:
    """C20: two first calls, one winner. The loser is refused, not silently rebound."""
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(librechat_user_id=None)
    fixture.repository.bind_race_winner = OTHER_LIBRECHAT_USER

    with pytest.raises(LibreChatUserMismatchError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert stored.librechat_user_id == OTHER_LIBRECHAT_USER


async def test_a_denied_binding_is_never_committed() -> None:
    """The commit is the last statement, after every gate — a refusal writes nothing."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(librechat_user_id=None)
    fixture.repository.bind_race_winner = OTHER_LIBRECHAT_USER

    with pytest.raises(LibreChatUserMismatchError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert fixture.repository.commits == 0


# --- V4: LDAP revalidation on a staleness interval ---


async def test_fresh_token_skips_the_directory() -> None:
    """A recent check means no LDAP round trip — the common path costs one query."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(last_ldap_check_at=NOW)

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.call_count == 0
    assert fixture.repository.ldap_touches == 0


async def test_stale_token_revalidates_against_the_directory() -> None:
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token(last_ldap_check_at=stale_check())

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.checked_emails == [EMAIL]
    assert stored.last_ldap_check_at == NOW


async def test_a_never_checked_token_revalidates() -> None:
    """NULL is stale: a token minted while LDAP was down must not skip its first check."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(last_ldap_check_at=None)

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.call_count == 1


async def test_a_zero_interval_revalidates_every_request() -> None:
    """`MCP_TOKEN_LDAP_REVALIDATE_SECONDS=0` is a real setting, not a disable switch."""
    fixture = build_resolver(ldap_revalidate_seconds=0)
    plaintext, _ = fixture.repository.add_token(last_ldap_check_at=NOW)

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.call_count == 1


async def test_the_staleness_boundary_is_inclusive() -> None:
    """Exactly `REVALIDATE_SECONDS` old counts as stale, so `0` can mean every request."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(last_ldap_check_at=stale_check())

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.call_count == 1


async def test_one_second_inside_the_interval_does_not_revalidate() -> None:
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(
        last_ldap_check_at=stale_check(seconds=REVALIDATE_SECONDS - 1)
    )

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert fixture.directory.call_count == 0


# --- V4: the two directory failures, which must not be conflated ---


async def test_user_absent_from_directory_revokes_every_token() -> None:
    """V4 cascade revoke: employment ended, so every credential goes, not just this one."""
    fixture = build_resolver(present=False)
    plaintext, stored = fixture.repository.add_token(last_ldap_check_at=None)
    # A second token for the same operator, and one for a colleague who must survive.
    fixture.repository.add_token(user_id=stored.user_id, last_ldap_check_at=None)
    _, colleague = fixture.repository.add_token(email="colleague@example.com")

    with pytest.raises(McpUserNotInDirectoryError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert list(fixture.repository.tokens) == [colleague.token_id]


async def test_the_cascade_revoke_is_committed_before_the_refusal() -> None:
    """A revoke that rolls back with the error path is a revoke that did not happen."""
    fixture = build_resolver(present=False)
    plaintext, _ = fixture.repository.add_token(last_ldap_check_at=None)

    with pytest.raises(McpUserNotInDirectoryError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert fixture.repository.commits == 1


async def test_directory_outage_denies_without_revoking() -> None:
    """V4 fail closed: unreachable ≠ gone. Swallowing this would mass-revoke on a blip."""
    fixture = build_resolver(unavailable=True)
    plaintext, stored = fixture.repository.add_token(last_ldap_check_at=None)

    with pytest.raises(LdapUnavailableError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert list(fixture.repository.tokens) == [stored.token_id]
    assert fixture.repository.commits == 0


async def test_directory_outage_leaves_the_binding_untouched() -> None:
    """Revalidation runs before the TOFU write, so a refused call cannot pin a token."""
    fixture = build_resolver(unavailable=True)
    plaintext, stored = fixture.repository.add_token(
        librechat_user_id=None, last_ldap_check_at=None
    )

    with pytest.raises(LdapUnavailableError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
        )

    assert stored.librechat_user_id is None
    assert fixture.repository.binds == 0


async def test_a_mismatch_is_refused_before_the_directory_is_called() -> None:
    """The free check runs first: a copied token must not cost an LDAP round trip."""
    fixture = build_resolver()
    plaintext, _ = fixture.repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
    )

    with pytest.raises(LibreChatUserMismatchError):
        await fixture.resolver.resolve(
            presented_token=plaintext, librechat_user_id=OTHER_LIBRECHAT_USER, now=NOW
        )

    assert fixture.directory.call_count == 0


# --- Usage stamping ---


async def test_a_successful_resolve_stamps_last_used_and_commits_once() -> None:
    fixture = build_resolver()
    plaintext, stored = fixture.repository.add_token()

    await fixture.resolver.resolve(
        presented_token=plaintext, librechat_user_id=LIBRECHAT_USER, now=NOW
    )

    assert stored.last_used_at == NOW
    assert fixture.repository.commits == 1
