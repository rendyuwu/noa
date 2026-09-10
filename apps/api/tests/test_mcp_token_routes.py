"""`/admin/users/{id}/tokens` and `/me/mcp-tokens` over HTTP (T53 — V2, V6, V8, V13, V14, V73,
V100).

`test_mcp_token_service.py` owns the policy: the label rule, the 404 shapes, what the audit
event carries. This file owns what only a request can prove — that the three admin routes are
behind `require_admin` while the three `/me` routes are behind `require_session_user`, that the
plaintext crosses the wire exactly once and appears in no later read, that a colleague's token
id is indistinguishable from a fabricated one, and that a successful write ends its transaction
while a refused one does not.

`support.admin.admin_harness` runs the real routers, the real gates, the real `McpTokenService`
and the shared error handler; only SQL and LDAP are faked. So a 403 here is the shipped 403.
The live `commit()` — the half a double cannot witness (B10, V100c) — is asserted against
Postgres in `test_mcp_token_repository.py::test_commit_makes_a_mint_outlive_the_request`.

**The plaintext is the thing under test.** Most assertions below are about where it is *not*:
not in a list, not in a stored row, not in an error body, not in a second read of the same
token. V2 gives it exactly one appearance, and a surface that leaked a second one would look
correct in every other respect.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import status

from core.audit.admin_events import EVENT_MCP_TOKEN_MINTED, EVENT_MCP_TOKEN_REVOKED
from core.auth.mcp_token_service import MAX_LABEL_LENGTH, TOKEN_MARKER, hash_mcp_token
from noa_api.api.request_context import REQUEST_ID_HEADER
from support.admin import (
    ADMIN_EMAIL,
    ME_TOKENS_PATH,
    OPERATOR_EMAIL,
    AdminHarness,
    admin_harness,
    admin_tokens_path,
)
from support.mcp_tokens import LABEL, OTHER_LABEL
from support.rbac import ROLE_SUPPORT

# The three admin routes, as (method, path template, body). Parametrized rather than repeated so
# a fourth route added without its own gate test fails these.
ADMIN_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/admin/users/{user}/tokens", None),
    ("POST", "/admin/users/{user}/tokens", {"label": LABEL}),
    ("DELETE", "/admin/users/{user}/tokens/{token}", None),
)

# The three self-service routes, same shape. The caller's own id never appears in a path here —
# that is the property, not an omission.
ME_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", ME_TOKENS_PATH, None),
    ("POST", ME_TOKENS_PATH, {"label": LABEL}),
    ("DELETE", ME_TOKENS_PATH + "/{token}", None),
)


def _call(
    harness: AdminHarness,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    *,
    user: UUID,
    token: UUID,
):
    """Issue one of the parametrized routes against `user` / `token`."""
    return harness.client.request(method, path.format(user=user, token=token), json=body)


def _plaintexts_in(text: str) -> bool:
    """Whether a response body contains anything shaped like a token plaintext.

    The marker is public and the prefix is meant to be published, so this looks for the *full*
    length: `noa_` plus 43 urlsafe-base64 characters.
    """
    return re.search(rf"{TOKEN_MARKER}[A-Za-z0-9_-]{{43}}", text) is not None


def _without_request_id(body: dict[str, Any]) -> dict[str, Any]:
    """The error body minus the one field that must differ per request."""
    return {key: value for key, value in body.items() if key != "request_id"}


# --- V13 + V6: the gates ---


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_an_admin_reaches_every_admin_token_route(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """The positive control. Without it every refusal below passes against a 404."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        minted = harness.mint_token(target.id)

        response = _call(
            harness, method, path, body, user=target.id, token=UUID(str(minted["token"]["id"]))
        )

    assert response.status_code == status.HTTP_200_OK, response.text


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_every_admin_token_route_refuses_a_non_admin(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """V13: authenticated, holds a role, still refused — on all three.

    The ids are random on purpose: the gate must decide before anything is looked up, so a
    non-admin cannot use the 403/404 split to learn which users hold credentials.
    """
    with admin_harness() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        response = _call(harness, method, path, body, user=uuid4(), token=uuid4())

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "admin_access_required"


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES + ME_ROUTES)
def test_every_token_route_refuses_a_missing_cookie(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    """401, not 403: no session at all, so the browser should sign in rather than give up.

    Both surfaces, one assertion: the `/me` routes have a different gate but the same answer to
    "no credential presented", and a route that fell through to an anonymous read would mint or
    reveal a credential for nobody in particular.
    """
    with admin_harness() as harness:
        response = _call(harness, method, path, body, user=uuid4(), token=uuid4())

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error_code"] == "session_invalid"


def test_a_disabled_admin_loses_the_token_routes_on_the_next_request() -> None:
    """V6: the row re-read runs before the role check, and the cookie is still valid.

    The session JWT has no revocation path before `exp`, so this re-read is the only thing that
    bounds a disabled admin's live session — and a route that mints MCP credentials is the
    surface where an extra hour of a revoked admin's session costs the most.
    """
    with admin_harness() as harness:
        admin = harness.sign_in()
        target = harness.add_target()
        assert harness.client.get(admin_tokens_path(target.id)).status_code == status.HTTP_200_OK

        admin.session.is_active = False  # what another admin's disable does to the row

        response = harness.client.post(admin_tokens_path(target.id), json={"label": LABEL})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error_code"] == "user_pending_approval"


def test_a_disabled_operator_cannot_mint_their_own_token() -> None:
    """V6 on the self-service half: `require_session_user` refuses before the service is built.

    Worth its own case rather than folding into the admin one above: the service deliberately
    does *not* refuse an inactive user (an admin may set someone up before activating them), so
    if this route did not inherit the session gate, a disabled operator could mint themselves a
    credential.
    """
    with admin_harness() as harness:
        operator = harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))
        assert harness.client.get(ME_TOKENS_PATH).status_code == status.HTTP_200_OK

        operator.session.is_active = False

        response = harness.client.post(ME_TOKENS_PATH, json={"label": LABEL})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert harness.token_repository.tokens == {}


