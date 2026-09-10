"""The crypt-verify guard: three answers, never two (§T.69 — V62, V87).

This is the file §T.69 exists for. `noa-old`'s `cloudinit_dump_matches_password` answered a
`bool`, and `False` meant three unrelated things: the password does not match, there was no hash
to compare, and **libcrypt would not load**. The third is not a verdict — a host that cannot
compare has refuted nothing — and reporting it as a mismatch turns a change that succeeded into
one an operator is told to repeat.

So every test here is about keeping *unavailable* separate from *mismatch*, and the pair that
matters most is:

- `test_a_host_without_libcrypt_reports_unavailable` — the guard;
- `test_verification_still_separates_a_wrong_password` — the negative control. Without it,
  a `verify_cloudinit_password` that answered `UNAVAILABLE` unconditionally would pass every
  other assertion in this file. A verdict function that stopped verifying would be silent.

**The real `crypt(3)` runs here.** `_load_crypt_lib` is the default loader and the fixtures are
hashed with it, so the match and mismatch cases exercise the same library the runner will. Only
the *absent* case is injected, and it is injected as a loader argument rather than as a patched
module global — the cache in `_load_crypt_lib` is deliberately sticky, and a test that cleared it
would leave the next one to discover that.
"""

from __future__ import annotations

from ctypes import CDLL

import pytest

from core.integrations.proxmox.cloudinit import (
    CAUSE_CRYPT_LIBRARY_UNAVAILABLE,
    CAUSE_CRYPT_REFUSED_HASH,
    CAUSE_PASSWORD_HASH_ABSENT,
    CryptVerdict,
    _load_crypt_lib,
    cloudinit_carries_password,
    crypt_password,
    extract_cloudinit_password_hash,
    verify_cloudinit_password,
)

PASSWORD = "correct-horse-battery-staple"
OTHER_PASSWORD = "Tr0ub4dor&3"
SALT = "$6$noatestsalt$"


def no_crypt_library() -> CDLL | None:
    """A host with no libcrypt — §T.69's subject."""
    return None


def dump_for(password: str) -> str:
    """A rendered user-data document carrying the real crypt hash of `password`."""
    computed = crypt_password(password, SALT)
    assert computed is not None, "this suite needs a working libcrypt to be meaningful"
    return f"#cloud-config\nuser: ubuntu\npassword: {computed}\nchpasswd:\n  expire: False\n"


# --- The guard (§T.69, V62) ---


def test_a_host_without_libcrypt_reports_unavailable() -> None:
    """§T.69, stated as directly as it can be.

    The dump carries the *right* password, so a function that fell back to comparing anything at
    all would answer `MATCH` and fail here. The only correct answer is that no comparison
    happened.
    """
    verification = verify_cloudinit_password(dump_for(PASSWORD), PASSWORD, loader=no_crypt_library)

    assert verification.verdict is CryptVerdict.UNAVAILABLE
    assert verification.cause == CAUSE_CRYPT_LIBRARY_UNAVAILABLE
    assert verification.verified is False
    assert verification.answered is False


def test_a_host_without_libcrypt_does_not_report_a_mismatch() -> None:
    """The half of §T.69 that is about what the answer must *not* be.

    `noa-old` answered `False` here, and a `False` is read as "this password is not on the VM".
    Asserted separately from the test above because a future refactor could keep `verified` false
    while collapsing the verdict back into `MISMATCH`, which is exactly the regression.
    """
    verification = verify_cloudinit_password(
        dump_for(OTHER_PASSWORD), PASSWORD, loader=no_crypt_library
    )

    assert verification.verdict is not CryptVerdict.MISMATCH
    assert verification.verdict is CryptVerdict.UNAVAILABLE


