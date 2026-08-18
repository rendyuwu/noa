"""The fifteen admin server routes, over the real services (T54, I.admin-api).

`support/admin.py`'s harness mounts all three routers with `require_admin`,
`require_session_user`, the real `JWTService`, the real CRUD services, the real `SecretCipher`
and the shared error handler — only SQL and the network are doubled. So a 403 here is the
shipped 403, a 422 is the shipped 422, and encrypt-on-write really runs.

What this file owns, and what it deliberately does not:

- **owns** the HTTP surface: the admin gate on every route (V13), the absence of every secret
  from every response (V2, V8), the refusals V21's field rules produce, the shared error
  envelope (V73), and the audit event per mutation (V14).
- **does not own** the trust-on-first-use rule. `POST …/validate` here goes through a stub
  (`support.server_admin.RecordingValidationService`), because when a pin is written is a
  property of a real key exchange — `test_server_host_key_validation.py` asserts it against a
  real `asyncssh` server, and `test_server_admin_service.py` asserts the branch structure and
  the validate audit event over the real services.
- **does not own** the commit boundary. Only a second database session can see the difference
  between flushed and committed (`test_server_admin_repository.py`, V100(c)).

The secret literals below are the point of several tests: they are sent in request bodies and
then hunted in every response byte. A row built with no secrets could not fail a "no secrets
leaked" assertion, so every fixture carries all of them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from core.db.models import ADMIN_ROLE_NAME
from core.secrets.crypto import ENCRYPTED_PREFIX
from core.servers.validation import ServerValidationResult
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, status_for
from noa_api.api.request_context import REQUEST_ID_HEADER
from support.admin import (
    PMG_SERVERS_PATH,
    PROXMOX_SERVERS_PATH,
    WHM_SERVERS_PATH,
    AdminHarness,
    admin_harness,
)
from support.servers import pmg_server, proxmox_server, whm_server

# Every credential a request body may carry, so one assertion can hunt all of them.
API_TOKEN = "whm-api-token-plaintext"
PROXMOX_TOKEN_SECRET = "proxmox-token-secret-plaintext"
SSH_PASSWORD = "operator-ssh-password-plaintext"
SSH_PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nplaintext-key\n"
SSH_PASSPHRASE = "key-passphrase-plaintext"

SUBMITTED_SECRETS = (
    API_TOKEN,
    PROXMOX_TOKEN_SECRET,
    SSH_PASSWORD,
    SSH_PRIVATE_KEY,
    SSH_PASSPHRASE,
)

FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAIExampleHostKeyDigest"


def whm_create_body(name: str = "web16", **overrides: Any) -> dict[str, Any]:
    """A WHM create payload the ported form would send, credentials included."""
    body: dict[str, Any] = {
        "name": name,
        "base_url": f"https://{name}.example.net:2087",
        "api_username": "root",
        "api_token": API_TOKEN,
        "verify_ssl": True,
        "ssh_username": "noa",
        "ssh_port": 22,
        "ssh_password": SSH_PASSWORD,
        "ssh_private_key": SSH_PRIVATE_KEY,
        "ssh_private_key_passphrase": SSH_PASSPHRASE,
    }
    body.update(overrides)
    return body


def proxmox_create_body(name: str = "pve1", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "base_url": f"https://{name}.example.net:8006",
        "api_token_id": "root@pam!noa",
        "api_token_secret": PROXMOX_TOKEN_SECRET,
        "verify_ssl": False,
    }
    body.update(overrides)
    return body


def pmg_create_body(name: str = "mail1", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "ssh_host": f"{name}.example.net",
        "ssh_username": "noa",
        "ssh_port": 22,
        "ssh_password": SSH_PASSWORD,
        "ssh_private_key": SSH_PRIVATE_KEY,
        "ssh_private_key_passphrase": SSH_PASSPHRASE,
        "ssh_host_key_fingerprint": FINGERPRINT,
    }
    body.update(overrides)
    return body


@pytest.fixture
def harness() -> Iterator[AdminHarness]:
    """A signed-in admin and one existing row per vertical."""
    with admin_harness(
        whm_rows=[whm_server("existing-whm")],
        proxmox_rows=[proxmox_server("existing-pve")],
        pmg_rows=[pmg_server("existing-mail")],
    ) as built:
        built.sign_in()
        yield built


# Every route, as (method, path-template), so the admin-gate walk covers the whole surface
# rather than a sample. `{id}` is filled per test.
ROUTE_TABLE: tuple[tuple[str, str], ...] = tuple(
    (method, f"{prefix}{suffix}")
    for prefix in (WHM_SERVERS_PATH, PROXMOX_SERVERS_PATH, PMG_SERVERS_PATH)
    for method, suffix in (
        ("GET", ""),
        ("POST", ""),
        ("PATCH", "/{id}"),
        ("DELETE", "/{id}"),
        ("POST", "/{id}/validate"),
    )
)


# --- V13: every route is admin-only ---


def test_every_server_route_is_admin_only(harness: AdminHarness) -> None:
    """V13: fifteen routes, fifteen 403s for an operator holding no `admin` role.

    Walked from `ROUTE_TABLE` rather than listed, so a sixteenth route added later without
    `AdminUserDep` fails here instead of shipping open. The count is asserted too — a table
    that silently shrank would make this pass by covering less.
    """
    assert len(ROUTE_TABLE) == 15

    harness.sign_in("nonadmin@example.com", roles=())
    server_id = uuid4()

    for method, template in ROUTE_TABLE:
        path = template.format(id=server_id)
        response = harness.client.request(method, path, json={})
        assert response.status_code == 403, f"{method} {path} → {response.status_code}"
        assert response.json()["error_code"] == "admin_access_required"


def test_a_demoted_admin_loses_the_routes_on_the_next_request() -> None:
    """V6 through V13: the role is read from the row per request, never from the cookie.

    The same claim `test_admin_user_routes.py` makes about its own surface, asserted again here
    because `require_admin` is a per-handler parameter on these fifteen: a router that acquired
    a `dependencies=[...]` gate instead would still pass that test and could stop re-reading.
    """
    with admin_harness(whm_rows=[whm_server("existing-whm")]) as harness:
        admin = harness.sign_in()
        assert harness.client.get(WHM_SERVERS_PATH).status_code == 200

        harness.auth_repository.user_roles[admin.id].discard(ADMIN_ROLE_NAME)

        response = harness.client.get(WHM_SERVERS_PATH)

    assert response.status_code == 403
    assert response.json()["error_code"] == "admin_access_required"


# --- V2, V8: no response carries a secret ---


def _all_response_bytes(harness: AdminHarness) -> str:
    """Every byte the fifteen routes answer with, for one leak assertion.

    Each vertical is driven through create → list → patch → validate → delete, so the hunt
    covers the shapes a secret could hide in: the created row, the list entry, the patched row,
    the validate envelope and the delete answer.
    """
    chunks: list[str] = []

    created = harness.client.post(WHM_SERVERS_PATH, json=whm_create_body("leak-whm"))
    assert created.status_code == 201, created.text
    whm_id = created.json()["server"]["id"]
    chunks.append(created.text)
    chunks.append(harness.client.get(WHM_SERVERS_PATH).text)
    chunks.append(
        harness.client.patch(
            f"{WHM_SERVERS_PATH}/{whm_id}",
            json={"api_token": API_TOKEN, "ssh_password": SSH_PASSWORD},
        ).text
    )
    chunks.append(harness.client.post(f"{WHM_SERVERS_PATH}/{whm_id}/validate").text)
    chunks.append(harness.client.delete(f"{WHM_SERVERS_PATH}/{whm_id}").text)

    created = harness.client.post(PROXMOX_SERVERS_PATH, json=proxmox_create_body("leak-pve"))
    assert created.status_code == 201, created.text
    proxmox_id = created.json()["server"]["id"]
    chunks.append(created.text)
    chunks.append(harness.client.get(PROXMOX_SERVERS_PATH).text)
    chunks.append(
        harness.client.patch(
            f"{PROXMOX_SERVERS_PATH}/{proxmox_id}",
            json={"api_token_secret": PROXMOX_TOKEN_SECRET},
        ).text
    )
    chunks.append(harness.client.post(f"{PROXMOX_SERVERS_PATH}/{proxmox_id}/validate").text)
    chunks.append(harness.client.delete(f"{PROXMOX_SERVERS_PATH}/{proxmox_id}").text)

    created = harness.client.post(PMG_SERVERS_PATH, json=pmg_create_body("leak-mail"))
    assert created.status_code == 201, created.text
    pmg_id = created.json()["server"]["id"]
    chunks.append(created.text)
    chunks.append(harness.client.get(PMG_SERVERS_PATH).text)
    chunks.append(
        harness.client.patch(
            f"{PMG_SERVERS_PATH}/{pmg_id}",
            json={"ssh_private_key": SSH_PRIVATE_KEY},
        ).text
    )
    chunks.append(harness.client.post(f"{PMG_SERVERS_PATH}/{pmg_id}/validate").text)
    chunks.append(harness.client.delete(f"{PMG_SERVERS_PATH}/{pmg_id}").text)

    return "\n".join(chunks)


def test_no_response_on_any_of_the_fifteen_routes_carries_a_secret_literal(
    harness: AdminHarness,
) -> None:
    """V2, V8: what goes in a request body never comes back out.

    Asserted on the serialized bytes, not on a key set: a field dropped from the model but
    reachable through a nested dict would pass a key comparison and fail here (V87's shape).
    """
    body = _all_response_bytes(harness)

    for secret in SUBMITTED_SECRETS:
        assert secret not in body, f"{secret!r} reached a response"
    # And the ciphertext form is absent too — a response echoing `enc:v1:fernet:…` would be a
    # leak of the stored value rather than of the plaintext, and just as wrong.
    assert ENCRYPTED_PREFIX not in body


def test_a_created_row_reports_credentials_as_presence_booleans(harness: AdminHarness) -> None:
    """V2: the panel needs "is something stored", which is all it gets."""
    server = harness.create_server(WHM_SERVERS_PATH, whm_create_body("booleans"))

    assert server["has_api_token"] is True
    assert server["has_ssh_password"] is True
    assert server["has_ssh_private_key"] is True
    assert "api_token" not in server
    assert "ssh_password" not in server
    assert "ssh_private_key" not in server
    assert "ssh_private_key_passphrase" not in server


def test_secrets_reach_the_row_encrypted(harness: AdminHarness) -> None:
    """C7, V48: the service encrypts before the repository sees the value.

    Over the fake repository, so this is about *what the service passed down*, not about SQL —
    `test_server_admin_repository.py` asserts the stored column against Postgres.
    """
    harness.create_server(WHM_SERVERS_PATH, whm_create_body("encrypted"))
    row = next(row for row in harness.whm_servers.servers if row.name == "encrypted")

    assert row.api_token.startswith(ENCRYPTED_PREFIX)
    assert row.ssh_password is not None and row.ssh_password.startswith(ENCRYPTED_PREFIX)
    assert row.ssh_private_key is not None and row.ssh_private_key.startswith(ENCRYPTED_PREFIX)
    assert row.ssh_private_key_passphrase is not None
    assert row.ssh_private_key_passphrase.startswith(ENCRYPTED_PREFIX)
    # The fingerprint is a public digest and is stored as typed — see
    # `core.servers.admin_service.encrypt_ssh_credentials`.
    harness.create_server(PMG_SERVERS_PATH, pmg_create_body("pinned"))
    pinned = next(row for row in harness.pmg_servers.servers if row.name == "pinned")
    assert pinned.ssh_host_key_fingerprint == FINGERPRINT


# --- V21: the field rules ---


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (WHM_SERVERS_PATH, whm_create_body(name="   ")),
        (WHM_SERVERS_PATH, whm_create_body(name="web 16")),
        (WHM_SERVERS_PATH, whm_create_body(base_url="http://web16.example.net:2087")),
        (WHM_SERVERS_PATH, whm_create_body(base_url="https://web16.example.net/whm")),
        (WHM_SERVERS_PATH, whm_create_body(base_url="https://u:p@web16.example.net")),
        (WHM_SERVERS_PATH, whm_create_body(api_username="root!")),
        (WHM_SERVERS_PATH, whm_create_body(api_token="   ")),
        (WHM_SERVERS_PATH, whm_create_body(ssh_port=0)),
        (WHM_SERVERS_PATH, whm_create_body(ssh_port=70000)),
        (PROXMOX_SERVERS_PATH, proxmox_create_body(name="")),
        (PROXMOX_SERVERS_PATH, proxmox_create_body(base_url="pve1.example.net")),
        (PROXMOX_SERVERS_PATH, proxmox_create_body(api_token_secret=" ")),
        (PMG_SERVERS_PATH, pmg_create_body(name="\t")),
        (PMG_SERVERS_PATH, pmg_create_body(ssh_host="https://mail1.example.net")),
        (PMG_SERVERS_PATH, pmg_create_body(ssh_host="mail1..example.net")),
        (PMG_SERVERS_PATH, pmg_create_body(ssh_host="mail 1")),
        (PMG_SERVERS_PATH, pmg_create_body(ssh_host="")),
    ],
)
def test_malformed_identity_fields_are_refused_before_any_write(
    harness: AdminHarness, path: str, body: dict[str, Any]
) -> None:
    """V21: whitespace-only and malformed identity fields are 422, and nothing is stored.

    The "nothing stored" half matters as much as the status: a validator that ran after the
    insert would answer 422 over a row that exists.
    """
    before = {
        WHM_SERVERS_PATH: len(harness.whm_servers.servers),
        PROXMOX_SERVERS_PATH: len(harness.proxmox_servers.servers),
        PMG_SERVERS_PATH: len(harness.pmg_servers.servers),
    }

    response = harness.client.post(path, json=body)

    assert response.status_code == 422, response.text
    assert response.json()["error_code"] == "request_validation_error"
    after = {
        WHM_SERVERS_PATH: len(harness.whm_servers.servers),
        PROXMOX_SERVERS_PATH: len(harness.proxmox_servers.servers),
        PMG_SERVERS_PATH: len(harness.pmg_servers.servers),
    }
    assert after == before


def test_a_422_body_never_echoes_the_submitted_value(harness: AdminHarness) -> None:
    """V8: pydantic's `input` carries the failing value, and these bodies carry credentials."""
    response = harness.client.post(
        WHM_SERVERS_PATH, json=whm_create_body(api_username="root!", api_token=API_TOKEN)
    )

    assert response.status_code == 422
    assert API_TOKEN not in response.text
    assert set(response.json()) == {"error_code", "message", "request_id"}


def test_a_base_url_is_normalized_before_it_is_stored(harness: AdminHarness) -> None:
    """The stored value is `https://host[:port]`, so the SSH host derived from it is one thing."""
    server = harness.create_server(
        WHM_SERVERS_PATH, whm_create_body("normalized", base_url="https://Web16.Example.NET:2087/")
    )

    assert server["base_url"] == "https://web16.example.net:2087"


# --- Refusals: 404, 409, and the shared envelope ---


def test_server_not_found_carries_the_shared_envelope(harness: AdminHarness) -> None:
    """V73: `request_id` in the body and the same value in `x-request-id`, on every vertical."""
    absent = uuid4()

    for path, code in (
        (WHM_SERVERS_PATH, "whm_server_not_found"),
        (PROXMOX_SERVERS_PATH, "proxmox_server_not_found"),
        (PMG_SERVERS_PATH, "pmg_server_not_found"),
    ):
        response = harness.client.delete(f"{path}/{absent}")

        assert response.status_code == 404, response.text
        body = response.json()
        assert body["error_code"] == code
        assert set(body) == {"error_code", "message", "request_id"}
        assert response.headers[REQUEST_ID_HEADER] == body["request_id"]


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        (WHM_SERVERS_PATH, whm_create_body("existing-whm"), "whm_server_name_exists"),
        (PROXMOX_SERVERS_PATH, proxmox_create_body("existing-pve"), "proxmox_server_name_exists"),
        (PMG_SERVERS_PATH, pmg_create_body("existing-mail"), "pmg_server_name_exists"),
    ],
)
def test_a_duplicate_name_is_409_and_stores_nothing(
    harness: AdminHarness, path: str, body: dict[str, Any], code: str
) -> None:
    """409 rather than 422: the name is valid, the table refuses it (`core.servers.errors`)."""
    response = harness.client.post(path, json=body)

    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == code


