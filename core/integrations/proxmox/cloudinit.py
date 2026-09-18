"""What cloud-init says about a VM's password, and whether NOA may claim it took.

Ported, never imported, from `noa-old` branch `MCP` (`proxmox/tools/_cloudinit_passwords.py`)
with one deliberate correction, which is the whole of the three-value crypt verdict below.

**The correction.** `noa-old`'s `cloudinit_dump_matches_password` answered a `bool`, and it
answered `False` in three unrelated situations: the password genuinely does not match, the
rendered user-data carries no `password:` line at all, and **libcrypt could not be loaded**. The
third is not a verdict. A box where `_load_crypt_lib()` answers `None` cannot compare anything,
and reporting that as "does not match" makes a *measurement* out of an *absence* — a change that
in fact succeeded is reported as one that failed, and an operator is sent to reset a password
that is already live. The verdict names it: **verification-unavailable ≠ verified**, and it is
equally ≠ refuted.

So verification answers three states rather than two (`CryptVerdict`), and every unavailable one
carries the **cause** that produced it. `verified` is derived from the verdict rather than stored
beside it, so the two cannot disagree.

**Why ctypes rather than the stdlib `crypt` module.** `noa-old`'s call, kept. The stdlib module
is deprecated and removed in 3.13, and the Python window already pins `<3.13` for `pgpy`;
binding this to a second removal would mean two reasons to be stuck on one interpreter instead
of one.

**The lock is load-bearing, not caution.** POSIX `crypt(3)` returns a pointer into a static
buffer, so two concurrent calls in one process can read each other's result. NOA runs approved
changes as concurrent asyncio tasks, and while an event loop serialises the *Python* here,
`CDLL` releases the GIL for the call — the lock is what makes that safe.

`crypt(3)` on a SHA-512 hash is a few milliseconds of CPU and is called once per verification
attempt, so it runs inline rather than on a thread. If a future caller verifies in a loop, that
is the moment to reconsider, not now.

**A `*`-prefixed answer is unavailable, not a mismatch.** libxcrypt reports a salt it cannot use
by returning `*0` (or `*`), never by returning a hash that fails to compare. Reading that as
"the password does not match" would be the same lie one layer down: a refusal to compute is not
a computed answer.

That branch is narrower than it first looks, and the bound is **measured** rather than assumed
(`test_proxmox_cloudinit_crypt.py`): only an unknown `$scheme$` is refused. Two leading
characters from the DES alphabet are a *valid* legacy salt, so a VM carrying an old short hash
gets a real comparison and a real `MISMATCH` — which is the right answer, because that hash is
genuinely not this password's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from ctypes import CDLL, c_char_p
from ctypes.util import find_library
from dataclasses import dataclass
from enum import StrEnum
from hmac import compare_digest
from threading import Lock
from typing import Any, Final

# The candidates `noa-old` tried, in its order. `find_library` first because it consults
# the linker cache and answers the platform's real SONAME; the literals cover a container whose
# `ldconfig` cache or `binutils` is absent, which is the common way `find_library` answers `None`
# on an otherwise healthy box.
_CRYPT_LIBRARY_CANDIDATES: Final[tuple[str, ...]] = (
    "libcrypt.so.1",
    "libcrypt.so",
    "libxcrypt.so.2",
    "libxcrypt.so.1",
)

# `crypt(3)` reads and writes one static buffer per process — see the module docstring.
_CRYPT_LOCK = Lock()

# Distinct from `None`, which is the *answer* "there is no crypt library here". A single
# sentinel is what makes the negative result cached too: a box without libcrypt should pay the
# `find_library` walk once, not once per approved change.
_NOT_LOADED: Final[object] = object()
_CRYPT_LIB: CDLL | object | None = _NOT_LOADED

# The line `cloudinit/dump?type=user` renders the crypt hash on.
_PASSWORD_FIELD: Final = "password:"  # noqa: S105 — a YAML key, not a credential

# Proxmox's own key for the cloud-init password, in both payload shapes it answers with.
_CIPASSWORD_KEY: Final = "cipassword"


class CryptVerdict(StrEnum):
    """What a crypt-compare established, including "nothing".

    Three values rather than a `bool`, because the third is the one the verdict exists for: a
    box that cannot compare has not refuted anything, and collapsing it into `MISMATCH` reports
    a change that worked as one that failed.
    """

    MATCH = "match"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


# The causes an `UNAVAILABLE` verdict can carry. Stable strings: they reach an operator through
# the receipt and a model through `result_summary`, and each names a different thing to do.
#
# The host has no libcrypt — the deployment's problem, and the one the verdict names by hand.
CAUSE_CRYPT_LIBRARY_UNAVAILABLE: Final = "crypt_library_unavailable"
# The rendered user-data has no `password:` line, so there is nothing to compare against. Either
# the drive has not been regenerated yet or this VM does not carry a cloud-init password at all.
CAUSE_PASSWORD_HASH_ABSENT: Final = "cloudinit_password_hash_absent"  # noqa: S105 — a code
# libcrypt loaded and then refused the stored salt (`*0`). A malformed or unsupported hash
# format on the VM, not a wrong password.
CAUSE_CRYPT_REFUSED_HASH: Final = "crypt_refused_stored_hash"


@dataclass(frozen=True)
class CloudInitPasswordVerification:
    """One crypt-compare, or the named reason there was not one.

    `verified` and `answered` are derived rather than stored, so no caller can be handed a value
    whose boolean disagrees with its verdict — the two-writers-one-run defect is the reminder
    for, one subject over.
    """

    verdict: CryptVerdict
    cause: str | None = None

    @property
    def verified(self) -> bool:
        """Did NOA *measure* that this password is the one on the VM?"""
        return self.verdict is CryptVerdict.MATCH

    @property
    def answered(self) -> bool:
        """Was a comparison performed at all? `False` is `verification_unavailable`."""
        return self.verdict is not CryptVerdict.UNAVAILABLE


def _load_crypt_lib() -> CDLL | None:
    """The host's `crypt(3)`, or `None` when there is not one.

    Cached both ways, for the reason `_NOT_LOADED` exists. `None` is the answer that becomes
    `verification_unavailable`; it is never an exception, because a missing library is a fact
    about the deployment that a change has to *report*, not one it should crash on.
    """
    global _CRYPT_LIB
    if _CRYPT_LIB is not _NOT_LOADED:
        return _CRYPT_LIB  # type: ignore[return-value]

    for candidate in (find_library("crypt"), find_library("xcrypt"), *_CRYPT_LIBRARY_CANDIDATES):
        if not candidate:
            continue
        try:
            library = CDLL(candidate)
        except OSError:
            continue
        crypt_fn = getattr(library, "crypt", None)
        if crypt_fn is None:
            # A library that loads but exports no `crypt` is not this library.
            continue
        crypt_fn.restype = c_char_p
        crypt_fn.argtypes = [c_char_p, c_char_p]
        _CRYPT_LIB = library
        return library

    _CRYPT_LIB = None
    return None


CryptLibraryLoader = Callable[[], CDLL | None]


def crypt_password(
    password: str, salt: str, *, loader: CryptLibraryLoader = _load_crypt_lib
) -> str | None:
    """`crypt(password, salt)`, or `None` when the host cannot compute one.

    `None` covers both "no library" and "the library refused the salt". The caller distinguishes
    them, because they are different causes with different remedies — this function's job is to
    keep either from looking like a hash.

    `loader` is the seam a test uses to present a host with no libcrypt. It is a parameter
    rather than a patched module global so the absent-library case is reachable without leaving
    the process in a state the next test inherits.
    """
    library = loader()
    if library is None:
        return None

    crypt_fn = library.crypt
    with _CRYPT_LOCK:
        result = crypt_fn(password.encode("utf-8"), salt.encode("utf-8"))
    if result is None:
        return None
    computed = result.decode("utf-8", errors="replace")
    # libxcrypt's refusal marker. See the module docstring: not a mismatch.
    return None if computed.startswith("*") else computed


def extract_cloudinit_password_hash(dump_value: object) -> str | None:
    """The crypt hash off the rendered user-data document, or `None`.

    The first `password:` line wins, matching `noa-old`. cloud-init user-data is YAML and a
    second one would be a document that does not round-trip anyway; picking the first is what
    the source did and there is nothing here to improve by guessing differently.

    Anything that is not a string, and any `password:` line with an empty value, answers `None` —
    which the caller reports as `cloudinit_password_hash_absent` rather than as a mismatch.
    """
    if not isinstance(dump_value, str):
        return None

    for line in dump_value.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith(_PASSWORD_FIELD):
            continue
        value = stripped[len(_PASSWORD_FIELD) :].strip()
        return value or None
    return None


def verify_cloudinit_password(
    dump_value: object,
    password: str,
    *,
    loader: CryptLibraryLoader = _load_crypt_lib,
) -> CloudInitPasswordVerification:
    """Does the VM's rendered cloud-init carry *this* password?

    Three outcomes and never two. The order matters: the library is probed **before** the hash is
    read, so a host that cannot compare says so rather than blaming a VM whose dump it was never
    going to be able to check.

    A `TypeError`/`ValueError` out of the encode-and-call path is `crypt_refused_stored_hash`
    rather than a raise, for the reason the whole module returns instead of raising: this runs
    inside an approved change, and the caller has a receipt to write either way — raw exceptions
    are sanitised one layer up.
    """
    if loader() is None:
        return CloudInitPasswordVerification(
            CryptVerdict.UNAVAILABLE, CAUSE_CRYPT_LIBRARY_UNAVAILABLE
        )

    password_hash = extract_cloudinit_password_hash(dump_value)
    if password_hash is None:
        return CloudInitPasswordVerification(CryptVerdict.UNAVAILABLE, CAUSE_PASSWORD_HASH_ABSENT)

    try:
        computed = crypt_password(password, password_hash, loader=loader)
    except (TypeError, ValueError):
        computed = None
    if computed is None:
        return CloudInitPasswordVerification(CryptVerdict.UNAVAILABLE, CAUSE_CRYPT_REFUSED_HASH)

    if compare_digest(computed, password_hash):
        return CloudInitPasswordVerification(CryptVerdict.MATCH)
    return CloudInitPasswordVerification(CryptVerdict.MISMATCH)


def cloudinit_carries_password(cloudinit_data: object) -> bool:
    """Does `GET .../cloudinit` report a `cipassword` at all?

    Ported from `noa-old`'s `_cloudinit_confirms_password_reset`. Proxmox answers this endpoint
    in two shapes — a flat mapping, or a list of `{"key": ..., "value": ...}` pairs — and both
    appear depending on version, so both are read.

    A weaker question than `verify_cloudinit_password` and used for a weaker claim: it says a
    password is *set*, never that it is the one NOA just generated. It is the cheap gate in front
    of the crypt compare, never a substitute for it — the compare is what "verify" means here.
    """
    if isinstance(cloudinit_data, Mapping):
        return _non_empty_text(cloudinit_data.get(_CIPASSWORD_KEY)) is not None

    if not isinstance(cloudinit_data, Sequence) or isinstance(cloudinit_data, str | bytes):
        return False

    for entry in cloudinit_data:
        if not isinstance(entry, Mapping):
            continue
        if _non_empty_text(entry.get("key")) != _CIPASSWORD_KEY:
            continue
        if _non_empty_text(entry.get("value")) is not None:
            return True
    return False


def _non_empty_text(value: Any) -> str | None:
    """A stripped non-empty string, or `None`. Non-strings are `None`, never stringified.

    `core.integrations.proxmox.client._normalized_text`'s rule, restated for this module's own
    payload reading rather than imported across a private name.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
