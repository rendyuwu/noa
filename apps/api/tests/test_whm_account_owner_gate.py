"""The owner-vs-credential gate on WHM account changes.

cPanel gates an account write on **ownership** rather than on the token's ACL, measured on a live
host: a root token with `suspend-acct` granted and `all` absent is refused for an account
it does not own. So `server_ref` on an account CHANGE names the account's `owner`, and the
credential that may perform the change is the row whose `api_username` *is* that owner.

What that makes testable is not "an exception was raised". Four claims, and each is asserted on
the thing it is about:

- **no card exists.** The recorded `action_requests` rows are counted, because the cost this guard
  exists to prevent is an operator's decision *and* a reason typed for nothing — and a reason is
  born at decision time, so a burnt one cannot be recovered. A refusal that still wrote the
  row would satisfy an exception assertion and none of the invariant.
- **the refusal is usable.** The message carries the `server_ref` that would have worked, because
  the model constructed the argument and cannot infer the right value from a bare refusal.
- **a matching credential still works.** The negative control. Without it the suite cannot tell a
  guard from a tool that refuses everything (the compare must still separate, one system over).
- **the check runs again after the approval.** A `whm_servers` row is editable between a request
  and its decision, so the runner reads `api_username` live and compares it against the
  `owner` the card was built from. Asserted on the mutation *count* at the WHM socket: zero
  requests to `suspendacct` is the only evidence that nothing ran.

Seams are the suspend tool's, unchanged: the real `WHMClient` over a doubled socket, the real
resolver, the real `open_change_request` inside a real request context, the real
`sanitize_tool_errors`. Only the socket, the SQL and the directory are doubles.

The pure compare is exercised directly as well as through the tools, because its bound is a
decision rather than a detail: it fires on a **positive** disagreement only, so a host that does
not report `owner` keeps working and WHM's own refusal stays the authoritative one there.
"""

from __future__ import annotations

import json
from typing import Any, Final
from uuid import UUID, uuid4

from structlog.testing import capture_logs

from core.approvals.context import AUDIT_IDENTITY_KEYS, audit_identity_from_context
from core.approvals.execution import ChangeExecutionRequest, build_receipt
from core.db.models import WHMServer
from core.servers.naming import normalize_whm_identity
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.registry import register_mcp_tools
from noa_api.mcp_tools.whm_account_change import (
    EVIDENCE_ACCOUNT,
    EVIDENCE_API_USERNAME,
    EVIDENCE_HOST,
    EVIDENCE_OWNER,
    EVIDENCE_SERVER_ID,
    EVIDENCE_SERVER_NAME,
    STATUS_NO_OP,
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
    whm_suspend_account,
    whm_unsuspend_account,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
)
from noa_api.mcp_tools.whm_account_owner_gate import (
    ERROR_ACCOUNT_OWNER_UNKNOWN,
    ERROR_WRONG_CREDENTIAL_FOR_OWNER,
    EVIDENCE_UNRECORDED,
    SITE_PREFLIGHT,
    SITE_RUNNER,
    Ownership,
    classify_ownership,
    recorded,
)
from support.action_decisions import REASON
from support.change_delta import payload_runner
from support.mcp_identity import authenticated_caller, http_request_context
from support.servers import SECRETS, ToolFixture, build_tool_context, whm_server
from support.whm_api import (
    LISTACCTS_PATH,
    SUSPENDACCT_PATH,
    UNSUSPENDACCT_PATH,
    FakeWHMApi,
    listaccts_body,
    whm_account,
    whm_api_success_body,
)

SERVER_NAME = "alpha"
HOST = "alpha.example.net"
ACCOUNT = "acmeco"

# The reseller that owns the account on the measured host's naming — and, once the row
# holding its credential is named after it, the `server_ref` an account change has to carry.
OWNER = "web08cpnpool01"

# The credential on a row that is *not* the owner's. `root` rather than a made-up name because that
# is the case measured on the live host: a root token with `suspend-acct` granted is still refused.
OTHER_CREDENTIAL = "root"
ROOT = "root"

# A second reseller credential, for the one case that needs the WRONG row to be a reseller: its
# `name` is its `api_username`, so a refusal built from the resolved row's name would disclose it.
OTHER_RESELLER = "web08cpnpool02"

WHM_API_TOKEN = "whm-api-token-plaintext"


# The sentinel for "WHM sent no `owner` key at all", as distinct from sending an empty one.
_NO_OWNER_KEY: Final = object()