def test_a_name_differing_only_by_case_is_refused(harness: AdminHarness) -> None:
    """`core.servers.reference`'s warning, closed on the write side (V18).

    Postgres uniqueness is case-sensitive, so `EXISTING-WHM` would insert beside
    `existing-whm` and every reference to either would resolve ambiguously forever.
    """
    response = harness.client.post(WHM_SERVERS_PATH, json=whm_create_body("EXISTING-WHM"))

    assert response.status_code == 409
    assert response.json()["error_code"] == "whm_server_name_exists"


def test_a_rename_may_keep_its_own_name(harness: AdminHarness) -> None:
    """The name check excludes the row being edited, or no save without a rename would pass."""
    existing = harness.whm_servers.servers[0]

    response = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}",
        json={"name": existing.name, "api_username": "reseller"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["server"]["api_username"] == "reseller"


def test_every_server_inventory_error_is_mapped_explicitly() -> None:
    """V73: no inventory refusal may reach the 503 *fallback*, which means "unclassified"."""
    from core.servers.errors import ServerInventoryError

    def subclasses(klass: type[ServerInventoryError]) -> set[type[ServerInventoryError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(ServerInventoryError):
        assert klass in set(STATUS_BY_ERROR), f"{klass.__name__} has no explicit status"
        assert status_for(klass.__new__(klass)) != FALLBACK_STATUS


# --- The partial-update contract ---


def test_an_absent_field_leaves_the_stored_value_alone(harness: AdminHarness) -> None:
    """`None` means "leave alone" — the whole reason the panel's secrets are write-only."""
    existing = harness.whm_servers.servers[0]
    stored_token = existing.api_token

    response = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"name": "renamed-whm"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["server"]["name"] == "renamed-whm"
    assert existing.api_token == stored_token
    assert existing.ssh_password is not None


def test_clear_ssh_configuration_empties_the_whole_block_including_the_pin(
    harness: AdminHarness,
) -> None:
    """What the WHM form sends when an admin turns SSH off."""
    existing = harness.whm_servers.servers[0]

    response = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"clear_ssh_configuration": True}
    )

    assert response.status_code == 200, response.text
    server = response.json()["server"]
    assert server["ssh_username"] is None
    assert server["ssh_port"] is None
    assert server["ssh_host_key_fingerprint"] is None
    assert server["has_ssh_password"] is False
    assert server["has_ssh_private_key"] is False


def test_moving_the_host_drops_the_stored_pin(harness: AdminHarness) -> None:
    """V82: a pin belongs to one `(host, port)` pair, so moving either invalidates it.

    Both directions in one test because the pair is one fact: `base_url` for WHM, and the port
    on its own.
    """
    existing = harness.whm_servers.servers[0]
    assert existing.ssh_host_key_fingerprint is not None

    moved = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"base_url": "https://moved.example.net:2087"}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["server"]["ssh_host_key_fingerprint"] is None

    # Negative control: a PATCH that touches neither keeps the pin, or "the pin was dropped"
    # would pass against a route that drops it on every save.
    existing.ssh_host_key_fingerprint = FINGERPRINT
    untouched = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"api_username": "reseller"}
    )
    assert untouched.status_code == 200, untouched.text
    assert untouched.json()["server"]["ssh_host_key_fingerprint"] == FINGERPRINT

    ported = harness.client.patch(f"{WHM_SERVERS_PATH}/{existing.id}", json={"ssh_port": 2222})
    assert ported.status_code == 200, ported.text
    assert ported.json()["server"]["ssh_host_key_fingerprint"] is None


