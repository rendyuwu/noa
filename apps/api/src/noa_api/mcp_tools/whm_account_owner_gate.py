"""The owner-vs-credential gate for WHM account changes (§V106, §V107, §V108).

**cPanel gates an account write on ownership, not on the token's ACL.** Measured on a live host
rather than read off a doc (§R.33): a root token with `suspend-acct` granted and `all` *absent*
is refused for an account it does not own, because an ACL-restricted root token cannot satisfy a
root check and WHM then falls through to the owner compare. So the credential that may suspend
an account is the one whose `api_username` **is** that account's `owner` — and `server_ref` on
an account CHANGE names the owner rather than the machine the listing came from.

Hoisted out of `whm_account_change` for the reason `change_target` was: two callers on opposite
sides of the approval boundary make the same refusal, and one of them is a runner. Here it is
one mechanism with one message; there it would be a line each tool and each runner has to
remember, which is the shape a guard is eventually written without (V66, and `firewall_gate`'s
argument one system over).

**The compare costs nothing.** `owner` is already on the account summary the preflight read and
`api_username` is already on the row that won resolution, so neither site spends a round trip to
ask a question it is holding the answer to.

**Ownership has to be PROVEN, and silence does not prove it**. Four verdicts, not two: the
credential is the owner; the credential is readable and is somebody else; WHM reported no owner;
NOA's row records no credential. The last two refuse — they do not proceed. That is the opposite
of T23's locked-suspension bound one field over, and the difference is evidence rather than
taste: `listaccts` demonstrably omits `is_locked` on cPanel versions that lack it, so failing
closed there would take the tool off a whole cPanel generation, while `owner` was measured
present on 451 of 451 rows across 7 owners, never blank (§R.33). So failing closed here costs
nothing in measured reality, and failing open buys exactly the harm §V106 exists to prevent: a
card that must fail, one operator decision spent on it, and one reason typed for nothing — and a
reason is born at decision time, so a burnt one cannot be recovered. Zero answers means
`unknown`, never the benign value.

The two unproven verdicts share one code and differ in one clause, because the operator's remedy
is the same act — go and look at the pair — and the sentence is what says which half was silent.
A separate code for each would be two public names for one remedy.

**A refusal carries its own remedy.** For a wrong credential that is the `server_ref` that would
have worked, because the model constructed the argument, `whm_list_servers` does not advertise
every credential NOA holds, and a refusal without a remedy can only produce the same call again.
For an unproven one it is the opposite instruction — *do not* retry with another `server_ref`,
because retrying cannot fix a field WHM did not send.

**Four fields, and the token is not one of them** (§V108). A privileged write whose credential is
not recorded is not auditable, so the evidence a card and a receipt are built from names the
row's `name`, its `api_username`, the host out of its `base_url` and the account's `owner`. What
an audit needs is which identity acted, and that is a username; `api_token` never leaves the
decrypt site. A refusal *message* names fewer, deliberately — see `refuse_unproven_ownership`.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

import structlog

from core.servers.naming import normalize_whm_identity
from noa_api.mcp_tools.results import ToolPayload, tool_failure

# The credential the change will run as, and what it is allowed to touch. The row's name is
# already on the evidence as `EVIDENCE_SERVER_NAME`; these are §V108's other three. Read by the
# card, copied verbatim into the receipt's `before` half, and — for the owner — read back by the
# runner's own compare.
EVIDENCE_API_USERNAME: Final = "api_username"
EVIDENCE_HOST: Final = "host"
EVIDENCE_OWNER: Final = "owner"

# What one of those says when its source did not answer. A word rather than an absent key or a
# `null`, because the card renders evidence generically — an omitted key reads as a field nobody
# thought to record, and `null` reads as a bug.
#
# **It no longer stands where `owner` goes.** An account whose owner is unknown is refused before
# any evidence is written, so a card always names a real one — the word for a non-answer there
# was scaffolding for the fail-open bound this gate no longer has. Two roles are left, and both
# are about the word never being mistaken for a name: `host` takes it if that key ever goes
# missing (`fetch_whm_accounts` falls back to the raw `base_url`, so nothing on the current path
# produces one), and `_comparable_identifier` refuses to compare it — a sentinel read as a
# username would answer `WRONG_CREDENTIAL` and print "retry with `server_ref` set to
# `unrecorded`", which is the one sentence here that would be a lie.
EVIDENCE_UNRECORDED: Final = "unrecorded"

# Both readable, and they differ. The remedy is a different `server_ref`.
ERROR_WRONG_CREDENTIAL_FOR_OWNER: Final = "whm_wrong_credential_for_owner"

# One of the two names was not readable, so ownership is unproven. Refused rather than opened,
# and there is nothing the caller can pass to fix it — see the module docstring.
ERROR_ACCOUNT_OWNER_UNKNOWN: Final = "whm_account_owner_unknown"

# One event for both tools, both sites and every refusing verdict, with tool, site and verdict as
# fields rather than in the name: "which credential was pointed at whose account" is one question
# and it is asked in one query. Identifiers only, never the account payload.
LOG_OWNERSHIP_REFUSED: Final = "whm_account_change_ownership_refused"
SITE_PREFLIGHT: Final = "preflight"
SITE_RUNNER: Final = "runner"

logger = structlog.get_logger(__name__)


class Ownership(Enum):
    """Whether the credential is **proven** to own the account (§V106, V86).

    An enum rather than a bool because there are two ways not to be proven and they are not the
    same fact: a credential that is somebody else's is a caller error with a remedy the caller
    can act on, while a name that was never reported is a gap in the evidence that no argument
    can close. Collapsing them would make one of the two sentences a lie, and the wrong one
    would be told to whichever case is rarer — which is the one nobody would notice.

    The values double as the log field, so a query for "why was this refused" reads the verdict
    rather than inferring it from which message was rendered.
    """

    OWNED = "owned"
    WRONG_CREDENTIAL = "wrong_credential"
    OWNER_NOT_REPORTED = "owner_not_reported"
    CREDENTIAL_NOT_RECORDED = "credential_not_recorded"

    @property
    def is_proven(self) -> bool:
        """Whether the change may go ahead on this verdict. Only one of the four."""
        return self is Ownership.OWNED


def classify_ownership(*, owner: object, api_username: object) -> Ownership:
    """Which of the four §V106 verdicts these two names produce.

    Both sides through `normalize_whm_identity`, which is the whole of §V106's normalisation and
    is shared with V109(b)'s admin-write guard: WHM echoes an owner as it was typed and an
    operator types the row's credential, so `Web08CpnPool01 ` and `web08cpnpool01` are one
    identity — and they have to be one identity to *both* guards, not just to this one, or a row
    the admin write accepted is a row this gate refuses with nothing naming the cause.

    The owner is checked first when both are missing, because that is the half a caller might
    have been able to see: `whm_search_accounts` reports `owner`, and nothing a model can reach
    reports `api_username`.
    """
    owner_name = _comparable_identifier(owner)
    credential = _comparable_identifier(api_username)
    if owner_name is None:
        return Ownership.OWNER_NOT_REPORTED
    if credential is None:
        return Ownership.CREDENTIAL_NOT_RECORDED
    return Ownership.OWNED if owner_name == credential else Ownership.WRONG_CREDENTIAL


def recorded(value: object) -> str:
    """One of §V108's four fields as a card shows it, or the word for a non-answer."""
    if isinstance(value, str) and value.strip():
        return value
    return EVIDENCE_UNRECORDED


