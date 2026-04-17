from __future__ import annotations

import hmac

import legacycrypt


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
        computed = legacycrypt.crypt(new_password, password_hash)
        return computed is not None and hmac.compare_digest(computed, password_hash)
    except (TypeError, ValueError):
        return False