def _account_without_owner(*, suspended: object = 0) -> dict[str, Any]:
    """One `listaccts` row with no `owner` key, which is what a host that does not report
    ownership sends. `whm_account` only adds the field when it is passed one."""
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=suspended)


def owned_account(*, owner: str | None = OWNER, suspended: object = 0) -> dict[str, Any]:
    """One `listaccts` row, with or without WHM's `owner` field.

    `owner=None` omits the field entirely rather than sending an empty one: that is what a
    cPanel version which does not report ownership does, and it is the case the guard's bound
    is about.
    """
    extra = {} if owner is None else {"owner": owner}
    return whm_account(ACCOUNT, domain="acme.example.com", suspended=suspended, **extra)


def whm_endpoint(
    *,
    listings: list[list[dict[str, Any]]] | None = None,
    mutation_path: str = SUSPENDACCT_PATH,
) -> FakeWHMApi:
    """A WHM endpoint answering `listaccts` from a queue and one mutation once."""
    rows = listings or [[owned_account()]]
    return FakeWHMApi(
        body=listaccts_body([]),
        scripted={
            LISTACCTS_PATH: [listaccts_body(listing) for listing in rows],
            mutation_path: [whm_api_success_body()],
        },
    )


def gate_context(
    endpoint: FakeWHMApi | None = None,
    *,
    extra: list[WHMServer] | None = None,
) -> tuple[ToolFixture, FakeWHMApi, WHMServer, WHMServer]:
    """Two `whm_servers` rows on **one** host: the machine, and a reseller's credential.

    This is the inventory measured on the live host rather than a minimal one, and the shape is what
    makes the refusal's remedy checkable: a root-credential row named after the box, a reseller-
    credential row named after the credential, both pointing at the same WHM host, because that is
    what two API tokens on one server are. `server_ref` resolves by name, which is unique, so the
    shared `base_url` is not an ambiguity — and a test that had only the wrong row could assert a
    refusal but never that the value it printed leads anywhere.
    """
    api = endpoint or whm_endpoint()
    cipher = build_tool_context().cipher
    base_url = f"https://{HOST}:2087"
    machine = whm_server(SERVER_NAME, base_url=base_url, api_username=OTHER_CREDENTIAL)
    reseller = whm_server(OWNER, base_url=base_url, api_username=OWNER, is_reseller_credential=True)
    rows = [machine, reseller, *(extra or [])]
    for row in rows:
        row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    fixture = build_tool_context(servers=rows, cipher=cipher, whm_transport=api.transport)
    return fixture, api, machine, reseller


async def suspend(fixture: ToolFixture, *, server_ref: str = SERVER_NAME) -> Any:
    """Call `whm_suspend_account` inside a real request context."""
    user, _ = authenticated_caller(None)
    with http_request_context({}, user=user):
        return await whm_suspend_account(
            server_ref=server_ref, username=ACCOUNT, context=fixture.context
        )


async def unsuspend(fixture: ToolFixture, *, server_ref: str = SERVER_NAME) -> Any:
    """Call `whm_unsuspend_account` inside a real request context."""
    user, _ = authenticated_caller(None)
    with http_request_context({}, user=user):
        return await whm_unsuspend_account(
            server_ref=server_ref, username=ACCOUNT, context=fixture.context
        )


def execution_request(
    *,
    server_id: UUID | str,
    owner: str | None = OWNER,
    host: str = HOST,
) -> ChangeExecutionRequest:
    """What the executor hands a runner for an approved suspension.

    The evidence carries the four recorded fields the way the gate wrote them, `owner` included,
    because the runner's re-check reads that key rather than re-deriving it.
    """
    evidence: dict[str, Any] = {
        EVIDENCE_SERVER_ID: str(server_id),
        EVIDENCE_SERVER_NAME: SERVER_NAME,
        EVIDENCE_API_USERNAME: OWNER,
        EVIDENCE_HOST: host,
        EVIDENCE_OWNER: EVIDENCE_UNRECORDED if owner is None else owner,
        EVIDENCE_ACCOUNT: {"user": ACCOUNT, "owner": owner, "suspended": False},
    }
    return ChangeExecutionRequest(
        action_request_id=uuid4(),
        tool_run_id=uuid4(),
        tool_name=TOOL_WHM_SUSPEND_ACCOUNT,
        arguments={"server_ref": OWNER, "username": ACCOUNT},
        evidence=evidence,
        reason=REASON,
    )