def test_a_pmg_patch_may_pin_a_new_key_while_moving_the_host(harness: AdminHarness) -> None:
    """An explicit fingerprint in the same patch wins over the invalidation rule.

    PMG's form offers the field, so an operator who moved a node and pasted its new key in one
    save meant both — and a rule that cleared it anyway would silently discard the value they
    typed.
    """
    existing = harness.pmg_servers.servers[0]
    other = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAIAnotherHostKeyDigest"

    response = harness.client.patch(
        f"{PMG_SERVERS_PATH}/{existing.id}",
        json={"ssh_host": "moved.example.net", "ssh_host_key_fingerprint": other},
    )

    assert response.status_code == 200, response.text
    assert response.json()["server"]["ssh_host_key_fingerprint"] == other


def test_a_whm_patch_cannot_set_a_fingerprint_at_all(harness: AdminHarness) -> None:
    """The WHM surface has no fingerprint input: the pin arrives from a validate capture.

    The field is not on the model, so pydantic ignores it — asserted, because "the panel does
    not send it" is not the same claim as "the API will not accept it".
    """
    existing = harness.whm_servers.servers[0]
    forged = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAIForgedHostKeyDigest"

    response = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"ssh_host_key_fingerprint": forged}
    )

    assert response.status_code == 200, response.text
    assert response.json()["server"]["ssh_host_key_fingerprint"] != forged


