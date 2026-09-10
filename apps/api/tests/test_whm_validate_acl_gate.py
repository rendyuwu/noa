"""Validate refuses a WHM row that cannot suspend, and reports the ACL set.

`test_server_admin_service.py` owns which probes run in which order and
`test_server_host_key_validation.py` owns the pin. What lives here is the answer the API probe
produces now that it is `myprivs`: a capability check whose verdict an admin can act on before
any operator sees an approval card.

The point of moving the check here is a cost, not a purity argument. Discovered at execute, a
missing `suspend-acct` costs an operator a reason they typed for a change WHM was always going
to refuse — and reads as a NOA fault, because everything NOA showed them said the server was
green. Discovered at validate, it is a row an admin edits.

Real `WHMClient` over `httpx.MockTransport` throughout, real `SecretCipher`, real
`resolve_whm_ssh_config`; only the network and the SSH probe are doubled. The ACL set is parsed
out of a WHM response body, so a doubled client would test the double.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from core.integrations.whm.client import (
    WHM_ACL_LIST_ACCOUNTS,
    WHM_ACL_SUSPEND_ACCOUNT,
    WHMClient,
    build_whm_client_from_creds,
)
from core.remote_exec.types import SSHConnectionConfig
from core.secrets.crypto import SecretCipher
from core.servers.validation import (
    WHM_ACL_INSUFFICIENT_CODE,
    ServerValidationResult,
    WHMServerValidationService,
    _whm_acl_answer,
    _whm_acl_metadata,
)
from support.rbac import RecordingAuditSink
from support.secrets import build_cipher
from support.server_admin import FakeHostKeyPinRepository, RecordingSessionFactory
from support.servers import whm_server
from support.whm_api import (
    MYPRIVS_PATH,
    FakeWHMApi,
    myprivs_body,
    reseller_privileges,
    whm_api_failure_body,
)

API_TOKEN = "WHM-API-TOKEN-THAT-MUST-NOT-BE-REPORTED"
CAPTURED_FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAICapturedByValidate"

# The measured refusal from a live host, kept verbatim: the XID differs on every call and the
# quotes are U+201C/U+201D, which is exactly why this text is classified by pattern, never
# byte-compared.
MEASURED_REFUSAL = "API failure: (XID 3y67mz) You do not have a user named “web08”."
XID = re.compile(r"\(XID \w+\)")


def _without_xid(text: str) -> str:
    """The refusal minus the one part of it that changes per call."""
    return XID.sub("(XID)", text)


@pytest.fixture
def cipher() -> SecretCipher:
    return build_cipher()


def _client_factory(api: FakeWHMApi) -> Callable[..., WHMClient]:
    def factory(server: Any, *, cipher: SecretCipher) -> WHMClient:
        return build_whm_client_from_creds(
            base_url=server.base_url,
            api_username=server.api_username,
            encrypted_token=server.api_token,
            verify_ssl=server.verify_ssl,
            cipher=cipher,
            transport=api.transport,
        )

    return factory


def _recording_probe() -> Any:
    calls: list[SSHConnectionConfig] = []

    async def probe(config: SSHConnectionConfig) -> None:
        calls.append(config)

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


async def _capture(_config: SSHConnectionConfig) -> str:
    return CAPTURED_FINGERPRINT


def _build(
    *,
    cipher: SecretCipher,
    api: FakeWHMApi,
    with_ssh: bool = False,
    probe: Callable[[SSHConnectionConfig], Awaitable[None]] | None = None,
) -> tuple[WHMServerValidationService, Any, FakeHostKeyPinRepository, RecordingAuditSink]:
    """One WHM row and the service over it. `with_ssh` decides whether the SSH half runs at
    all — the ACL gate sits in front of it, so most cases here do not need it."""
    row = whm_server(
        "web08",
        api_token=cipher.encrypt_text(API_TOKEN),
        ssh_password=cipher.encrypt_text("ssh-password") if with_ssh else None,
        ssh_private_key=None,
        ssh_host_key_fingerprint=None,
    )
    pins = FakeHostKeyPinRepository([row])
    audit = RecordingAuditSink()
    service = WHMServerValidationService(
        session_factory=RecordingSessionFactory(),
        repository_factory=lambda _session: pins,
        cipher=cipher,
        audit_sink=audit,
        client_factory=_client_factory(api),
        capture_host_key=_capture,
        probe=probe or _recording_probe(),
    )
    return service, row, pins, audit


# --- the refusal, and the negative control that proves it separates ---


async def test_a_token_without_suspend_acct_is_refused_at_validate(cipher: SecretCipher) -> None:
    """`whm_token_acl_insufficient`, and the SSH half never runs.

    A row NOA will not let anybody change is not made usable by its host key being reachable,
    and the operator's remedy — re-issue the token with the ACL — is the same either way.
    """
    api = FakeWHMApi(body=myprivs_body(reseller_privileges(**{"suspend-acct": ""})))
    probe = _recording_probe()
    service, row, pins, _ = _build(cipher=cipher, api=api, with_ssh=True, probe=probe)

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == WHM_ACL_INSUFFICIENT_CODE
    assert probe.calls == []
    assert pins.pins == []
    assert [request.url.path for request in api.requests] == [MYPRIVS_PATH]


async def test_a_token_with_suspend_acct_validates_green(cipher: SecretCipher) -> None:
    """The negative control. Without it, "validate refused" is all the suite knows, and
    a gate that refuses everything would pass the test above."""
    api = FakeWHMApi(body=myprivs_body(reseller_privileges()))
    service, row, _, _ = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert result.ok is True
    assert result.error_code is None


@pytest.mark.parametrize(
    ("value", "expected_ok"),
    [
        (1, True),  # root's spelling, measured on a live host
        ("1", True),  # the reseller's spelling for the same grant on the same host
        (0, False),
        ("", False),
        ("maybe", False),
    ],
)
async def test_the_gate_reads_every_measured_spelling_of_the_flag(
    cipher: SecretCipher, value: object, expected_ok: bool
) -> None:
    """The gate goes through the client's truth table rather than carrying its own.

    Two readers of one table drift, and the direction one of them drifts in is an operator
    typing a reason for a change WHM refuses. The table itself is tested exhaustively in
    `test_whm_privileges.py`; this asserts the gate is wired to it.
    """
    api = FakeWHMApi(body=myprivs_body(reseller_privileges(**{"suspend-acct": value})))
    service, row, _, _ = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert result.ok is expected_ok


async def test_an_absent_suspend_acct_key_is_refused_like_an_empty_one(
    cipher: SecretCipher,
) -> None:
    """The third not-granted spelling: WHM sends no such key at all."""
    privileges = reseller_privileges()
    privileges.pop("suspend-acct")
    service, row, _, _ = _build(cipher=cipher, api=FakeWHMApi(body=myprivs_body(privileges)))

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == WHM_ACL_INSUFFICIENT_CODE


# --- The ACL set gets REPORTED, not just `ok` ---


async def test_the_acl_set_reaches_the_message_and_the_audit_metadata(
    cipher: SecretCipher,
) -> None:
    """Both surfaces, because they answer different questions.

    The message is what an admin reads now — the two ACLs that decide what the row may do, and
    a count for the rest, because an unrestricted root token grants around a hundred and the
    two that matter would be lost in them. The metadata is the forensic copy: the whole set,
    for the day somebody asks what this credential could do six months ago.
    """
    api = FakeWHMApi(body=myprivs_body(reseller_privileges()))
    service, row, _, audit = _build(cipher=cipher, api=api)

    result = await service.validate(row.id, actor_email="admin@example.com")

    assert "suspend-acct: granted" in result.message
    assert "list-accts: granted" in result.message
    metadata = audit.events[0].metadata
    reported = metadata["whm_acls"]
    # By property, not by the literal count: a fixture that gains a key later breaks a
    # hard-coded `3` for a reason that has nothing to do with what this test is about. The
    # count an admin reads is the size of the set the trail kept — one fact on two surfaces.
    assert f"({len(reported)} granted)" in result.message
    # And it is the fixture's own grants rather than a number that happens to line up: the two
    # decisive names are in the set, and the write ACLs the measured reseller does not hold
    # (measured on a live host) stayed out of it.
    assert WHM_ACL_SUSPEND_ACCOUNT in reported
    assert WHM_ACL_LIST_ACCOUNTS in reported
    assert "all" not in reported
    assert "create-acct" not in reported
    assert reported == sorted(reported)
    assert metadata["whm_acl_suspend_acct"] is True
    assert metadata["whm_acl_list_accts"] is True


async def test_the_refusal_also_reports_the_set_it_refused_on(cipher: SecretCipher) -> None:
    """A red validate that names only its code sends an admin to guess which ACL is missing."""
    api = FakeWHMApi(body=myprivs_body(reseller_privileges(**{"suspend-acct": 0})))
    service, row, _, audit = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert "suspend-acct: missing" in result.message
    assert "list-accts: granted" in result.message
    metadata = audit.events[0].metadata
    assert metadata["whm_acl_suspend_acct"] is False
    assert metadata["whm_acl_list_accts"] is True
    assert "suspend-acct" not in metadata["whm_acls"]


async def test_a_missing_list_accts_is_reported_and_not_refused(cipher: SecretCipher) -> None:
    """The one-ACL-both-directions rule's second half, and it is a report rather than a gate.

    A row is refused for what it cannot do on the CHANGE path, which is the path that costs a
    typed reason. A row that can suspend but cannot list is a strange configuration, not an
    unusable one — so it is named on every validate, green included, and left to the admin.
    """
    api = FakeWHMApi(body=myprivs_body(reseller_privileges(**{"list-accts": ""})))
    service, row, _, audit = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert result.ok is True
    assert "list-accts: missing" in result.message
    assert audit.events[0].metadata["whm_acl_list_accts"] is False


async def test_the_acl_report_survives_the_ssh_half(cipher: SecretCipher) -> None:
    """A row with SSH credentials gets both probes, and the second must not overwrite the
    first's answer with a bare `ok` — that would drop the ACL set on exactly the rows that
    have the most configured."""
    api = FakeWHMApi(body=myprivs_body(reseller_privileges()))
    service, row, pins, audit = _build(cipher=cipher, api=api, with_ssh=True)

    result = await service.validate(row.id)

    assert result.ok is True
    assert "suspend-acct: granted" in result.message
    assert pins.pins == [CAPTURED_FINGERPRINT]
    assert audit.events[0].metadata["whm_acl_suspend_acct"] is True


# --- "Could not ask" is not "holds nothing" ---


async def test_a_probe_that_never_answered_records_an_unknown_acl_set(
    cipher: SecretCipher,
) -> None:
    """WHM refused the probe itself, so the ACL set is `None` in the trail, not `[]`.

    An empty list would read, later, as a credential that held no privileges — a fact nobody
    established. The verdict names the source that did not answer: `whm_api_error` with WHM's
    own reason, which is a different remedy from a missing ACL.
    """
    api = FakeWHMApi(body=whm_api_failure_body("Access denied"))
    service, row, _, audit = _build(cipher=cipher, api=api, with_ssh=True)

    result = await service.validate(row.id)

    assert result == ServerValidationResult(
        ok=False, message="Access denied", error_code="whm_api_error"
    )
    metadata = audit.events[0].metadata
    assert metadata["whm_acls"] is None
    assert metadata["whm_acl_suspend_acct"] is None
    assert metadata["whm_acl_list_accts"] is None


async def test_an_unreadable_privileges_shape_is_not_an_empty_acl_set(
    cipher: SecretCipher,
) -> None:
    """Same rule one layer down: a body whose `data.privileges` will not unwrap is
    `invalid_response`, not a token with nothing in it."""
    api = FakeWHMApi(body={"metadata": {"result": 1, "reason": "OK"}, "data": {"privileges": []}})
    service, row, _, audit = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert result.ok is False
    assert result.error_code == "invalid_response"
    assert audit.events[0].metadata["whm_acls"] is None


@pytest.mark.parametrize("payload", [{"ok": True, "message": "ok"}, {"ok": True, "acls": "1"}])
def test_an_ok_payload_with_no_acl_set_records_unknown_not_empty(
    payload: dict[str, object],
) -> None:
    """The same rule one branch over, where nothing routes today.

    `_whm_acl_answer` is called directly because there is no way to reach this through the
    service: `WHMClient.privileges` synthesises `acls` on every success, so a fixture cannot
    produce an `ok` payload without one. That is the argument for testing it rather than
    against — the branch is silent, and a second caller of this helper arrives without anyone
    re-deriving what an absent `acls` should mean.

    `()` here would record `whm_acls: []` — "the token holds nothing granted" — for a set that
    was never read. The verdict fails closed and names the source that did not answer.
    """
    answer, acls = _whm_acl_answer(payload)

    # The recorded ACL set first, because it is the assertion that carries the rule: a
    # regression here has to fail ON the metadata, not on a verdict code that happens to
    # change with it.
    assert acls is None
    assert _whm_acl_metadata(acls) == {
        "whm_acls": None,
        "whm_acl_suspend_acct": None,
        "whm_acl_list_accts": None,
    }
    assert answer.ok is False
    assert answer.error_code == "invalid_response"
    assert "myprivs" in answer.message


def test_an_acl_set_that_answered_with_nothing_granted_is_empty_not_unknown() -> None:
    """The separator: `()` and `None` must not both mean "no". WHM answered, and it granted
    nothing — a token to re-issue, which is a different remedy from a probe that never ran."""
    answer, acls = _whm_acl_answer({"ok": True, "acls": []})

    assert answer.ok is False
    assert answer.error_code == WHM_ACL_INSUFFICIENT_CODE
    assert acls == ()
    assert _whm_acl_metadata(acls)["whm_acls"] == []


# --- WHM's own text is passed through, so assert on it by property ---


async def test_whms_reason_reaches_the_operator_and_compares_by_property(
    cipher: SecretCipher,
) -> None:
    """The XID is stripped before comparing, and the curly quotes are expected, not normalised.

    Byte-comparing this message passes once and fails on the next call with a different XID.
    """
    api = FakeWHMApi(body=whm_api_failure_body(MEASURED_REFUSAL))
    service, row, _, _ = _build(cipher=cipher, api=api)

    result = await service.validate(row.id)

    assert result.error_code == "whm_api_error"
    assert _without_xid(result.message) == _without_xid(MEASURED_REFUSAL)
    assert "“" in result.message and "”" in result.message
    assert "(XID 3y67mz)" in result.message  # the raw value did reach the operator


async def test_the_xid_stripped_compare_still_separates_two_reasons() -> None:
    """The separating case: a compare that ignores the volatile part must still fail on
    text that differs anywhere else, or it is asserting nothing."""
    other = "API failure: (XID zz99aa) You do not have a user named “web09”."

    assert _without_xid(other) != _without_xid(MEASURED_REFUSAL)
    assert _without_xid(other.replace("zz99aa", "3y67mz")) != _without_xid(MEASURED_REFUSAL)


# --- The credential is never in what validate reports ---


async def test_the_api_token_appears_in_neither_the_message_nor_the_metadata(
    cipher: SecretCipher,
) -> None:
    api = FakeWHMApi(body=myprivs_body(reseller_privileges()))
    service, row, _, audit = _build(cipher=cipher, api=api, with_ssh=True)

    result = await service.validate(row.id, actor_email="admin@example.com")

    assert API_TOKEN in api.authorization_headers[0]  # it was presented
    assert API_TOKEN not in result.message
    assert API_TOKEN not in str(audit.events[0].metadata)