# --------------------------------------------------------------------------------------
# The compare itself: what counts as one identity, and what counts as no answer
# --------------------------------------------------------------------------------------


def test_the_same_name_in_another_case_or_with_spaces_is_one_identity() -> None:
    """The owner compare's normalisation, both sides: `strip().lower()`.

    WHM echoes an owner as it was typed and an operator types the row's credential, so a
    byte-exact compare would refuse a change the credential is entitled to make — the most
    expensive possible false positive, since the remedy it would print is the value the caller
    already passed.
    """
    assert classify_ownership(owner=OWNER, api_username=OWNER) is Ownership.OWNED
    assert classify_ownership(owner="  Web08CpnPool01 ", api_username=OWNER) is Ownership.OWNED
    assert classify_ownership(owner=OWNER, api_username=" WEB08CPNPOOL01\t") is Ownership.OWNED
    assert classify_ownership(owner=" Owner ", api_username="owner") is Ownership.OWNED


def test_two_different_names_are_a_disagreement() -> None:
    """The separating case, so the normalisation above is not just lowering everything to equal
    (a compare that stopped separating would pass every test above)."""
    wrong = Ownership.WRONG_CREDENTIAL
    assert classify_ownership(owner=OWNER, api_username=OTHER_CREDENTIAL) is wrong
    assert classify_ownership(owner="web08cpnpool01", api_username="web08cpnpool02") is wrong


def test_a_name_that_could_not_be_read_is_its_own_verdict_and_it_refuses() -> None:
    """A non-answer names itself, and this is the branch the owner inverted: silence is **not** the
    benign value.

    The unsuspend tool's lock guard fails open on an absent `is_locked` because `listaccts`
    demonstrably omits that field where cPanel lacks it, so failing closed would take the tool off a
    whole cPanel generation. `owner` has the opposite evidence — measured present on 451 of 451 rows
    across 7 owners, never blank — so failing closed costs nothing measured, while failing open buys
    the one harm owner-as-`server_ref` exists to prevent: an operator decision spent on a card that
    must fail, and a reason typed for nothing that cannot be recovered.

    Two verdicts rather than one, because the two halves are not the same fact: WHM said nothing,
    or NOA's own row records nothing. `EVIDENCE_UNRECORDED` counts as unreadable on both sides —
    the word for a non-answer must never compare as a username, or the answer would be
    `WRONG_CREDENTIAL`, the one sentence that would be a lie about it.
    """
    for unreadable in (None, "", "   ", 7, {"owner": OWNER}, EVIDENCE_UNRECORDED):
        assert (
            classify_ownership(owner=unreadable, api_username=OWNER) is Ownership.OWNER_NOT_REPORTED
        )
        assert (
            classify_ownership(owner=OWNER, api_username=unreadable)
            is Ownership.CREDENTIAL_NOT_RECORDED
        )
    # Both silent: the owner is the half a caller could have seen, so it is the half named.
    assert classify_ownership(owner=None, api_username=None) is Ownership.OWNER_NOT_REPORTED


def test_only_one_of_the_four_verdicts_lets_a_change_proceed() -> None:
    """The whole gate in one assertion: `is_proven` is what both call sites branch on, so a
    verdict added later without a decision about it fails here rather than opening a card."""
    proven = {verdict for verdict in Ownership if verdict.is_proven}
    assert proven == {Ownership.OWNED}


def test_the_compare_agrees_with_the_shared_identity_normalisation() -> None:
    """One normalisation for two guards, driven from the shared function rather than from
    strings this test picked.

    The admin write refuses a reseller row whose `name` is not its `api_username`, and
    the preflight refuses an account whose `owner` is not the resolved row's
    `api_username`. Both read the same two columns, so a second spelling of `strip().lower()`
    would let them disagree about one row — the admin surface would accept a row this gate then
    refuses, and the operator would see a refused CHANGE with nothing naming the cause.

    Asserted as an equivalence over pairs rather than as a call count, so a copy of the
    normalisation that happens to agree today keeps passing and one that drifts fails the
    moment it disagrees.
    """
    pairs = (
        (OWNER, OWNER),
        ("  Web08CpnPool01 ", OWNER),
        ("ROOT", OTHER_CREDENTIAL),
        (" owner\t", "OWNER"),
        ("web08cpnpool01", "web08cpnpool02"),
        (OWNER, OTHER_CREDENTIAL),
    )
    for owner, api_username in pairs:
        one_identity = normalize_whm_identity(owner) == normalize_whm_identity(api_username)
        verdict = classify_ownership(owner=owner, api_username=api_username)
        assert (verdict is Ownership.OWNED) is one_identity