# --- V2: the plaintext, exactly once ---


def test_mint_returns_the_plaintext_and_no_later_read_does() -> None:
    """V2 show-once: `POST` carries it, and nothing afterwards can recover it."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        minted = harness.mint_token(target.id, label=LABEL)
        plaintext = str(minted["plaintext"])

        listed = harness.client.get(admin_tokens_path(target.id))
        own = harness.client.get(ME_TOKENS_PATH)

    assert plaintext.startswith(TOKEN_MARKER)
    assert plaintext not in listed.text
    assert not _plaintexts_in(listed.text)
    assert not _plaintexts_in(own.text)


def test_a_minted_plaintext_is_stored_only_as_a_digest() -> None:
    """V2 hashed at rest: the row holds the SHA-256 and no fragment of the credential.

    Asserted against everything the repository holds, not only `token_hash` — a prefix column
    that had been sized wrong, or a label defaulted to the plaintext, would pass a narrower
    check.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        minted = harness.mint_token(target.id, label=LABEL)
        plaintext = str(minted["plaintext"])

        assert harness.token_repository.stored_hashes == [hash_mcp_token(plaintext)]
        assert plaintext not in " ".join(harness.token_repository.stored_values)


def test_a_listed_token_carries_the_prefix_and_no_credential_material() -> None:
    """§I.admin-api's read shape, field for field.

    `token_prefix` is published deliberately — it is what lets an admin match a row to the
    credential in a config file — and it is the *only* thing about the secret that is.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        minted = harness.mint_token(target.id, label=LABEL)

        listed = harness.list_tokens(target.id)

    assert len(listed) == 1
    assert set(listed[0]) == {
        "id",
        "user_id",
        "token_prefix",
        "label",
        "librechat_user_id",
        "last_used_at",
        "last_ldap_check_at",
        "expires_at",
        "created_at",
    }
    assert listed[0]["token_prefix"] == str(minted["plaintext"])[: len(TOKEN_MARKER) + 8]
    assert listed[0]["label"] == LABEL
    # C20/V3: NULL at mint is the precondition first-use binding needs.
    assert listed[0]["librechat_user_id"] is None
    assert listed[0]["last_used_at"] is None


def test_the_wire_shapes_are_the_ones_i_admin_api_names() -> None:
    """`{tokens}`, `{token, plaintext}`, `{ok}` — the keys a panel would parse."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        minted = harness.client.post(admin_tokens_path(target.id), json={"label": LABEL})
        token_id = minted.json()["token"]["id"]

        assert set(minted.json()) == {"token", "plaintext"}
        assert set(harness.client.get(admin_tokens_path(target.id)).json()) == {"tokens"}
        assert harness.client.delete(f"{admin_tokens_path(target.id)}/{token_id}").json() == {
            "ok": True
        }


