"""Settings guards.

Every test builds `Settings` explicitly with `_env_file=None` so the developer's
own `.env` cannot change the outcome.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from core.config import (
    DEFAULT_POSTGRES_URL,
    DEV_DEFAULT_API_URL,
    DEV_DEFAULT_EMBED_BASE_URL,
    MIN_JWT_SECRET_LENGTH,
    Settings,
    get_settings,
    resolve_env_file,
)

# The two operator-facing addresses a production deployment has to state, and the field
# each one sets. Kept as a pair so the guard's coverage and the tests' coverage move together.
PROD_EMBED_BASE_URL = "https://embed.noa.internal"
PROD_API_URL = "https://noa.internal"

PROD_REQUIRED = {
    "environment": "production",
    "auth_jwt_secret": "x" * MIN_JWT_SECRET_LENGTH,
    "noa_secret_encryption_key": Fernet.generate_key().decode(),
    "ldap_server_uri": "ldaps://ldap.example.com:636",
    # A production build that leaves these on their dev defaults is refused, so every
    # production fixture below has to state them — which is the guard working, not noise.
    "noa_embed_base_url": PROD_EMBED_BASE_URL,
    "noa_api_url": PROD_API_URL,
}

REPO_ROOT = Path(__file__).resolve().parents[3]

# The Fernet key's name, written once. The field guard below asserts `Settings` calls it
# this, and the doc guard asserts `.env.example` and `README.md` say the same — so a rename
# edits this line and then has to edit both documents to get back to green.
ENCRYPTION_KEY_FIELD = "noa_secret_encryption_key"
ENCRYPTION_KEY_ENV_VAR = ENCRYPTION_KEY_FIELD.upper()

# The rejected name: the key encrypts server credentials, not the database.
LEGACY_ENCRYPTION_KEY_FIELD = "noa_db_secret_key"

# Files that carry the rejected name legitimately, because their subject is the prohibition:
# the decision record and this module.
LEGACY_NAME_ALLOWED_IN = frozenset(
    {
        "DECISIONS.md",
        "apps/api/tests/test_config.py",
    }
)


@pytest.fixture(autouse=True)
def _process_env_cannot_reach_these_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this module's docstring true, rather than nearly true.

    `_env_file=None` silences the dotenv *file* and nothing else: pydantic-settings still reads
    `os.environ`, so every "default" asserted below was really "whatever the shell happened to
    export". On a developer's machine that is usually nothing and the tests pass; in CI the pytest
    lane exports `POSTGRES_URL` pointing at its Postgres service container, and
    `test_dev_defaults_are_usable_without_any_env` compared that address against the dev default
    and failed — the one environment where the assertion was actually being tested.

    Worse than the failure is the shape of it: locally the exported DSN and the default are the
    same string, so the test passed by coincidence rather than by isolation, and would have gone
    on passing while measuring nothing.

    Cleared by field name in both spellings because the settings lookup is case-insensitive. Tests
    that want a variable set still set it: `monkeypatch` applies in call order, so a `setenv` in
    the test body lands after this.
    """
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field.lower(), raising=False)


def build(**overrides: object) -> Settings:
    """Construct settings from explicit values only — no `.env`, no process env."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def build_production(**overrides: object) -> Settings:
    return build(**{**PROD_REQUIRED, **overrides})


def tracked_files() -> list[Path]:
    """Every file git tracks, so the scan covers the repo and nothing outside it.

    `git ls-files` rather than a filesystem walk: it excludes the developer's own `.env`,
    `.venv`, and `node_modules` without an ignore list of its own to drift.
    """
    git = shutil.which("git")
    if git is None:  # pragma: no cover - git is present wherever this repo is checked out
        pytest.skip("git unavailable; cannot enumerate tracked files")

    listing = subprocess.run(  # noqa: S603  (fixed argv, no shell)
        [git, "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    return [REPO_ROOT / name for name in listing.stdout.split("\0") if name]


def read_text_or_empty(path: Path) -> str:
    """Text content, or `""` for anything unreadable as UTF-8 (images, lockfile blobs)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


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


# --- JWT secret ---


def test_jwt_secret_generated_in_dev() -> None:
    """Auto-generated in dev, and long enough to be usable."""
    settings = build(environment="development")

    assert len(settings.jwt_secret) >= MIN_JWT_SECRET_LENGTH


def test_jwt_secret_required_in_production() -> None:
    """Absent secret is a startup failure, not a generated one."""
    with pytest.raises(ValidationError, match="auth_jwt_secret is required"):
        build(
            environment="production",
            noa_secret_encryption_key=Fernet.generate_key().decode(),
            ldap_server_uri="ldaps://ldap.example.com:636",
        )


def test_short_jwt_secret_rejected_in_production() -> None:
    """≥32 chars in production."""
    with pytest.raises(ValidationError, match="at least 32 characters"):
        build_production(auth_jwt_secret="tooshort")