def refuse_unproven_ownership(
    *,
    ownership: Ownership,
    tool_name: str,
    site: str,
    username: str,
    owner: object,
    api_username: object,
    server_ref: object,
    server_name: object = None,
    server_id: object = None,
    action_request_id: str | None = None,
) -> ToolPayload:
    """Log an unproven-ownership refusal and shape it, for both sites that make it (§V106).

    One function because the two sites answer the same question and have to answer it the same
    way: a preflight refusal the model reads and a runner refusal that lands on a receipt are
    the same fact about the same two names, and two spellings would let a remedy drift out of
    one of them.

    **The sentence echoes `server_ref` — what the caller passed — and never the resolved row's
    `name`.** V109(b) forces a reseller row's `name` to *be* its `api_username`, and V109(a)
    keeps those rows out of `whm_list_servers`, so printing the resolved name would disclose a
    credential username the model could not otherwise see, into a transcript that persists in
    LibreChat's MongoDB. The caller's own string discloses nothing new and is the more
    useful half anyway: it names the thing to change. The resolved name stays in the structured
    log, which is not the transcript.

    The owner *is* named — the model read it off `whm_search_accounts`, and §V106 needs it to
    find the credential. What the sentence does not do is claim the owner string is itself a
    usable `server_ref`: `resolve_whm_server_ref` matches an id, a `name` or a hostname and never
    `api_username` (`core.servers.reference`), and only reseller rows are named after their
    credential — the root rows cannot all be called `root`. For the 56 of 451 measured
    `owner=root` accounts (§R.33) the machine's own row is the answer, so the sentence states the
    rule that covers both.

    Raises on `OWNED`: a caller that reached this with a proven verdict has inverted its own
    condition, and answering a refusal would hide that behind a plausible sentence.
    """
    if ownership.is_proven:
        raise ValueError("refuse_unproven_ownership called on a proven ownership verdict")

    logger.warning(
        LOG_OWNERSHIP_REFUSED,
        tool=tool_name,
        site=site,
        verdict=ownership.value,
        username=username,
        owner=owner,
        api_username=api_username,
        server_id=server_id,
        server_name=server_name,
        action_request_id=action_request_id,
    )
    passed = f"`{server_ref}`" if isinstance(server_ref, str) and server_ref.strip() else "it"

    if ownership is Ownership.WRONG_CREDENTIAL:
        named_owner = recorded(owner)
        return tool_failure(
            ERROR_WRONG_CREDENTIAL_FOR_OWNER,
            f"`{username}` belongs to `{named_owner}`, and WHM only lets that owner's own "
            f"credential change it — the `server_ref` {passed} names a different credential. "
            f"Use the NOA WHM server whose API username is `{named_owner}`: that is the server "
            f"named `{named_owner}` if the owner has its own reseller credential, and the "
            "machine's own row — its id, name or hostname — if the owner is the machine's root "
            "user.",
        )

    cause = (
        "WHM did not report an owner for it"
        if ownership is Ownership.OWNER_NOT_REPORTED
        else f"the NOA WHM server {passed} records no API username"
    )
    # Worded for both sites: this sentence reaches a model as a tool answer at the preflight and
    # an operator as a receipt at the runner, and "refused rather than sent for approval" would
    # be false on the second one — that change *was* approved, and is being refused after.
    return tool_failure(
        ERROR_ACCOUNT_OWNER_UNKNOWN,
        f"NOA cannot confirm which credential may change `{username}`, because {cause}. The "
        "change is refused rather than run on a credential nobody checked, and another "
        "`server_ref` will not help. An administrator has to check the account's ownership "
        "in WHM.",
    )


def _comparable_identifier(value: object) -> str | None:
    """`value` as a WHM identity name, or `None` when it is not one.

    Three values answer `None`, and the third is the one worth naming: a non-string (JSONB
    round-trips whatever was written), a blank string, and `EVIDENCE_UNRECORDED`. Without that
    last case the word for a non-answer would compare as an ordinary username and produce a
    *wrong-credential* verdict — the one sentence that would be a lie about it.
    """
    if not isinstance(value, str):
        return None
    normalized = normalize_whm_identity(value)
    if not normalized or normalized == EVIDENCE_UNRECORDED:
        return None
    return normalized


__all__ = [
    "ERROR_ACCOUNT_OWNER_UNKNOWN",
    "ERROR_WRONG_CREDENTIAL_FOR_OWNER",
    "EVIDENCE_API_USERNAME",
    "EVIDENCE_HOST",
    "EVIDENCE_OWNER",
    "EVIDENCE_UNRECORDED",
    "LOG_OWNERSHIP_REFUSED",
    "SITE_PREFLIGHT",
    "SITE_RUNNER",
    "Ownership",
    "classify_ownership",
    "recorded",
    "refuse_unproven_ownership",
]