def test_a_field_nobody_recorded_is_named_rather_than_left_null() -> None:
    """A source that did not answer gets named beside the verdict. The card renders
    evidence generically, so the alternative is a `null` an operator reads as a bug."""
    assert recorded(OWNER) == OWNER
    assert recorded(None) == EVIDENCE_UNRECORDED
    assert recorded("  ") == EVIDENCE_UNRECORDED


# --------------------------------------------------------------------------------------
# The preflight: refused before `action_requests` is written
# --------------------------------------------------------------------------------------


async def test_a_credential_that_does_not_own_the_account_opens_no_request() -> None:
    """Owner-as-`server_ref`, and the row count is the assertion that matters.

    "It refused" would pass against a guard that refused *after* writing the PENDING row, which
    is the one outcome this invariant exists to prevent: a card an operator has to decide, plus
    a reason typed for a run that cannot succeed.
    """
    fixture, api, _, _ = gate_context()

    answer = await suspend(fixture, server_ref=SERVER_NAME)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_WRONG_CREDENTIAL_FOR_OWNER
    assert fixture.action_requests.requests == []
    assert fixture.action_requests.commits == []
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_the_refusal_carries_the_remedy_and_the_value_that_was_passed() -> None:
    """The remedy travels with the refusal or the next call is the same call.

    Three things in one sentence, and each is load-bearing. The **owner**, because that is what
    identifies the credential and the model already has it from `whm_search_accounts`. The
    `server_ref` **the caller passed**, so "a different credential" is anchored to something it
    recognises rather than to a row name it may never have seen (the URL carries an id only — see
    the disclosure test below). And the **rule** for turning an owner into a `server_ref`, which is
    not "pass the owner": that resolves only for a reseller, and the root branch needs the machine's
    own row.
    """
    fixture, _, _, _ = gate_context()

    answer = await suspend(fixture, server_ref=SERVER_NAME)

    message = answer["message"]
    assert OWNER in message
    assert f"`server_ref` `{SERVER_NAME}`" in message
    assert f"named `{OWNER}` if the owner has its own reseller credential" in message
    assert "id, name or hostname" in message


async def test_the_owning_credential_opens_the_request_as_before() -> None:
    """The negative control. Without it the suite cannot tell this guard from a tool that
    refuses every change, and "it raised" would be all the suite knows."""
    fixture, api, _, _ = gate_context()

    answer = await suspend(fixture, server_ref=OWNER)

    assert not isinstance(answer, dict)
    request = fixture.action_requests.only
    assert request.tool_name == TOOL_WHM_SUSPEND_ACCOUNT
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_the_remedy_the_refusal_printed_resolves_and_opens_the_card() -> None:
    """The two halves of the owner gate in one call sequence, which is the only place the remedy is
    proved rather than pattern-matched.

    A message naming something that resolves to nothing would satisfy every assertion in the
    test above and leave the model exactly where it started. So: call with the machine, take the
    name the refusal points at, call again with *that string*, and require a card. The second
    call also has to reach a different row than the first — asserted on the recorded evidence's
    `api_username`, because "it worked the second time" is otherwise consistent with the guard
    having been skipped.

    This is the reseller branch, where the owner name *is* a resolvable `server_ref` because the
    name == `api_username` rule forces the row to be named after its credential. The root branch is
    two tests down, and it is the one that showed the earlier wording was wrong.
    """
    fixture, _, _, reseller = gate_context(
        whm_endpoint(listings=[[owned_account()], [owned_account()]])
    )

    refused = await suspend(fixture, server_ref=SERVER_NAME)
    assert f"API username is `{OWNER}`" in refused["message"]
    answer = await suspend(fixture, server_ref=OWNER)

    assert not isinstance(answer, dict)
    evidence = fixture.action_requests.only.approval_context["evidence"]
    assert evidence[EVIDENCE_SERVER_ID] == str(reseller.id)
    assert evidence[EVIDENCE_API_USERNAME] == OWNER