def test_the_library_is_probed_before_the_hash_is_read() -> None:
    """A host that cannot compare says so, rather than blaming a document it never read.

    A dump with no `password:` line *and* no libcrypt has two possible causes, and the one that
    is actionable is the deployment's. Ordering the checks the other way would send an
    administrator to look at the VM.
    """
    verification = verify_cloudinit_password(
        "#cloud-config\nuser: ubuntu\n", PASSWORD, loader=no_crypt_library
    )

    assert verification.cause == CAUSE_CRYPT_LIBRARY_UNAVAILABLE


# --- The negative control: the compare still separates ---


def test_verification_confirms_the_password_that_is_actually_set() -> None:
    """The real library, the real hash, the real compare."""
    verification = verify_cloudinit_password(dump_for(PASSWORD), PASSWORD)

    assert verification.verdict is CryptVerdict.MATCH
    assert verification.verified is True
    assert verification.answered is True
    assert verification.cause is None


def test_verification_still_separates_a_wrong_password() -> None:
    """**The control this file would be worthless without**.

    Every other assertion here is satisfied by a function that answers `UNAVAILABLE` for
    everything. This is the one that is not: a document carrying somebody else's password has to
    come back `MISMATCH`, measured, with `answered` true — because "we compared and it differed"
    and "we could not compare" are the two states §T.69 exists to keep apart, and a guard that
    swallowed both would look identical to a correct one from the outside.
    """
    verification = verify_cloudinit_password(dump_for(OTHER_PASSWORD), PASSWORD)

    assert verification.verdict is CryptVerdict.MISMATCH
    assert verification.verified is False
    # It *answered*: this is a measurement, not an absence.
    assert verification.answered is True


# --- The other two unavailable causes ---


def test_a_dump_with_no_password_line_is_unavailable_not_a_mismatch() -> None:
    """Nothing to compare against is not evidence of a wrong password (V62's rule again).

    It is the state a VM is in between the config write and the drive regeneration, so reading it
    as a mismatch would fail a reset that had simply not finished.
    """
    verification = verify_cloudinit_password("#cloud-config\nuser: ubuntu\n", PASSWORD)

    assert verification.verdict is CryptVerdict.UNAVAILABLE
    assert verification.cause == CAUSE_PASSWORD_HASH_ABSENT


@pytest.mark.parametrize(
    "dump",
    [
        pytest.param(None, id="not-a-string"),
        pytest.param("", id="empty"),
        pytest.param("#cloud-config\npassword:\n", id="empty-value"),
        pytest.param({"password": "x"}, id="a-mapping"),
    ],
)
def test_an_unreadable_dump_is_unavailable(dump: object) -> None:
    """Every shape that is not a document with a password line lands in one place.

    Parametrised rather than written four times because the claim is about the *set*: any of
    these reaching the mismatch branch would be a change reported as failed on the strength of a
    payload nobody could read.
    """
    verification = verify_cloudinit_password(dump, PASSWORD)

    assert verification.verdict is CryptVerdict.UNAVAILABLE
    assert verification.cause == CAUSE_PASSWORD_HASH_ABSENT


def test_a_salt_the_library_refuses_is_unavailable_not_a_mismatch() -> None:
    """libxcrypt answers `*0` for a salt it cannot use, and that is a refusal to compute.

    Its own cause, because the remedy is different again: the hash on the VM is in a scheme this
    host does not support, which is neither a deployment problem nor a wrong password.

    `$99$` rather than a generic-looking string, and that distinction is **measured**: libxcrypt
    reads two leading characters from the DES alphabet as a legacy DES salt, so `not-a-crypt-hash`
    is *computed* rather than refused (see the test below). Only an unknown `$scheme$` reaches
    this branch.
    """
    verification = verify_cloudinit_password("#cloud-config\npassword: $99$abc$def\n", PASSWORD)

    assert verification.verdict is CryptVerdict.UNAVAILABLE
    assert verification.cause == CAUSE_CRYPT_REFUSED_HASH


