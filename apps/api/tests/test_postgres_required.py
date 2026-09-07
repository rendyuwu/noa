"""A skipped lane is not a passed one (§V.102).

`support/database.py::migrated_database` skips when Postgres is unreachable, and that is the right
default: 92 of this suite's 115 files need no database, and a laptop without Docker should still
run them. The cost is that `uv run pytest -q` also exits **0** when the other 23 never ran — the
files that carry every live auth, RBAC, approval-decision, audit, migration and repository
behaviour. In CI that is a green pipeline over a data layer that was never exercised.

`NOA_REQUIRE_POSTGRES` is the opt-out, and these are its negative controls. §V.87 and §V.89 both
say the same thing about a control like this: without a case proving the *forbidden* outcome is
reachable, the flag passes with its `if` deleted. So the pair is asserted in both directions, and
the truthiness rule is asserted too — a variable pinned `0` reading as "required" would turn a job
strict by writing it off, which is the opposite of what that line means.

The behaviour is tested through `unreachable_postgres` rather than by pointing `migrated_database`
at a dead server: the subject here is the skip-versus-fail decision, and a real connection attempt
would put a socket timeout between the test and its assertion (§V.90 — the gate must not be the
thing under test).
"""

from __future__ import annotations

import pytest

from support.database import (
    REQUIRE_POSTGRES_ENV_VAR,
    postgres_required,
    unreachable_postgres,
)

# Stands in for whatever asyncpg raises at connect time. The decision under test reads the
# environment, never the exception, so a real driver error would only add a dependency.
UNREACHABLE = ConnectionRefusedError("connection refused")


def test_unreachable_postgres_skips_when_the_flag_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default: a missing database is an absent environment, not a failure."""
    monkeypatch.delenv(REQUIRE_POSTGRES_ENV_VAR, raising=False)

    assert postgres_required() is False
    with pytest.raises(pytest.skip.Exception) as skipped:
        unreachable_postgres(UNREACHABLE)

    assert "Postgres unavailable" in str(skipped.value)


def test_unreachable_postgres_fails_when_the_flag_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative control for the case above: the forbidden outcome is reachable.

    Without this, the test above passes against a `unreachable_postgres` that can only ever skip
    — which is exactly the function §V.102 was written to replace.
    """
    monkeypatch.setenv(REQUIRE_POSTGRES_ENV_VAR, "1")

    assert postgres_required() is True
    with pytest.raises(pytest.fail.Exception) as failed:
        unreachable_postgres(UNREACHABLE)

    message = str(failed.value)
    assert "Postgres unavailable" in message
    # The failure names the variable that caused it. A pipeline whose service died reads this
    # line, and "Postgres unavailable" alone would send the reader looking for a broken test.
    assert REQUIRE_POSTGRES_ENV_VAR in message


@pytest.mark.parametrize("value", ["", " ", "0", "false", "FALSE", "no", "off"])
def test_negative_values_do_not_make_postgres_required(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """`NOA_REQUIRE_POSTGRES=0` means not required, not "set, therefore required"."""
    monkeypatch.setenv(REQUIRE_POSTGRES_ENV_VAR, value)

    assert postgres_required() is False
    with pytest.raises(pytest.skip.Exception):
        unreachable_postgres(UNREACHABLE)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "please"])
def test_any_other_value_makes_postgres_required(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Anything that is not a recognised "off" is a request for the strict lane.

    Deliberately not an allowlist of true-ish words: this variable exists so a pipeline can insist,
    and a typo'd `NOA_REQUIRE_POSTGRES=ture` that silently returned to skipping would hand back the
    silently-green suite the flag was added to remove.
    """
    monkeypatch.setenv(REQUIRE_POSTGRES_ENV_VAR, value)

    assert postgres_required() is True
    with pytest.raises(pytest.fail.Exception):
        unreachable_postgres(UNREACHABLE)