def test_two_mints_produce_two_different_credentials() -> None:
    """V2: a fresh 256-bit draw per mint, so one leaked token says nothing about the next."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        first = harness.mint_token(target.id, label=LABEL)
        second = harness.mint_token(target.id, label=OTHER_LABEL)

    assert first["plaintext"] != second["plaintext"]
    assert first["token"]["id"] != second["token"]["id"]
    assert len(set(harness.token_repository.stored_hashes)) == 2


# --- V2: revoke = delete row ---


def test_revoke_deletes_the_row_and_the_list_loses_it() -> None:
    """V2: revocation is the row's absence — no tombstone, no status column."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        minted = harness.mint_token(target.id, label=LABEL)
        token_id = minted["token"]["id"]

        response = harness.client.delete(f"{admin_tokens_path(target.id)}/{token_id}")

        assert response.status_code == status.HTTP_200_OK
        assert harness.list_tokens(target.id) == []
        assert harness.token_repository.tokens == {}


def test_revoking_another_users_token_answers_the_same_404_as_an_unknown_id() -> None:
    """V2, the V27/V76 existence-⊥-leak principle: one shape for "not yours" and "not there".

    Compared field for field rather than by status alone — a differing `error_code` or `message`
    would be the same oracle wearing a different hat. `request_id` differs per request by
    design, so it is dropped from the comparison and asserted present instead.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        colleague = harness.add_target("colleague@example.com")
        theirs = harness.mint_token(colleague.id, label=LABEL)

        foreign = harness.client.delete(f"{admin_tokens_path(target.id)}/{theirs['token']['id']}")
        unknown = harness.client.delete(f"{admin_tokens_path(target.id)}/{uuid4()}")

        # The colleague's token survived: the id was refused, not deleted from under them.
        assert len(harness.list_tokens(colleague.id)) == 1

    assert foreign.status_code == unknown.status_code == status.HTTP_404_NOT_FOUND
    assert _without_request_id(foreign.json()) == _without_request_id(unknown.json())
    assert foreign.json()["error_code"] == "mcp_token_not_found"
    assert foreign.json()["request_id"]


def test_the_two_error_bodies_would_have_separated_if_they_differed() -> None:
    """The negative control for the comparison above.

    Without it, `_without_request_id(a) == _without_request_id(b)` also passes for a comparison
    that has eaten every distinguishing field. An unknown *user* is a genuinely different
    refusal, and it must show up as one.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        unknown_token = harness.client.delete(f"{admin_tokens_path(target.id)}/{uuid4()}")
        unknown_user = harness.client.get(admin_tokens_path(uuid4()))

    assert _without_request_id(unknown_token.json()) != _without_request_id(unknown_user.json())
    assert unknown_user.json()["error_code"] == "admin_user_not_found"