async def test_a_root_owned_account_is_not_told_to_pass_the_owner_as_a_server_ref() -> None:
    """The bug the review found: for a root-owned account the owner string resolves to nothing.

    `resolve_whm_server_ref` matches an id, a `name` or a hostname and never `api_username`
    (`core.servers.reference`), and the name rule binds only rows flagged as reseller credentials —
    "the sixteen root rows cannot all be named `root`"
    (`core.servers.errors.WHMResellerCredentialNameMismatchError`). 56 of the 451 accounts measured
    on the live host are owned by `root`, so this is the common case, not a corner.

    An earlier draft printed "Call this tool again with `server_ref` set to `root`", which is a
    refusal whose remedy is another refusal. The sentence now has to name the machine's own row
    instead, and that is what is asserted — including the absence of the imperative form, because
    a message can name the right thing and still tell the model to do the wrong one.
    """
    fixture, _, _, _ = gate_context(whm_endpoint(listings=[[owned_account(owner=ROOT)]]))

    answer = await suspend(fixture, server_ref=OWNER)

    assert answer["error_code"] == ERROR_WRONG_CREDENTIAL_FOR_OWNER
    assert "set to `root`" not in answer["message"]
    assert "id, name or hostname" in answer["message"]
    assert f"API username is `{ROOT}`" in answer["message"]


async def test_a_root_owned_account_proceeds_on_the_machines_own_row() -> None:
    """The other half, and the negative control for the test above: the machine's row *is* the
    credential for a root-owned account, so passing its name opens the card."""
    fixture, api, machine, _ = gate_context(whm_endpoint(listings=[[owned_account(owner=ROOT)]]))

    answer = await suspend(fixture, server_ref=SERVER_NAME)

    assert not isinstance(answer, dict)
    evidence = fixture.action_requests.only.approval_context["evidence"]
    assert evidence[EVIDENCE_SERVER_ID] == str(machine.id)
    assert evidence[EVIDENCE_API_USERNAME] == ROOT
    assert evidence[EVIDENCE_OWNER] == ROOT
    assert api.requests_to(SUSPENDACCT_PATH) == []


async def test_the_refusal_never_prints_a_resolved_reseller_rows_name() -> None:
    """The sentence echoes what the caller passed, never the row it resolved to.

    A reseller row's `name` *is* its `api_username` and those rows are kept out of
    `whm_list_servers` output, so a message built from the resolved row's name discloses a
    credential username the model could not otherwise see — into a transcript that persists in
    LibreChat's MongoDB. The caller here passes a second reseller's **id**, so the row's name is
    something it has never seen; printing it would be the leak, and echoing the id it typed is
    both safe and more useful.

    The fixture needs the wrong row to be a *reseller*: with a root-credentialled wrong row the
    resolved name is `alpha` and nothing would be disclosed, which is why the earlier version of
    this suite could not catch it.
    """
    other = whm_server(
        OTHER_RESELLER,
        base_url=f"https://{HOST}:2087",
        api_username=OTHER_RESELLER,
        is_reseller_credential=True,
    )
    fixture, _, _, _ = gate_context(extra=[other])

    answer = await suspend(fixture, server_ref=str(other.id))

    assert answer["error_code"] == ERROR_WRONG_CREDENTIAL_FOR_OWNER
    assert OTHER_RESELLER not in answer["message"]
    assert str(other.id) in answer["message"]
    assert OWNER in answer["message"]


async def test_an_account_whm_reports_no_owner_for_is_refused_and_opens_no_request() -> None:
    """Non-answer named beside the verdict, through the real tool, in all three spellings WHM could
    send.

    `normalize_whm_account_summary` collapses a missing key, `""` and `None` into one absence
    (`_optional_string`: "blank is absence"), and that collapse is exactly why all three are
    asserted here rather than one: a blank string reaching the compare as a readable name would
    make the verdict `WRONG_CREDENTIAL` and print a remedy that cannot work, and this is where
    that hole would show.

    The live host measured `owner` present on 451 of 451 rows, so nothing below is reachable on the
    measured hardware — which is the reason it is tested rather than reasoned about. An
    unexercised control is what parked `redaction.py`: a control with no caller is untested.
    """
    for absent in (_NO_OWNER_KEY, None, "", "   "):
        rows = (
            [_account_without_owner()] if absent is _NO_OWNER_KEY else [owned_account(owner=absent)]
        )
        fixture, api, _, _ = gate_context(whm_endpoint(listings=[rows]))

        answer = await suspend(fixture, server_ref=OWNER)

        assert answer["ok"] is False, absent
        assert answer["error_code"] == ERROR_ACCOUNT_OWNER_UNKNOWN, absent
        assert fixture.action_requests.requests == [], absent
        assert fixture.action_requests.commits == [], absent
        assert api.requests_to(SUSPENDACCT_PATH) == [], absent