def test_crypt_password_answers_none_rather_than_a_refusal_marker() -> None:
    """The layer below: a `*`-prefixed answer never escapes as though it were a hash.

    Asserted directly because it is what keeps the caller's branch honest — a `*0` compared
    against a stored hash would be a clean, confident, wrong `MISMATCH`.
    """
    assert crypt_password(PASSWORD, "$99$abc$") is None
    assert crypt_password(PASSWORD, SALT) is not None


def test_a_legacy_des_salt_is_computed_rather_than_refused() -> None:
    """The boundary of the branch above, and the reason it is drawn where it is.

    Two characters from the DES alphabet are a *valid* salt to libxcrypt, so a cloud-init drive
    carrying a short legacy hash gets a real comparison — and one against NOA's password comes
    back `MISMATCH`, correctly, because that stored value genuinely is not this password.
    Recorded as a test because it is the difference between "unsupported scheme" and "old
    scheme", and only the first is an absence of an answer.

    Both directions, so this says "it was computed" rather than only "it did not match" — a
    refusal branch that swallowed DES would produce `UNAVAILABLE` for the second case and could
    not produce `MATCH` for the first.
    """
    legacy_salt = "no"
    ours = crypt_password(PASSWORD, legacy_salt)
    theirs = crypt_password(OTHER_PASSWORD, legacy_salt)
    assert ours is not None and theirs is not None and ours != theirs

    assert (
        verify_cloudinit_password(f"#cloud-config\npassword: {ours}\n", PASSWORD).verdict
        is CryptVerdict.MATCH
    )

    verification = verify_cloudinit_password(f"#cloud-config\npassword: {theirs}\n", PASSWORD)
    assert verification.verdict is CryptVerdict.MISMATCH
    assert verification.answered is True


# --- The loader itself ---


def test_the_real_loader_finds_a_crypt_library_on_this_host() -> None:
    """Not a tautology: it is what makes the match and mismatch tests above meaningful.

    If this ever fails, the two tests that assert `MATCH` and `MISMATCH` are asserting nothing,
    and the suite should say which of those two facts changed rather than reporting a mismatch
    somewhere confusing.
    """
    assert _load_crypt_lib() is not None


def test_the_loader_caches_its_answer() -> None:
    """Called once per approved change, so the `find_library` walk is paid once per process."""
    assert _load_crypt_lib() is _load_crypt_lib()


# --- Reading Proxmox's own payload shapes ---


def test_extracting_the_hash_takes_the_first_password_line() -> None:
    """`noa-old`'s behaviour, kept, and the indentation is why it is asserted.

    cloud-init user-data is YAML and the field can be nested, so the parser strips leading
    whitespace — a `startswith` on the raw line would miss every indented document.
    """
    dump = "#cloud-config\nusers:\n  - name: ubuntu\n    password: $6$abc$def\n"

    assert extract_cloudinit_password_hash(dump) == "$6$abc$def"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"cipassword": "**********"}, True, id="mapping-set"),
        pytest.param({"cipassword": "   "}, False, id="mapping-blank"),
        pytest.param({"ciuser": "ubuntu"}, False, id="mapping-absent"),
        pytest.param([{"key": "cipassword", "value": "**"}], True, id="pairs-set"),
        pytest.param([{"key": "cipassword", "value": ""}], False, id="pairs-blank"),
        pytest.param([{"key": "ciuser", "value": "ubuntu"}], False, id="pairs-other-key"),
        pytest.param("cipassword", False, id="a-string-is-not-a-payload"),
        pytest.param(None, False, id="null"),
    ],
)
def test_cloudinit_password_presence_reads_both_payload_shapes(
    data: object, expected: bool
) -> None:
    """Proxmox answers this endpoint as a mapping on some versions and as pairs on others.

    Both are read, and the string case is here because `str` is a `Sequence`: a walk that did not
    exclude it would iterate the characters of `"cipassword"` and find nothing, which is the right
    answer reached by an accident that would stop holding.
    """
    assert cloudinit_carries_password(data) is expected