def test_short_jwt_secret_allowed_in_dev() -> None:
    """Dev may use a throwaway value; only production enforces the length."""
    assert build(environment="development", auth_jwt_secret="short").jwt_secret == "short"


# --- Fernet key ---


def test_encryption_key_generated_in_dev_is_valid_fernet() -> None:
    """Generated in dev, and actually round-trips."""
    key = build(environment="development").secret_encryption_key
    cipher = Fernet(key.encode())

    assert cipher.decrypt(cipher.encrypt(b"ssh-credential")) == b"ssh-credential"


def test_encryption_key_required_in_production() -> None:
    """No silent generation in production — the ciphertext would be orphaned."""
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
    """The var encrypts server credentials, not the DB. No legacy alias."""
    fields = Settings.model_fields

    assert ENCRYPTION_KEY_FIELD in fields
    assert LEGACY_ENCRYPTION_KEY_FIELD not in fields


def test_secrets_are_not_exposed_by_repr() -> None:
    """A settings dump in a log must not print secrets."""
    settings = build_production(ldap_bind_password="ldap-bind-password")
    rendered = f"{settings!r} {settings.model_dump()}"

    assert "ldap-bind-password" not in rendered
    assert settings.secret_encryption_key not in rendered
    assert "**********" in rendered


# --- Operator-facing addresses ---


@pytest.mark.parametrize(
    ("field", "env_var", "value"),
    [
        ("noa_embed_base_url", "NOA_EMBED_BASE_URL", DEV_DEFAULT_EMBED_BASE_URL),
        ("noa_embed_base_url", "NOA_EMBED_BASE_URL", f"{DEV_DEFAULT_EMBED_BASE_URL}/"),
        ("noa_api_url", "NOA_API_URL", DEV_DEFAULT_API_URL),
        ("noa_api_url", "NOA_API_URL", f"{DEV_DEFAULT_API_URL}/"),
    ],
)
def test_dev_default_address_rejected_in_production(field: str, env_var: str, value: str) -> None:
    """The laptop address is a startup failure outside development.

    The trailing-slash rows are not padding: `_normalize_base_url` runs first, so a deployment
    that pasted the `.env.example` line with a slash on the end is on the default too, and a
    guard comparing the raw string would wave it through.

    Pinned to `ValidationError` and to the env var's own name — a bare `Exception` match would
    pass on an unrelated `TypeError` and quietly stop testing the guard.
    """
    with pytest.raises(ValidationError, match=f"{env_var} is still the development default"):
        build_production(**{field: value})


def test_explicit_addresses_boot_in_production() -> None:
    """The guard refuses the *default*, not the field: a stated address boots and survives
    intact."""
    settings = build_production()

    assert settings.noa_embed_base_url == PROD_EMBED_BASE_URL
    assert settings.noa_api_url == PROD_API_URL


def test_dev_default_addresses_boot_in_development() -> None:
    """The negative control: without it, "it raised" is all the suite knows.

    A guard that refused the localhost address in *every* environment would pass the two tests
    above and break a fresh clone — the case that has to stay green is this one.
    """
    settings = build(environment="development")

    assert settings.noa_embed_base_url == DEV_DEFAULT_EMBED_BASE_URL
    assert settings.noa_api_url == DEV_DEFAULT_API_URL


def test_refused_addresses_are_the_field_defaults() -> None:
    """The guard has to refuse what an unset var actually produces, not a lookalike string.

    Without this the rejection tests are tautological: they hand the guard the constant the
    guard compares against, and both could drift away from the field default together, leaving
    a deployment that forgot the var booting clean — the exact failure the guard exists to stop.
    """
    fields = Settings.model_fields

    assert fields["noa_embed_base_url"].default == DEV_DEFAULT_EMBED_BASE_URL
    assert fields["noa_api_url"].default == DEV_DEFAULT_API_URL


def test_both_missing_addresses_are_named_in_one_message() -> None:
    """A deploy that forgot both learns both at once, not one boot at a time."""
    with pytest.raises(ValidationError) as caught:
        build(
            environment="production",
            auth_jwt_secret="x" * MIN_JWT_SECRET_LENGTH,
            noa_secret_encryption_key=Fernet.generate_key().decode(),
            ldap_server_uri="ldaps://ldap.example.com:636",
        )

    message = str(caught.value)

    assert "NOA_EMBED_BASE_URL" in message
    assert "NOA_API_URL" in message


# --- LDAP transport ---


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
    """The bypass exists for local dev only."""
    with pytest.raises(ValidationError, match="auth_dev_bypass_ldap"):
        build_production(auth_dev_bypass_ldap=True)


def test_dev_ldap_bypass_allowed_in_dev() -> None:
    assert build(environment="development", auth_dev_bypass_ldap=True).auth_dev_bypass_ldap is True