async def test_the_unknown_owner_refusal_says_whm_did_not_report_one() -> None:
    """The operator reads the actual cause, not a wrong-credential sentence that would be a lie.

    It also tells the caller **not** to retry with another `server_ref`: the model's instinct
    after a wrong-credential refusal is to pass a different one, and here that cannot help — no
    argument closes a gap in what WHM sent.
    """
    fixture, _, _, _ = gate_context(whm_endpoint(listings=[[_account_without_owner()]]))

    answer = await suspend(fixture, server_ref=OWNER)

    assert "did not report an owner" in answer["message"]
    assert "will not help" in answer["message"]
    assert ACCOUNT in answer["message"]


async def test_the_unsuspend_tool_is_guarded_by_the_same_door() -> None:
    """The guard sits in the tail both tools share, so neither can be written without it —
    asserted on the second tool rather than argued from the first."""
    fixture, api, _, _ = gate_context(
        whm_endpoint(listings=[[owned_account(suspended=1)]], mutation_path=UNSUSPENDACCT_PATH)
    )

    answer = await unsuspend(fixture, server_ref=SERVER_NAME)

    assert answer["ok"] is False
    assert answer["error_code"] == ERROR_WRONG_CREDENTIAL_FOR_OWNER
    assert fixture.action_requests.requests == []
    assert api.requests_to(UNSUSPENDACCT_PATH) == []


async def test_a_no_op_answers_before_the_ownership_compare() -> None:
    """An account already in the state the change would produce is answered, not gated, and
    that answer is true whichever credential read it: nothing is being authorised, so there is
    no card to protect. Sending the model to fetch a different `server_ref` first would buy one
    more round trip and the same sentence."""
    fixture, _, _, _ = gate_context(whm_endpoint(listings=[[owned_account(suspended=1)]]))

    answer = await suspend(fixture, server_ref=SERVER_NAME)

    assert answer["ok"] is True
    assert answer["status"] == STATUS_NO_OP
    assert fixture.action_requests.requests == []


async def test_the_refusal_is_logged_with_both_names_and_the_site() -> None:
    """ "Why is there no card" is answerable from the logs, and for this refusal that needs both
    identities and which of the two sites refused. Identifiers only, never the account
    payload."""
    fixture, _, _, _ = gate_context()

    with capture_logs() as events:
        await suspend(fixture, server_ref=SERVER_NAME)

    refusals = [event for event in events if event.get("site") == SITE_PREFLIGHT]
    assert len(refusals) == 1
    assert refusals[0]["verdict"] == Ownership.WRONG_CREDENTIAL.value
    assert refusals[0]["owner"] == OWNER
    assert refusals[0]["api_username"] == OTHER_CREDENTIAL
    assert refusals[0]["tool"] == TOOL_WHM_SUSPEND_ACCOUNT


# --------------------------------------------------------------------------------------
# What the card and the receipt name
# --------------------------------------------------------------------------------------


async def test_the_card_evidence_names_the_row_the_credential_the_host_and_the_owner() -> None:
    """The recorded four, and the fourth is the point: a privileged write whose credential is not
    recorded is not auditable, and "which machine" does not answer "which identity acted"."""
    fixture, _, _, reseller = gate_context()

    await suspend(fixture, server_ref=OWNER)

    evidence = fixture.action_requests.only.approval_context["evidence"]
    assert evidence[EVIDENCE_SERVER_NAME] == OWNER
    assert evidence[EVIDENCE_API_USERNAME] == OWNER
    assert evidence[EVIDENCE_HOST] == HOST
    assert evidence[EVIDENCE_OWNER] == OWNER
    assert evidence[EVIDENCE_SERVER_ID] == str(reseller.id)


def test_the_audit_rows_whitelist_names_the_same_four_fields_the_evidence_does() -> None:
    """The one thing that couples the two vocabularies of recorded fields.

    `core.approvals.context.AUDIT_IDENTITY_KEYS` restates the four names as literals because it
    cannot import them: `core/` is below `apps/api` and the dependency runs one way only. So a
    rename on either side is invisible to every other test — the producer test above keeps
    passing because it uses the constants, and the audit-row test in
    `test_action_request_decision_records_live.py` keeps passing because it builds its own
    evidence.
    The row would just quietly carry three fields instead of four, which is the omission the
    name-the-non-answer rule exists to forbid.

    This test is in `apps/api/tests` rather than beside the reader because it is the only place
    that may import both vocabularies at once.
    """
    assert set(AUDIT_IDENTITY_KEYS) == {
        EVIDENCE_SERVER_NAME,
        EVIDENCE_API_USERNAME,
        EVIDENCE_HOST,
        EVIDENCE_OWNER,
    }