def test_a_list_for_an_unknown_user_is_404_not_an_empty_list() -> None:
    """ "Holds no tokens" and "no such user" are different answers.

    Collapsing them renders a stale panel link as an empty page instead of a dead one.
    """
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.get(admin_tokens_path(uuid4()))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error_code"] == "admin_user_not_found"


# --- The self-service surface ---


def test_the_me_routes_reach_only_the_callers_own_tokens() -> None:
    """The id comes off the session, never off the request — so there is nothing to substitute.

    Three assertions, because the property has three halves: a colleague's token is not listed,
    a colleague's token id revokes nothing, and the mint lands on the caller's own row.
    """
    with admin_harness() as harness:
        admin = harness.sign_in()
        colleague = harness.add_target("colleague@example.com")
        theirs = harness.mint_token(colleague.id, label=OTHER_LABEL)

        mine = harness.client.post(ME_TOKENS_PATH, json={"label": LABEL})
        listed = harness.client.get(ME_TOKENS_PATH).json()["tokens"]
        stolen = harness.client.delete(f"{ME_TOKENS_PATH}/{theirs['token']['id']}")

    assert mine.json()["token"]["user_id"] == str(admin.id)
    assert [token["id"] for token in listed] == [mine.json()["token"]["id"]]
    assert stolen.status_code == status.HTTP_404_NOT_FOUND
    assert stolen.json()["error_code"] == "mcp_token_not_found"
    assert len(harness.token_repository.tokens) == 2


def test_an_operator_mints_lists_and_revokes_their_own_token() -> None:
    """The whole `/me` cycle for a non-admin: the surface exists for operators, not for admins.

    A `require_admin` accidentally applied to this router would leave every other test in this
    file green — they all sign in as an admin.
    """
    with admin_harness() as harness:
        operator = harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        minted = harness.client.post(ME_TOKENS_PATH, json={"label": LABEL})
        assert minted.status_code == status.HTTP_200_OK, minted.text
        token_id = minted.json()["token"]["id"]

        listed = harness.client.get(ME_TOKENS_PATH)
        revoked = harness.client.delete(f"{ME_TOKENS_PATH}/{token_id}")

    assert minted.json()["token"]["user_id"] == str(operator.id)
    assert [token["id"] for token in listed.json()["tokens"]] == [token_id]
    assert revoked.status_code == status.HTTP_200_OK
    assert harness.token_repository.tokens == {}


def test_a_mint_with_no_body_field_is_an_unlabelled_token() -> None:
    """`label` is optional because the column is nullable — an unnamed token is legitimate."""
    with admin_harness() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        response = harness.client.post(ME_TOKENS_PATH, json={})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["token"]["label"] is None


def test_an_over_long_label_is_400_and_mints_nothing() -> None:
    """400 `invalid_token_label` rather than a database error surfacing as a 500."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        response = harness.client.post(
            admin_tokens_path(target.id), json={"label": "x" * (MAX_LABEL_LENGTH + 1)}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error_code"] == "invalid_token_label"
        assert harness.token_repository.tokens == {}


# --- V14: one audit event per change, from the route ---


def test_mint_and_revoke_each_record_one_audit_event() -> None:
    """The actor is the signed-in admin, resolved from the cookie and never from the body.

    Metadata carries ids, prefix and label so a reader can match an event to the row they see;
    it carries no digest and no plaintext.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        minted = harness.mint_token(target.id, label=LABEL)
        harness.client.delete(f"{admin_tokens_path(target.id)}/{minted['token']['id']}")
        events = harness.audit.events

    assert [event.event_type for event in events] == [
        EVENT_MCP_TOKEN_MINTED,
        EVENT_MCP_TOKEN_REVOKED,
    ]
    assert {event.actor_email for event in events} == {ADMIN_EMAIL}
    assert [event.target for event in events] == [str(minted["token"]["id"])] * 2
    assert events[0].metadata["target_user_id"] == str(target.id)
    assert events[0].metadata["token_prefix"] == str(minted["plaintext"])[: len(TOKEN_MARKER) + 8]
    rendered = repr([event.metadata for event in events])
    assert str(minted["plaintext"]) not in rendered
    assert hash_mcp_token(str(minted["plaintext"])) not in rendered