# --- Session cookie ---


def test_cookie_secure_forced_off_in_dev() -> None:
    """Local dev is plain HTTP; a Secure cookie would never be sent."""
    assert (
        build(environment="development", auth_session_cookie_secure=True).auth_session_cookie_secure
        is False
    )


def test_cookie_kwargs_shared_by_set_and_clear() -> None:
    """httpOnly, SameSite=Lax, `.noa.internal`, one definition."""
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


def test_samesite_none_without_secure_rejected_in_dev() -> None:
    """Dev forces `secure=False`, and browsers discard SameSite=None without Secure.

    `none` is exactly what someone reaches for when testing the cross-site embed
    locally, so the combination must fail loudly instead of setting a cookie the
    browser throws away and leaving the approval POST unauthenticated.
    """
    with pytest.raises(ValidationError, match="requires a Secure cookie"):
        build(environment="development", auth_session_cookie_samesite="none")


def test_samesite_none_allowed_when_secure() -> None:
    """The attribute itself is legitimate — the embed path may need it."""
    settings = build_production(auth_session_cookie_samesite="none")

    assert settings.session_cookie_kwargs()["samesite"] == "none"
    assert settings.session_cookie_kwargs()["secure"] is True


def test_samesite_none_rejected_when_secure_explicitly_disabled() -> None:
    """Production too: turning Secure off by hand cannot smuggle the combination in."""
    with pytest.raises(ValidationError, match="requires a Secure cookie"):
        build_production(auth_session_cookie_samesite="none", auth_session_cookie_secure=False)


# --- JSON-array env vars ---


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
    """Emails compare case-insensitively; normalize once, at the edge."""
    settings = build(auth_bootstrap_admin_emails='["Admin@Example.COM"]')

    assert settings.auth_bootstrap_admin_emails == ["admin@example.com"]


# --- Bounds and normalization ---


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_max_inflight_per_user", 0),  # a cap of 0 blocks all CHANGE
        ("approval_pending_ttl_seconds", 5),  # TTL must be usable
        # A sweep interval of 0 is a sweeper that never sleeps; a negative one is a
        # sweeper that never runs. Either way the background half of the sweep is held by nothing.
        ("approval_expiry_sweep_interval_seconds", 0),
        # A reap deadline under a minute would call a merely slow change abandoned,
        # and a reaped run reads as an outcome nobody observed.
        ("approval_stranded_run_reap_after_seconds", 59),
        ("approval_stranded_run_reap_interval_seconds", 0),
        # A batch of 0 is a reaper that resolves nothing, and a batch with no ceiling is
        # the unbounded pass again, spelled in an env var.
        ("approval_stranded_run_reap_batch_size", 0),
        ("approval_stranded_run_reap_batch_size", 1001),
        ("yopass_secret_expiration_seconds", 0),
        ("secret_password_length", 4),  # no trivially guessable password
        ("db_pool_size", 0),
        ("ldap_timeout_seconds", 0),
        ("auth_login_rate_limit_max_attempts", 0),  # 0 would lock everyone out
    ],
)
def test_out_of_range_values_rejected(field: str, value: int) -> None:
    """Bounds fail at startup, not as a confusing runtime behaviour."""
    with pytest.raises(ValidationError):
        build(**{field: value})


def test_approval_defaults_match_invariants() -> None:
    """Default cap of 1; pending TTL and the sweep interval that enforces it."""
    settings = build()

    assert settings.approval_max_inflight_per_user == 1
    assert settings.approval_pending_ttl_seconds == 3600
    # A resolution, not a lifetime: how long a request may still *read* PENDING after it
    # stopped being answerable. Well under the TTL, and deliberately not derived from it.
    assert settings.approval_expiry_sweep_interval_seconds == 60
    assert settings.approval_expiry_sweep_interval_seconds < settings.approval_pending_ttl_seconds


def test_reaper_defaults_are_a_lifetime_a_resolution_and_a_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """How long a run may sit STARTED, how often the reaper looks, and how much one
    pass may resolve.

    Two settings rather than one derived from the other, matching the sweep pair one invariant
    over — and the interval is well under the deadline, because a reaper that looked less often
    than its own deadline would leave a run abandoned for up to two deadlines.

    The deadline also has to be *under* the pending TTL: a stranded change spends its operator's
    in-flight-cap allowance until it is reaped, and holding that longer than a request may stay
    pending would make one crash cost more than a whole approval window.
    """
    settings = build()

    assert settings.approval_stranded_run_reap_after_seconds == 900
    assert settings.approval_stranded_run_reap_interval_seconds == 120
    assert (
        settings.approval_stranded_run_reap_interval_seconds
        < settings.approval_stranded_run_reap_after_seconds
    )
    assert settings.approval_stranded_run_reap_after_seconds < settings.approval_pending_ttl_seconds
    # The drain rate is the batch over the interval — 100 per 120s, so 50 a minute. It is
    # asserted as a rate rather than as the number alone, because the number on its own says
    # nothing: what has to beat the rate stranded rows appear at is batch ÷ interval.
    assert settings.approval_stranded_run_reap_batch_size == 100
    drained_per_minute = (
        settings.approval_stranded_run_reap_batch_size
        * 60
        / settings.approval_stranded_run_reap_interval_seconds
    )
    assert drained_per_minute == 50