async def test_the_audit_row_reader_takes_all_four_off_a_real_preflight() -> None:
    """The producer and the reader, end to end, on evidence the real tool wrote.

    `audit_identity_from_context` copies a field only when it is a `str`, which is true of all
    four on this path by construction — and this is what pins that. A later change that made one
    of them a `None`, an int or a nested object would narrow the audit row silently: the reader
    would drop it, the card would still render, and nothing else in the suite would notice.
    Asserted as an exact dict rather than key-by-key, so a fourth field going missing fails
    here rather than reading as three fields that were all there.
    """
    fixture, _, _, _ = gate_context()

    await suspend(fixture, server_ref=OWNER)
    context = fixture.action_requests.only.approval_context

    assert audit_identity_from_context(context) == {
        EVIDENCE_SERVER_NAME: OWNER,
        EVIDENCE_API_USERNAME: OWNER,
        EVIDENCE_HOST: HOST,
        EVIDENCE_OWNER: OWNER,
    }


async def test_the_receipt_carries_the_same_four_and_the_token_appears_in_neither() -> None:
    """The receipt's `before` half is the gate's evidence copied verbatim
    (`core.approvals.execution.build_receipt`), so the recorded four reach an audit surface
    without a second writer deciding what to record — and the API token is in none of it.
    Asserted on the serialized JSON rather than on a key list, because a token nested inside the
    account summary would satisfy a key check.
    """
    fixture, _, _, _ = gate_context()

    await suspend(fixture, server_ref=OWNER)
    evidence = fixture.action_requests.only.approval_context["evidence"]
    receipt = build_receipt(evidence=evidence, payload={"ok": True})

    before = receipt["before"]
    assert before[EVIDENCE_SERVER_NAME] == OWNER
    assert before[EVIDENCE_API_USERNAME] == OWNER
    assert before[EVIDENCE_HOST] == HOST
    assert before[EVIDENCE_OWNER] == OWNER

    serialized = json.dumps(receipt) + json.dumps(evidence)
    assert WHM_API_TOKEN not in serialized
    assert all(secret not in serialized for secret in SECRETS)
    assert "api_token" not in serialized


# --------------------------------------------------------------------------------------
# The runner: the same compare, after the decision
# --------------------------------------------------------------------------------------


async def test_the_runner_refuses_when_the_rows_credential_changed_after_the_request() -> None:
    """The owner compare's second site. A `whm_servers` row is editable between a request and its
    decision (context persisted at gate time), so the credential this change would run as need
    not be the one the operator authorised — repointing `api_username` at another reseller after
    the card was rendered would otherwise substitute the acting identity silently.

    Asserted on the mutation count: zero requests to `suspendacct` is the only evidence that
    nothing ran, and a payload assertion alone would pass against a runner that suspended the
    account and then reported a refusal.
    """
    fixture, api, _, reseller = gate_context()
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    # The operator approved a card built against the owner's credential; the row now holds
    # somebody else's.
    reseller.api_username = OTHER_CREDENTIAL

    payload = await runner(execution_request(server_id=reseller.id))

    assert payload["ok"] is False
    assert payload["error_code"] == ERROR_WRONG_CREDENTIAL_FOR_OWNER
    assert OWNER in payload["message"]
    # Both branches are worded for two sites. This one lands on an operator's receipt as well
    # as in a model's transcript, so an imperative addressed to the caller ("call this tool
    # again") would be addressed to nobody on the surface that outlives the call.
    assert "Call this tool again" not in payload["message"]
    assert "the `server_ref`" in payload["message"]
    assert api.requests_to(SUSPENDACCT_PATH) == []
    assert api.requests_to(LISTACCTS_PATH) == []


async def test_the_runner_runs_when_the_credential_still_owns_the_account() -> None:
    """The negative control on the far side of the boundary: the same evidence, an unedited row,
    and the mutation happens."""
    fixture, api, _, reseller = gate_context(whm_endpoint(listings=[[owned_account(suspended=1)]]))
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))

    payload = await runner(execution_request(server_id=reseller.id))

    assert payload["ok"] is True
    assert len(api.requests_to(SUSPENDACCT_PATH)) == 1