def test_clearing_the_pin_is_how_an_operator_asks_for_a_recapture(harness: AdminHarness) -> None:
    """The deliberate half of the refresh path (`core.servers.validation`)."""
    existing = harness.whm_servers.servers[0]

    response = harness.client.patch(
        f"{WHM_SERVERS_PATH}/{existing.id}", json={"clear_ssh_host_key_fingerprint": True}
    )

    assert response.status_code == 200, response.text
    assert response.json()["server"]["ssh_host_key_fingerprint"] is None
    assert response.json()["server"]["has_ssh_password"] is True


# --- V14: the audit trail ---


def test_every_mutation_records_one_audit_event(harness: AdminHarness) -> None:
    """V14, and the *order* matters: the event precedes the commit (V100(a))."""
    created = harness.create_server(WHM_SERVERS_PATH, whm_create_body("audited"))
    server_id = created["id"]

    harness.client.patch(f"{WHM_SERVERS_PATH}/{server_id}", json={"api_username": "reseller"})
    harness.client.delete(f"{WHM_SERVERS_PATH}/{server_id}")

    recorded = [event.event_type for event in harness.audit.events]
    assert recorded == ["whm_server_created", "whm_server_updated", "whm_server_deleted"]
    for event in harness.audit.events:
        assert event.actor_email == "admin@example.com"
    assert harness.whm_servers.journal.entries == [
        "create",
        "commit",
        "update",
        "commit",
        "delete",
        "commit",
    ]


