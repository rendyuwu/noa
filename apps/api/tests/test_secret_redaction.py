"""One-way redaction of anything persisted or logged (T73 — V8, V45).

Ported with `core/secrets/redaction.py` from `noa-old` branch `MCP` (C13). The tests are
not ported: the source had none for this module, which is part of why T15 left it out until
something called it (V69 — a control lands with a test against the real mechanism).

The claim under test is narrow and worth stating: **redaction is by key name, one-way, and
recursive.** Not by value shape, not reversible, and not top-level only.
"""

from __future__ import annotations

import pytest

from core.secrets.redaction import (
    REDACTED,
    SENSITIVE_KEYS,
    is_sensitive_key,
    redact_sensitive_data,
    sensitive_key_paths,
)

# Argument names the CHANGE tools of T25-T29 carry, plus the credential columns of schema
# v1. Named individually so a failure says which one stopped being covered.
CREDENTIAL_KEYS = [
    "api_token",
    "api_token_secret",
    "password",
    "ssh_password",
    "ssh_private_key",
    "ssh_private_key_passphrase",
    "cipassword",
]

SECRET = "hunter2-correct-horse"


@pytest.mark.parametrize("key", CREDENTIAL_KEYS)
def test_every_credential_key_is_replaced(key: str) -> None:
    """V8: a value under one of these names never reaches storage or a log."""
    assert redact_sensitive_data({key: SECRET}) == {key: REDACTED}


def test_matching_ignores_case_and_surrounding_space() -> None:
    """A schema that spells a field differently still gets redacted.

    The alternative is a redactor that works until someone writes `SSH_Password`, which is
    the kind of gap that only shows up in an audit table after the fact.
    """
    assert is_sensitive_key(" SSH_Password ")
    assert redact_sensitive_data({"SSH_Password": SECRET}) == {"SSH_Password": REDACTED}


def test_ordinary_fields_survive_untouched() -> None:
    """The audit trail has to still say what was asked for (V45, T55).

    A redactor that flattened everything would satisfy V8 and make the audit surface
    useless, so the passthrough is as much the contract as the replacement is.
    """
    args = {"server_ref": "whm-1", "target": "1.2.3.4", "duration_minutes": 120}

    assert redact_sensitive_data(args) == args


def test_redaction_reaches_nested_and_listed_values() -> None:
    """A credential inside a structured argument is still a credential.

    A top-level scan would pass `{"server": {"ssh_password": ...}}` straight through, which
    is exactly the shape a resolved server row takes.
    """
    payload = {
        "server": {"name": "alpha", "ssh_password": SECRET},
        "servers": [{"name": "beta", "api_token": SECRET}],
    }

    assert redact_sensitive_data(payload) == {
        "server": {"name": "alpha", "ssh_password": REDACTED},
        "servers": [{"name": "beta", "api_token": REDACTED}],
    }


def test_a_secret_string_is_not_walked_character_by_character() -> None:
    """`str` is a `Sequence`; the sequence branch has to exclude it.

    Without that exclusion a password would come back as a list of one-character strings —
    unredacted, and unreadable in the audit view.
    """
    assert redact_sensitive_data({"note": SECRET}) == {"note": SECRET}
    assert redact_sensitive_data([SECRET]) == [SECRET]


def test_redaction_is_one_way() -> None:
    """The departure from `noa-old`, asserted rather than left in a comment.

    That repo *encrypted* sensitive audit args so the admin UI could decrypt them back
    (`storage/postgres/action_tool_runs.py::_encrypt_sensitive_args`). §V45/V47 say
    redacted, and a recoverable audit table turns one Fernet key into every credential ever
    passed to a tool. Nothing here carries the plaintext forward, in any encoding.
    """
    redacted = redact_sensitive_data({"password": SECRET})

    assert SECRET not in repr(redacted)


def test_the_locator_walks_exactly_where_the_redactor_replaces() -> None:
    """`sensitive_key_paths` answers "where" for a caller that must refuse rather than rewrite.

    T38's executor will not run a change whose arguments came back `[redacted]`, and the two
    walks disagreeing is the failure that matters: a shallower locator passes a nested
    credential through to a runner as the literal placeholder (V66).
    """
    payload = {
        "server": {"name": "alpha", "ssh_password": SECRET},
        "servers": [{"name": "beta", "api_token": SECRET}],
        "duration_minutes": 120,
    }

    assert sorted(sensitive_key_paths(payload)) == [
        "server.ssh_password",
        "servers[0].api_token",
    ]


def test_the_locator_finds_nothing_in_an_unredacted_payload() -> None:
    """The negative control (V87): an ordinary argument set is not a refusal, and a value that
    merely *looks* like the placeholder is not one either — the rule is key names."""
    assert sensitive_key_paths({"server_ref": "whm-1", "target": REDACTED}) == []
    assert sensitive_key_paths({}) == []


def test_the_key_list_is_not_empty_and_covers_the_schema_v1_columns() -> None:
    """A redactor whose key set drifted empty would pass every other test here."""
    assert {"api_token", "api_token_secret", "ssh_password"} <= SENSITIVE_KEYS