async def test_the_runner_refuses_evidence_that_names_no_owner() -> None:
    """The same inversion on the far side of the boundary — a non-answer named, never the benign
    value.

    Three spellings, because the runner reads JSONB rather than a normaliser's output: the key
    absent, `null`, and a blank string all round-trip differently and all mean the same thing —
    nobody said who owns this account. An approval authorises a change to *this* account by
    *that* credential, so evidence that cannot say whether the pair holds does not carry the
    authorisation forward; a request opened before this key existed refuses once, with a code,
    rather than running as an identity nobody checked.

    Asserted on the mutation count: zero requests to `suspendacct` is the only evidence that
    nothing ran.
    """
    for absent in (_NO_OWNER_KEY, None, "", "   "):
        fixture, api, _, reseller = gate_context(
            whm_endpoint(listings=[[owned_account(suspended=1)]])
        )
        runner = payload_runner(build_whm_suspend_runner(context=fixture.context))
        request = execution_request(server_id=reseller.id)
        if absent is _NO_OWNER_KEY:
            del request.evidence[EVIDENCE_OWNER]
        else:
            request.evidence[EVIDENCE_OWNER] = absent

        payload = await runner(request)

        assert payload["ok"] is False, absent
        assert payload["error_code"] == ERROR_ACCOUNT_OWNER_UNKNOWN, absent
        # This sentence lands on a receipt an operator reads, so it must not claim the change
        # was "refused rather than sent for approval" — it was approved, then refused.
        assert "did not report an owner" in payload["message"], absent
        assert "sent for approval" not in payload["message"], absent
        assert api.requests_to(SUSPENDACCT_PATH) == [], absent
        assert api.requests_to(LISTACCTS_PATH) == [], absent


async def test_the_runner_refusal_is_logged_at_the_runner_site() -> None:
    """Which of the two sites refused is a field on one event, so "why did this approved change
    not run" is one query rather than two. Identifiers only, never the payload."""
    fixture, _, _, reseller = gate_context()
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))
    reseller.api_username = OTHER_CREDENTIAL

    with capture_logs() as events:
        await runner(execution_request(server_id=reseller.id))

    refusals = [event for event in events if event.get("site") == SITE_RUNNER]
    assert len(refusals) == 1
    assert refusals[0]["verdict"] == Ownership.WRONG_CREDENTIAL.value
    assert refusals[0]["owner"] == OWNER
    assert refusals[0]["api_username"] == OTHER_CREDENTIAL


async def test_one_credential_performs_the_preflight_the_mutation_and_the_postflight() -> None:
    """The identity that wrote is the one that confirms.

    A reseller token's `listaccts` sees its own accounts (77 of 77 on the measured host)
    and its own account is the only one in question, so there is nothing a root credential could
    add to the confirming read except an answer from an identity that did not perform the write.
    Asserted on the `Authorization` headers at the socket, which is the only place "the same
    credential" is a fact rather than a claim — and the header proves a real decrypt ran.
    """
    fixture, api, _, reseller = gate_context(
        whm_endpoint(listings=[[owned_account()], [owned_account(suspended=1)]])
    )

    await suspend(fixture, server_ref=OWNER)
    runner = payload_runner(build_whm_suspend_runner(context=fixture.context))
    payload = await runner(execution_request(server_id=reseller.id))

    assert payload["ok"] is True
    assert payload["verified"] is True
    # Preflight listaccts, the mutation, the postflight listaccts — three round trips, one
    # identity, and it is the owner's.
    assert len(api.requests) == 3
    assert api.authorization_headers == [f"whm {OWNER}:{WHM_API_TOKEN}"] * 3


# --------------------------------------------------------------------------------------
# What the model is told `server_ref` means
# --------------------------------------------------------------------------------------


async def test_both_change_tools_teach_that_server_ref_is_the_accounts_owner() -> None:
    """The owner-as-`server_ref` rule, asserted on what `tools/list` publishes rather than on a
    constant.

    The model constructs the argument, and `whm_list_servers` does not advertise every
    credential NOA holds — so a description that only named "which WHM server" would leave the
    right value undiscoverable from the tool surface alone.
    """
    context = build_tool_context().context
    server = build_mcp_server(tool_context=context)
    register_mcp_tools(server, context=context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    for name in (TOOL_WHM_SUSPEND_ACCOUNT, TOOL_WHM_UNSUSPEND_ACCOUNT):
        described = tools[name].parameters["properties"]["server_ref"]["description"]
        assert "owner" in described.lower()
        assert "whm_search_accounts" in described
        assert "owner" in tools[name].description.lower()