def test_mcp_token_ttl_optional() -> None:
    """No TTL -> non-expiring until revoked."""
    assert build(mcp_token_ttl_seconds="").mcp_token_ttl_seconds is None
    assert build(mcp_token_ttl_seconds=3600).mcp_token_ttl_seconds == 3600


def test_base_urls_lose_trailing_slashes() -> None:
    """Keeps URL joins predictable (approval URLs, yopass POST)."""
    settings = build(
        yopass_base_url="https://yopass.example.com/",
        noa_embed_base_url="https://embed.noa.internal//",
        noa_api_url="http://localhost:8000/",
    )

    assert settings.yopass_base_url == "https://yopass.example.com"
    assert settings.noa_embed_base_url == "https://embed.noa.internal"
    assert settings.noa_api_url == "http://localhost:8000"


def test_yopass_unconfigured_by_default() -> None:
    """Absent yopass is a tool-level error, not a boot failure."""
    settings = build()

    assert settings.yopass_base_url is None
    assert settings.yopass_configured is False
    assert build(yopass_base_url="https://yopass.example.com").yopass_configured is True


# --- Env var naming and `.env` resolution ---


def test_field_names_map_to_documented_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Field name == env var name, case-insensitively."""
    monkeypatch.setenv("APPROVAL_MAX_INFLIGHT_PER_USER", "7")

    assert Settings(_env_file=None).approval_max_inflight_per_user == 7


def test_the_process_env_is_what_the_isolation_holds_back() -> None:
    """The negative control for `_process_env_cannot_reach_these_tests`.

    Reproduces the CI failure exactly — the pytest lane's own service DSN — inside a monkeypatch
    that lands after the autouse fixture. If `Settings` ever stopped reading `os.environ`, this
    goes red and the fixture above becomes ceremony that should be deleted rather than kept.

    Its own `monkeypatch` instance on purpose: sharing the fixture's would make the order this
    test is asserting about invisible.
    """
    service_dsn = "postgresql+asyncpg://noa:noa@postgres:5432/noa"
    assert service_dsn != DEFAULT_POSTGRES_URL, "this control needs a value the default is not"

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("POSTGRES_URL", service_dsn)
        assert build().postgres_url_str == service_dsn

    assert build().postgres_url_str == DEFAULT_POSTGRES_URL


def test_env_example_documents_every_required_var() -> None:
    """`.env.example` is the documented surface — a new field belongs there too."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.lstrip("# ").split("=", 1)[0].strip().lower()
        for line in example.splitlines()
        if "=" in line
    }

    undocumented = set(Settings.model_fields) - documented

    assert undocumented == set(), f"add to .env.example: {sorted(undocumented)}"


@pytest.mark.parametrize("doc_name", [".env.example", "README.md"])
def test_operator_docs_name_the_env_var_the_settings_field_defines(doc_name: str) -> None:
    """The two operator-facing files name the var the field above defines.

    `.env.example` is already bound to the field set by the test above; `README.md` is the loose
    restatement, and it is the file that carries the scope claim itself — "encrypts server
    credentials, not the database". A rename that stops here leaves that sentence pointing at a
    var nothing reads.
    """
    assert ENCRYPTION_KEY_ENV_VAR in (REPO_ROOT / doc_name).read_text(encoding="utf-8")


def test_legacy_encryption_key_name_absent_from_every_tracked_file() -> None:
    """The rejected name stays out of the files this task cannot reach.

    The rename lists Dockerfiles and `docker-compose.yml` among its targets and neither exists —
    the compose work writes them, by which time this row is closed and a `NOA_DB_SECRET_KEY`
    there would meet nothing red. Scanning every tracked file binds the property instead of
    trusting the next author to have read this row (the reason `test_pins.py` binds the pin
    prose to the SDK).
    """
    offenders = sorted(
        relative
        for path in tracked_files()
        if (relative := str(path.relative_to(REPO_ROOT))) not in LEGACY_NAME_ALLOWED_IN
        and LEGACY_ENCRYPTION_KEY_FIELD in read_text_or_empty(path).lower()
    )

    assert offenders == [], (
        f"the key encrypts server credentials — use {ENCRYPTION_KEY_ENV_VAR}: {offenders}"
    )


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
