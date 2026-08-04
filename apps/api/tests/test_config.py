"""Settings guards (T5).

Every test builds `Settings` explicitly with `_env_file=None` so the developer's
own `.env` cannot change the outcome.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from core.config import (
    DEFAULT_POSTGRES_URL,
    MIN_JWT_SECRET_LENGTH,
    Settings,
    get_settings,
    resolve_env_file,
)

PROD_REQUIRED = {
    "environment": "production",
    "auth_jwt_secret": "x" * MIN_JWT_SECRET_LENGTH,
    "noa_secret_encryption_key": Fernet.generate_key().decode(),
    "ldap_server_uri": "ldaps://ldap.example.com:636",
}


def build(**overrides: object) -> Settings:
    """Construct settings from explicit values only — no `.env`, no process env."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def build_production(**overrides: object) -> Settings:
    return build(**{**PROD_REQUIRED, **overrides})


# --- Defaults and environment classification ---


def test_dev_defaults_are_usable_without_any_env() -> None:
    """A fresh clone boots: dev generates what production demands."""
    settings = build()

    assert settings.is_development is True
    assert settings.postgres_url_str == DEFAULT_POSTGRES_URL
    assert settings.jwt_secret
    assert settings.secret_encryption_key


@pytest.mark.parametrize("environment", ["development", "dev", "test", "DEV", " Test "])
def test_development_environments(environment: str) -> None:
    assert build(environment=environment).is_development is True


@pytest.mark.parametrize("environment", ["production", "prod", "staging"])
def test_non_development_environments_are_production(environment: str) -> None:
    """Staging is production: it gets the same secret requirements."""
    settings = build_production(environment=environment)

    assert settings.is_development is False
    assert settings.is_production is True


# --- V53: JWT secret ---


def test_jwt_secret_generated_in_dev() -> None:
    """V53: auto-generated in dev, and long enough to be usable."""
    settings = build(environment="development")

    assert len(settings.jwt_secret) >= MIN_JWT_SECRET_LENGTH


def test_jwt_secret_required_in_production() -> None:
    """V53: absent secret is a startup failure, not a generated one."""
    with pytest.raises(ValidationError, match="auth_jwt_secret is required"):
        build(
            environment="production",
            noa_secret_encryption_key=Fernet.generate_key().decode(),
            ldap_server_uri="ldaps://ldap.example.com:636",
        )


