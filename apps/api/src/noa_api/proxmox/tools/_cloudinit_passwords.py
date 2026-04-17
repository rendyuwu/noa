from __future__ import annotations

import hmac
from ctypes import CDLL, c_char_p
from ctypes.util import find_library
from threading import Lock


_CRYPT_LOCK = Lock()
_CRYPT_LIB = None


def _load_crypt_lib() -> CDLL | None:
    global _CRYPT_LIB
    if _CRYPT_LIB is not None:
        return _CRYPT_LIB

    for candidate in (
        find_library("crypt"),
        find_library("xcrypt"),
        "libcrypt.so.1",
        "libcrypt.so",
        "libxcrypt.so.2",
        "libxcrypt.so.1",
    ):
        if not candidate:
            continue
        try:
            library = CDLL(candidate)
        except OSError:
            continue
        crypt_fn = getattr(library, "crypt", None)
        if crypt_fn is None:
            continue
        crypt_fn.restype = c_char_p
        crypt_fn.argtypes = [c_char_p, c_char_p]
        _CRYPT_LIB = library
        return library

    _CRYPT_LIB = None
    return None


def _crypt_password(password: str, salt: str) -> str | None:
    library = _load_crypt_lib()
    if library is None:
        return None

    crypt_fn = getattr(library, "crypt")
    with _CRYPT_LOCK:
        result = crypt_fn(password.encode("utf-8"), salt.encode("utf-8"))
    if result is None:
        return None
    return result.decode("utf-8")


def sanitize_cloudinit_dump_user(dump_value: object) -> tuple[str | None, bool]:
    if not isinstance(dump_value, str):
        return None, False

    dump_text = dump_value.strip()
    if not dump_text:
        return None, False

    sanitized_lines: list[str] = []
    found_password = False
    for line in dump_value.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("password:"):
            sanitized_lines.append(line)
            continue

        value = stripped[len("password:") :].strip()
        if not value:
            return None, False

        leading = line[: len(line) - len(stripped)]
        sanitized_lines.append(f"{leading}password: [REDACTED]")
        found_password = True

    if not found_password:
        return None, False

    sanitized = "\n".join(sanitized_lines)
    if dump_value.endswith("\n"):
        sanitized += "\n"
    return sanitized, True


def extract_cloudinit_password_hash(dump_value: object) -> str | None:
    if not isinstance(dump_value, str):
        return None

    for line in dump_value.splitlines():
        stripped = line.lstrip()
        if not stripped.startswith("password:"):
            continue
        value = stripped[len("password:") :].strip()
        return value or None
    return None


def cloudinit_dump_matches_password(dump_value: object, new_password: str) -> bool:
    password_hash = extract_cloudinit_password_hash(dump_value)
    if password_hash is None or password_hash == "[REDACTED]":
        return False
    try:
        computed = _crypt_password(new_password, password_hash)
        return computed is not None and hmac.compare_digest(computed, password_hash)
    except (TypeError, ValueError):
        return False