def test_a_refused_write_records_no_event_and_no_commit(harness: AdminHarness) -> None:
    """V100(a): the guards raise before the commit, so a refusal persists nothing."""
    response = harness.client.post(WHM_SERVERS_PATH, json=whm_create_body("existing-whm"))

    assert response.status_code == 409
    assert harness.audit.events == []
    assert harness.whm_servers.journal.entries == []


def test_an_audit_event_carries_no_credential(harness: AdminHarness) -> None:
    """V8: the trail records shape — hosts, ports, presence booleans — never material."""
    harness.create_server(WHM_SERVERS_PATH, whm_create_body("audited-secrets"))

    event = harness.audit.events[0]
    rendered = repr(event.metadata)
    for secret in SUBMITTED_SECRETS:
        assert secret not in rendered
    assert ENCRYPTED_PREFIX not in rendered
    assert event.metadata["has_ssh_password"] is True
    assert event.metadata["base_url"] == "https://audited-secrets.example.net:2087"


# --- The validate envelope ---


def test_validate_answers_200_with_the_service_verdict(harness: AdminHarness) -> None:
    """A reachable server: `ok: true`, and `error_code` absent rather than `""`."""
    existing = harness.whm_servers.servers[0]

    response = harness.client.post(f"{WHM_SERVERS_PATH}/{existing.id}/validate")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "message": "ok", "error_code": None}
    assert harness.whm_validation.calls == [existing.id]
    assert harness.whm_validation.actors == ["admin@example.com"]