def test_short_jwt_secret_rejected_in_production() -> None:
    """V53: ≥32 chars in production."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        build_production(auth_jwt_secret="tooshort")


def test_short_jwt_secret_allowed_in_dev() -> None:
    """Dev may use a throwaway value; only production enforces the length."""
    assert build(environment="development", auth_jwt_secret="short").jwt_secret == "short"


# --- V52 / C7: Fernet key ---


def test_encryption_key_generated_in_dev_is_valid_fernet() -> None:
    """V52: generated in dev, and actually round-trips (C7)."""
    key = build(environment="development").secret_encryption_key
    cipher = Fernet(key.encode())

    assert cipher.decrypt(cipher.encrypt(b"ssh-credential")) == b"ssh-credential"


def test_encryption_key_required_in_production() -> None:
    """V52: no silent generation in production — the ciphertext would be orphaned."""
    with pytest.raises(ValidationError, match="noa_secret_encryption_key is required"):
        build(
            environment="production",
            auth_jwt_secret="x" * MIN_JWT_SECRET_LENGTH,
            ldap_server_uri="ldaps://ldap.example.com:636",
        )


@pytest.mark.parametrize("bad_key", ["not-a-fernet-key", "c2hvcnQ=", "x" * 44])
def test_malformed_encryption_key_rejected(bad_key: str) -> None:
    """Fail at startup, not at the first credential decrypt."""
    with pytest.raises(ValidationError, match="Fernet key"):
        build_production(noa_secret_encryption_key=bad_key)


def test_encryption_key_name_reflects_scope() -> None:
    """V52: the var encrypts server credentials, not the DB. No legacy alias."""
    fields = Settings.model_fields

    assert "noa_secret_encryption_key" in fields
    assert "noa_db_secret_key" not in fields


def test_secrets_are_not_exposed_by_repr() -> None:
    """V8: a settings dump in a log must not print secrets."""
    settings = build_production(ldap_bind_password="ldap-bind-password")
    rendered = f"{settings!r} {settings.model_dump()}"

    assert "ldap-bind-password" not in rendered
    assert settings.secret_encryption_key not in rendered
    assert "**********" in rendered


# --- C4: LDAP transport ---


def test_insecure_ldap_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="ldaps:// is required"):
        build_production(ldap_server_uri="ldap://ldap.example.com:389")


def test_insecure_ldap_allowed_in_production_when_explicitly_waived() -> None:
    """Escape hatch stays explicit — one flag, named for what it does."""
    settings = build_production(
        ldap_server_uri="ldap://ldap.example.com:389",
        ldap_allow_insecure_transport=True,
    )

    assert settings.ldap_server_uri.startswith("ldap://")


def test_insecure_ldap_allowed_in_dev() -> None:
    assert build(environment="development", ldap_server_uri="ldap://localhost:389")


def test_dev_ldap_bypass_rejected_in_production() -> None:
    """C4: the bypass exists for local dev only."""
    with pytest.raises(ValidationError, match="auth_dev_bypass_ldap"):
        build_production(auth_dev_bypass_ldap=True)


def test_dev_ldap_bypass_allowed_in_dev() -> None:
    assert build(environment="development", auth_dev_bypass_ldap=True).auth_dev_bypass_ldap is True


# --- V6 / V40: session cookie ---


def test_cookie_secure_forced_off_in_dev() -> None:
    """Local dev is plain HTTP; a Secure cookie would never be sent."""
    assert (
        build(environment="development", auth_session_cookie_secure=True).auth_session_cookie_secure
        is False
    )


def test_cookie_kwargs_shared_by_set_and_clear() -> None:
    """V6, V40: httpOnly, SameSite=Lax, `.noa.internal`, one definition."""
    settings = build_production(auth_session_cookie_domain=".noa.internal")

    kwargs = settings.session_cookie_kwargs()

    assert kwargs == {
        "key": "noa_session",
        "domain": ".noa.internal",
        "path": "/",
        "samesite": "lax",
        "secure": True,
        "httponly": True,
    }


def test_blank_cookie_domain_becomes_none() -> None:
    """`AUTH_SESSION_COOKIE_DOMAIN=` means unset — a host-only cookie."""
    assert build(auth_session_cookie_domain="   ").auth_session_cookie_domain is None


def test_invalid_samesite_rejected() -> None:
    with pytest.raises(ValidationError, match="samesite"):
        build(auth_session_cookie_samesite="sometimes")


# --- C11: JSON-array env vars ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["https://a.example","https://b.example"]', ["https://a.example", "https://b.example"]),
        ("https://a.example,https://b.example", ["https://a.example", "https://b.example"]),
        ("  ", []),
        ("[]", []),
        # Malformed JSON falls back to comma splitting rather than dropping the value.
        ('["https://a.example"', ['["https://a.example"']),
    ],
)
def test_cors_origins_accept_json_array_or_csv(raw: str, expected: list[str]) -> None:
    assert build(api_cors_allowed_origins=raw).api_cors_allowed_origins == expected


def test_bootstrap_admin_emails_lowercased() -> None:
    """V7: emails compare case-insensitively; normalize once, at the edge."""
    settings = build(auth_bootstrap_admin_emails='["Admin@Example.COM"]')

    assert settings.auth_bootstrap_admin_emails == ["admin@example.com"]


# --- Bounds and normalization ---


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_max_inflight_per_user", 0),  # V31: a cap of 0 blocks all CHANGE
        ("approval_pending_ttl_seconds", 5),  # V32: TTL must be usable
        ("yopass_secret_expiration_seconds", 0),
        ("secret_password_length", 4),  # V49: no trivially guessable password
        ("db_pool_size", 0),
        ("ldap_timeout_seconds", 0),
        ("auth_login_rate_limit_max_attempts", 0),  # V9: 0 would lock everyone out
    ],
)
def test_out_of_range_values_rejected(field: str, value: int) -> None:
    """Bounds fail at startup, not as a confusing runtime behaviour."""
    with pytest.raises(ValidationError):
        build(**{field: value})


def test_approval_defaults_match_invariants() -> None:
    """V31 default cap of 1; V32 pending TTL present."""
    settings = build()

    assert settings.approval_max_inflight_per_user == 1
    assert settings.approval_pending_ttl_seconds == 3600


def test_mcp_token_ttl_optional() -> None:
    """V2: no TTL -> non-expiring until revoked."""
    assert build(mcp_token_ttl_seconds="").mcp_token_ttl_seconds is None
    assert build(mcp_token_ttl_seconds=3600).mcp_token_ttl_seconds == 3600


def test_base_urls_lose_trailing_slashes() -> None:
    """Keeps URL joins predictable (V26 approval URLs, V50 yopass POST)."""
    settings = build(
        yopass_base_url="https://yopass.example.com/",
        noa_embed_base_url="https://embed.noa.internal//",
        noa_api_url="http://localhost:8000/",
    )

    assert settings.yopass_base_url == "https://yopass.example.com"
    assert settings.noa_embed_base_url == "https://embed.noa.internal"
    assert settings.noa_api_url == "http://localhost:8000"


def test_yopass_unconfigured_by_default() -> None:
    """C15: absent yopass is a tool-level error, not a boot failure."""
    settings = build()

    assert settings.yopass_base_url is None
    assert settings.yopass_configured is False
    assert build(yopass_base_url="https://yopass.example.com").yopass_configured is True


# --- Env var naming and `.env` resolution ---


def test_field_names_map_to_documented_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Field name == env var name, case-insensitively."""
    monkeypatch.setenv("APPROVAL_MAX_INFLIGHT_PER_USER", "7")

    assert Settings(_env_file=None).approval_max_inflight_per_user == 7


def test_env_example_documents_every_required_var() -> None:
    """`.env.example` is the documented surface — a new field belongs there too."""
    repo_root = Path(__file__).resolve().parents[3]
    example = (repo_root / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.lstrip("# ").split("=", 1)[0].strip().lower()
        for line in example.splitlines()
        if "=" in line
    }

    undocumented = set(Settings.model_fields) - documented

    assert undocumented == set(), f"add to .env.example: {sorted(undocumented)}"


def test_resolve_env_file_prefers_repo_root(tmp_path: Path) -> None:
    """Same `.env` whether the command runs from the root or from `apps/api`."""
    (tmp_path / "AGENTS.md").write_text("marker", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("ENVIRONMENT=test\n", encoding="utf-8")
    nested = tmp_path / "apps" / "api" / "src"
    nested.mkdir(parents=True)

    assert resolve_env_file(start=nested, cwd=tmp_path) == env_file


def test_resolve_env_file_returns_none_when_absent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("marker", encoding="utf-8")
    nested = tmp_path / "apps" / "api"
    nested.mkdir(parents=True)

    assert resolve_env_file(start=nested, cwd=tmp_path) is None


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