def test_a_self_service_mint_names_the_operator_as_the_actor() -> None:
    """V14: "who issued this credential" is the question the trail answers, and on `/me` the
    answer is the operator themselves rather than an admin acting for them."""
    with admin_harness() as harness:
        harness.sign_in(OPERATOR_EMAIL, roles=(ROLE_SUPPORT,))

        harness.client.post(ME_TOKENS_PATH, json={"label": LABEL})

    assert [event.actor_email for event in harness.audit.events] == [OPERATOR_EMAIL]


def test_the_token_read_routes_record_nothing() -> None:
    """V14 is about changes. A trail of list calls is V45's job, on another surface."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        harness.list_tokens(target.id)
        harness.client.get(ME_TOKENS_PATH)

        assert harness.audit.events == []


# --- V100: the transaction boundary, from the route ---


def test_a_mint_and_a_revoke_each_commit_once() -> None:
    """V100: `get_db_session` never commits, so a write that does not end its own transaction
    answers 200 over a rollback — here, handing back a credential for a row that
    disappears at teardown. One commit per mutation, not one per statement."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        minted = harness.mint_token(target.id, label=LABEL)
        assert harness.token_repository.commits == 1
        assert harness.token_repository.committed == harness.token_repository.tokens

        harness.client.delete(f"{admin_tokens_path(target.id)}/{minted['token']['id']}")

        assert harness.token_repository.commits == 2
        assert harness.token_repository.committed == {}


def test_a_refused_mint_and_a_refused_revoke_commit_nothing() -> None:
    """V100(a): every guard raises before the commit, so a refused write persists nothing.

    The committed snapshot is the assertion surface, not the mutable dict: a future guard
    ordered *after* the write would pass a "no rows" check on the dict alone and fail this one.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        unknown_user = harness.client.post(admin_tokens_path(uuid4()), json={"label": LABEL})
        unknown_token = harness.client.delete(f"{admin_tokens_path(target.id)}/{uuid4()}")

        assert unknown_user.status_code == status.HTTP_404_NOT_FOUND
        assert unknown_token.status_code == status.HTTP_404_NOT_FOUND
        assert harness.token_repository.commits == 0
        assert harness.token_repository.committed == {}
        assert harness.token_repository.tokens == {}


# --- V8 + V73: the error envelope, from a real route ---


def test_a_refusal_carries_request_id_and_no_credential_material() -> None:
    """V8: the body is `error_code` + `message` + `request_id`, nothing else.

    The id that was refused stays in `detail`, which is a log field — so a 404 cannot become a
    way to have NOA echo an attacker's own guess back at them.
    """
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()
        ghost = uuid4()

        response = harness.client.delete(f"{admin_tokens_path(target.id)}/{ghost}")

    body = response.json()
    assert set(body) == {"error_code", "message", "request_id"}
    assert str(ghost) not in response.text
    assert not _plaintexts_in(response.text)


def test_an_error_body_and_header_share_one_request_id() -> None:
    """V73: same value in the body and in `x-request-id`, so an operator can quote either."""
    with admin_harness() as harness:
        harness.sign_in()

        response = harness.client.get(admin_tokens_path(uuid4()))

    assert response.headers[REQUEST_ID_HEADER] == response.json()["request_id"]


def test_the_user_routes_still_answer_beside_the_token_routes() -> None:
    """Every router on one app, one actor, one audit sink — the wiring the V14 assertions
    above depend on."""
    with admin_harness() as harness:
        harness.sign_in()
        target = harness.add_target()

        assert harness.client.get("/admin/users").status_code == status.HTTP_200_OK
        assert harness.client.get(admin_tokens_path(target.id)).status_code == status.HTTP_200_OK
        assert harness.client.get(ME_TOKENS_PATH).status_code == status.HTTP_200_OK