def test_an_unreachable_server_is_still_200(harness: AdminHarness) -> None:
    """The operator asked whether it answers; "no, `ssh_timeout`" is that answer.

    A 502 here would throw at the panel's transport and render as an unhandled error instead of
    the red status chip `deriveWhmValidationStatus` draws off `result.ok`.
    """
    with admin_harness(
        pmg_rows=[pmg_server("unreachable")],
        validation_result=ServerValidationResult(
            ok=False, message="SSH connection timed out", error_code="ssh_timeout"
        ),
    ) as built:
        built.sign_in()
        server_id = built.pmg_servers.servers[0].id

        response = built.client.post(f"{PMG_SERVERS_PATH}/{server_id}/validate")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": False,
        "message": "SSH connection timed out",
        "error_code": "ssh_timeout",
    }


def test_validate_on_an_absent_row_is_404(harness: AdminHarness) -> None:
    """The one refusal this route has. Same code and same body as the other four."""
    response = harness.client.post(f"{PROXMOX_SERVERS_PATH}/{uuid4()}/validate")

    assert response.status_code == 404, response.text
    assert response.json()["error_code"] == "proxmox_server_not_found"


# --- V87: the clock-stamped fields ---


def test_create_response_shape_ignores_timestamps_but_still_separates(
    harness: AdminHarness,
) -> None:
    """V87: `created_at`/`updated_at` are asserted by property, and the compare still works.

    Two rows created from bodies that differ in one field must compare unequal once the
    timestamps are dropped — otherwise a field-dropping comparator has quietly become a
    tautology (V87's second clause, B4's shape).
    """
    first = harness.create_server(WHM_SERVERS_PATH, whm_create_body("stamped-a"))
    second = harness.create_server(
        WHM_SERVERS_PATH, whm_create_body("stamped-b", api_username="reseller")
    )

    # Dropped: the three the server stamps, plus the two that carry the row's identity — the
    # comparison is about the *rest* of the shape.
    def without_clock(server: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in server.items()
            if key not in {"id", "created_at", "updated_at", "name", "base_url"}
        }

    # Property, not value: present and ISO-8601-shaped.
    for server in (first, second):
        assert isinstance(server["created_at"], str)
        assert server["created_at"].startswith("20")
        assert isinstance(server["updated_at"], str)

    assert without_clock(first) != without_clock(second)
    # ...and the comparator does not report a difference that is not there.
    assert without_clock(first) == without_clock(
        harness.create_server(WHM_SERVERS_PATH, whm_create_body("stamped-c"))
    )


# --- Listing ---


def test_the_list_is_ordered_by_name(harness: AdminHarness) -> None:
    """The panel's table, the tool result and the ambiguity `choices` share one order."""
    for name in ("zulu", "alpha", "mike"):
        harness.create_server(WHM_SERVERS_PATH, whm_create_body(name))

    names = [server["name"] for server in harness.list_servers(WHM_SERVERS_PATH)]

    assert names == sorted(names)


def test_each_vertical_lists_only_its_own_table(harness: AdminHarness) -> None:
    """Three tables, three lists. A shared double would hide a router reading the wrong one."""
    assert [server["name"] for server in harness.list_servers(WHM_SERVERS_PATH)] == ["existing-whm"]
    assert [server["name"] for server in harness.list_servers(PROXMOX_SERVERS_PATH)] == [
        "existing-pve"
    ]
    assert [server["name"] for server in harness.list_servers(PMG_SERVERS_PATH)] == [
        "existing-mail"
    ]


def test_delete_removes_the_row_and_answers_ok(harness: AdminHarness) -> None:
    existing: UUID = harness.pmg_servers.servers[0].id

    response = harness.client.delete(f"{PMG_SERVERS_PATH}/{existing}")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert harness.pmg_servers.servers == []
