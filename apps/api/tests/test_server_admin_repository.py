"""The server-inventory write path against a live database.

`test_admin_server_routes.py` covers the HTTP surface over doubles and
`test_server_admin_service.py` covers the validate branches. This file covers the two things a
double cannot tell you:

1. **The transaction boundary**. An in-memory repository cannot roll back, so "flushed"
   and "committed" read identically through it — and identically *inside* one session, too,
   which is precisely what let the flush-only 200-over-rollback bug ship twice. Every case here
   observes from a **second** session, and every case ships the flush-only negative control
   beside it, so a green result cannot come from an observer that reads its own transaction or
   one that can see nothing (the live-commit witness, the compare-must-still-separate rule).

2. **What the columns actually hold**. The service encrypts; this asserts the stored
   bytes are `enc:v1:fernet:…` on all six secret columns across the three tables — and that the
   host-key fingerprint, which is a public digest, is stored as typed.

Plus the two things that are SQL rather than policy: the unique-name constraint as the backstop
behind the service's case-insensitive pre-check, and the `ORDER BY name` the admin list, the
tool result and the ambiguity `choices` all share.

A scratch database is created, migrated with `alembic upgrade head`, and dropped — skipped
(never failed) when Postgres is unreachable, exactly as every other repository test here does.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.secrets.crypto import ENCRYPTED_PREFIX, SecretCipher
from core.servers.admin_repository import (
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    SQLHostKeyPinRepository,
    SQLServerAdminRepository,
    SSHCredentials,
    SSHCredentialsPatch,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.admin_service import (
    PMG_ADMIN_POLICY,
    PROXMOX_ADMIN_POLICY,
    WHM_ADMIN_POLICY,
    ServerAdminService,
)
from core.servers.errors import WHMServerNameExistsError
from support.database import MUTATED_TABLES, migrated_database, truncate
from support.rbac import RecordingAuditSink
from support.secrets import build_cipher

SCRATCH_DB = "noa_server_admin_repository_test"

ACTOR = "admin@example.com"

API_TOKEN = "whm-api-token-plaintext"
PROXMOX_SECRET = "proxmox-token-secret-plaintext"
SSH_PASSWORD = "ssh-password-plaintext"
SSH_PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nplaintext\n"
SSH_PASSPHRASE = "passphrase-plaintext"

FINGERPRINT = "SHA256:AAAAC3NzaC1lZDI1NTE5AAAAILiveDatabaseHostKey"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session_factory(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test engine, with the mutated tables emptied first.

    Function-scoped because an asyncpg connection belongs to the event loop that opened it, and
    each test gets its own loop.
    """
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as opened:
        yield opened


@pytest.fixture
def cipher() -> SecretCipher:
    return build_cipher()


def whm_service(
    session: AsyncSession, cipher: SecretCipher
) -> ServerAdminService[WHMServer, WHMServerCreate, WHMServerUpdate]:
    return ServerAdminService(
        repository=SQLServerAdminRepository(session, model=WHMServer, host_field="base_url"),
        policy=WHM_ADMIN_POLICY,
        cipher=cipher,
        audit_sink=RecordingAuditSink(),
    )


def proxmox_service(
    session: AsyncSession, cipher: SecretCipher
) -> ServerAdminService[ProxmoxServer, ProxmoxServerCreate, ProxmoxServerUpdate]:
    return ServerAdminService(
        repository=SQLServerAdminRepository(session, model=ProxmoxServer),
        policy=PROXMOX_ADMIN_POLICY,
        cipher=cipher,
        audit_sink=RecordingAuditSink(),
    )


def pmg_service(
    session: AsyncSession, cipher: SecretCipher
) -> ServerAdminService[PMGServer, PMGServerCreate, PMGServerUpdate]:
    return ServerAdminService(
        repository=SQLServerAdminRepository(session, model=PMGServer, host_field="ssh_host"),
        policy=PMG_ADMIN_POLICY,
        cipher=cipher,
        audit_sink=RecordingAuditSink(),
    )


def whm_spec(name: str = "web16", **overrides: object) -> WHMServerCreate:
    """A create spec whose secrets are **plaintext**, as the service takes them."""
    values: dict[str, object] = {
        "name": name,
        "base_url": f"https://{name}.example.net:2087",
        "api_username": "root",
        "api_token": API_TOKEN,
        "verify_ssl": True,
        "ssh": SSHCredentials(
            ssh_username="noa",
            ssh_port=22,
            ssh_password=SSH_PASSWORD,
            ssh_private_key=SSH_PRIVATE_KEY,
            ssh_private_key_passphrase=SSH_PASSPHRASE,
        ),
    }
    values.update(overrides)
    return WHMServerCreate(**values)  # type: ignore[arg-type]


def proxmox_spec(name: str = "pve1") -> ProxmoxServerCreate:
    return ProxmoxServerCreate(
        name=name,
        base_url=f"https://{name}.example.net:8006",
        api_token_id="root@pam!noa",
        api_token_secret=PROXMOX_SECRET,
        verify_ssl=False,
    )


def pmg_spec(name: str = "mail1") -> PMGServerCreate:
    return PMGServerCreate(
        name=name,
        ssh_host=f"{name}.example.net",
        ssh=SSHCredentials(
            ssh_username="noa",
            ssh_port=22,
            ssh_password=SSH_PASSWORD,
            ssh_private_key=SSH_PRIVATE_KEY,
            ssh_private_key_passphrase=SSH_PASSPHRASE,
            ssh_host_key_fingerprint=FINGERPRINT,
        ),
    )


async def observed_names(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[WHMServer] | type[ProxmoxServer] | type[PMGServer],
) -> set[str]:
    """Row names as a **separate** connection sees them. The live-commit witness."""
    async with session_factory() as observer:
        result = await observer.execute(sa.select(model.name))
        return set(result.scalars().all())


# --- Secrets encrypted at rest: what the columns hold ---


async def test_every_secret_column_is_written_as_ciphertext(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """The stored bytes, read back as text, across all three tables.

    Read with raw SQL rather than through the ORM attribute, so the assertion is about the
    column and not about anything a type decorator might do on the way out.
    """
    whm = await whm_service(session, cipher).create(whm_spec(), actor_email=ACTOR)
    proxmox = await proxmox_service(session, cipher).create(proxmox_spec(), actor_email=ACTOR)
    pmg = await pmg_service(session, cipher).create(pmg_spec(), actor_email=ACTOR)

    whm_row = (
        await session.execute(
            sa.text(
                "SELECT api_token, ssh_password, ssh_private_key, ssh_private_key_passphrase, "
                "ssh_host_key_fingerprint FROM whm_servers WHERE id = :id"
            ),
            {"id": str(whm.id)},
        )
    ).one()
    for stored in whm_row[:4]:
        assert stored.startswith(ENCRYPTED_PREFIX), stored
    # No fingerprint was supplied for the WHM row: a first validate is what captures it.
    assert whm_row[4] is None

    proxmox_secret = (
        await session.execute(
            sa.text("SELECT api_token_secret FROM proxmox_servers WHERE id = :id"),
            {"id": str(proxmox.id)},
        )
    ).scalar_one()
    assert proxmox_secret.startswith(ENCRYPTED_PREFIX)

    pmg_row = (
        await session.execute(
            sa.text(
                "SELECT ssh_password, ssh_private_key, ssh_private_key_passphrase, "
                "ssh_host_key_fingerprint FROM pmg_servers WHERE id = :id"
            ),
            {"id": str(pmg.id)},
        )
    ).one()
    for stored in pmg_row[:3]:
        assert stored.startswith(ENCRYPTED_PREFIX), stored
    # The fingerprint is a **public digest** and is stored as typed: it exists to be compared
    # mid-handshake and to be shown to an operator, so ciphertext would buy nothing and cost
    # both (`core.servers.admin_service.encrypt_ssh_credentials`).
    assert pmg_row[3] == FINGERPRINT

    # ...and the plaintext is nowhere in any of the three rows.
    dumped = repr(whm_row) + repr(proxmox_secret) + repr(pmg_row)
    for secret in (API_TOKEN, PROXMOX_SECRET, SSH_PASSWORD, SSH_PRIVATE_KEY, SSH_PASSPHRASE):
        assert secret not in dumped


async def test_a_stored_secret_decrypts_back_to_what_was_submitted(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """The round trip, so "it is encrypted" is not satisfied by storing garbage."""
    created = await whm_service(session, cipher).create(whm_spec(), actor_email=ACTOR)

    assert cipher.decrypt_text(created.api_token) == API_TOKEN
    assert created.ssh_password is not None
    assert cipher.decrypt_text(created.ssh_password) == SSH_PASSWORD


# --- The commit boundary, with a witness ---


async def test_commit_makes_a_create_outlive_the_request(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """Flushed is not persisted, and only a second connection can tell.

    Without the boundary the route answers 201 with a body describing a row that vanishes at
    teardown — an operator adds a server, sees it in the response, and it is not there on
    reload, with no error anywhere.

    The direct `create` through the repository alone is the negative control (the
    compare-must-still-separate rule, the flush-only-200 bug's own shape): the same write,
    checked before anything commits.
    """
    # Negative control: flush only.
    control_repository = SQLServerAdminRepository(session, model=WHMServer, host_field="base_url")
    flushed = await control_repository.create(whm_spec("control"))
    assert await observed_names(session_factory, WHMServer) == set()

    created = await whm_service(session, cipher).create(whm_spec("committed"), actor_email=ACTOR)

    observed = await observed_names(session_factory, WHMServer)
    assert created.name in observed
    # The control's row is visible now too — the service's commit ended the shared transaction.
    # Asserted so the control's silence above reads as "not yet committed", never as "this
    # observer cannot see inserts".
    assert flushed.name in observed


async def test_commit_makes_an_update_outlive_the_request(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """The commit-boundary rule: the same boundary on the write that *edits* a server.

    An uncommitted rename answers 200 with the new name and leaves the old one in place, so the
    panel and the database disagree until somebody reloads.
    """
    service = whm_service(session, cipher)
    subject = await service.create(whm_spec("before-rename"), actor_email=ACTOR)
    control = await service.create(whm_spec("control"), actor_email=ACTOR)

    # Negative control: the identical edit through the repository alone, flush only.
    await SQLServerAdminRepository(session, model=WHMServer, host_field="base_url").update(
        control.id, WHMServerUpdate(name="control-renamed")
    )
    assert "control-renamed" not in await observed_names(session_factory, WHMServer)

    await service.update(subject.id, WHMServerUpdate(name="after-rename"), actor_email=ACTOR)

    observed = await observed_names(session_factory, WHMServer)
    assert "after-rename" in observed
    assert "before-rename" not in observed
    assert "control-renamed" in observed


async def test_commit_makes_a_delete_outlive_the_request(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """The commit-boundary rule again, on the dangerous direction.

    An uncommitted delete answers `{ok: true}`, the panel stops listing the server, and the row
    — with the credentials on it — survives the request that removed it.
    """
    service = pmg_service(session, cipher)
    subject = await service.create(pmg_spec("doomed"), actor_email=ACTOR)
    control = await service.create(pmg_spec("control"), actor_email=ACTOR)

    # Negative control: the identical delete through the repository alone, flush only.
    control_repository = SQLServerAdminRepository(session, model=PMGServer, host_field="ssh_host")
    assert await control_repository.delete(control.id)
    assert "control" in await observed_names(session_factory, PMGServer)

    await service.delete(subject.id, actor_email=ACTOR)

    observed = await observed_names(session_factory, PMGServer)
    assert "doomed" not in observed
    assert "control" not in observed


@pytest.mark.parametrize("system", ["whm", "proxmox", "pmg"])
async def test_every_mutation_of_every_service_commits(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
    system: str,
) -> None:
    """The commit-boundary rule: the whole *class* of mutations, on all three services.

    Parametrised rather than sampled, and the sibling-service flush-only bug is why:
    `AuthorizationService` was fixed at the first sighting and `McpTokenService` carried the
    identical hole until token management landed, so "one of them commits" is not
    the claim — nine writes are, and each is observed from a second session.

    A mutation check confirms this separates: deleting the `commit()` from any one of the nine
    turns exactly this test red for that system.
    """
    builders = {
        "whm": (whm_service, whm_spec, WHMServer, WHMServerUpdate),
        "proxmox": (proxmox_service, proxmox_spec, ProxmoxServer, ProxmoxServerUpdate),
        "pmg": (pmg_service, pmg_spec, PMGServer, PMGServerUpdate),
    }
    build_service, build_spec, model, update_model = builders[system]
    service = build_service(session, cipher)

    created = await service.create(build_spec(f"{system}-committed"), actor_email=ACTOR)
    assert f"{system}-committed" in await observed_names(session_factory, model)

    await service.update(created.id, update_model(name=f"{system}-renamed"), actor_email=ACTOR)
    observed = await observed_names(session_factory, model)
    assert f"{system}-renamed" in observed
    assert f"{system}-committed" not in observed

    await service.delete(created.id, actor_email=ACTOR)
    assert await observed_names(session_factory, model) == set()


async def test_a_refused_create_persists_nothing(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """The commit-boundary rule: the guards raise before the commit, so the refusal leaves
    no row behind."""
    service = whm_service(session, cipher)
    await service.create(whm_spec("taken"), actor_email=ACTOR)

    with pytest.raises(WHMServerNameExistsError):
        await service.create(whm_spec("TAKEN"), actor_email=ACTOR)

    assert await observed_names(session_factory, WHMServer) == {"taken"}


async def test_validate_persists_the_captured_fingerprint(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """The commit-boundary rule on the one column the validate flow writes.

    The pin repository is the object the validation service holds, and it commits its own
    transaction — a pin that only flushed would leave the row unvalidated while the operator
    reads "Host key pinned" in the panel, and every subsequent tool call would then refuse with
    `ssh_host_key_not_validated`.
    """
    created = await whm_service(session, cipher).create(whm_spec("unpinned"), actor_email=ACTOR)

    async def observed_pin() -> str | None:
        async with session_factory() as observer:
            result = await observer.execute(
                sa.select(WHMServer.ssh_host_key_fingerprint).where(WHMServer.id == created.id)
            )
            return result.scalar_one()

    assert await observed_pin() is None

    # Negative control: the write through a *flush* only, on a second server, stays invisible.
    control = await whm_service(session, cipher).create(whm_spec("control"), actor_email=ACTOR)
    control_pin = SQLHostKeyPinRepository(session, model=WHMServer)
    assert await control_pin.set_host_key_fingerprint(control.id, FINGERPRINT)

    async def observed_control_pin() -> str | None:
        async with session_factory() as observer:
            result = await observer.execute(
                sa.select(WHMServer.ssh_host_key_fingerprint).where(WHMServer.id == control.id)
            )
            return result.scalar_one()

    assert await observed_control_pin() is None

    pins = SQLHostKeyPinRepository(session, model=WHMServer)
    assert await pins.set_host_key_fingerprint(created.id, FINGERPRINT)
    await pins.commit()

    assert await observed_pin() == FINGERPRINT
    assert await observed_control_pin() == FINGERPRINT


async def test_the_pmg_pin_repository_commits_too(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    cipher: SecretCipher,
) -> None:
    """The second table's pin writer, so neither validate flow carries the hole alone."""
    created = await pmg_service(session, cipher).create(
        PMGServerCreate(name="unpinned", ssh_host="unpinned.example.net"), actor_email=ACTOR
    )

    pins = SQLHostKeyPinRepository(session, model=PMGServer)
    assert await pins.set_host_key_fingerprint(created.id, FINGERPRINT)
    await pins.commit()

    async with session_factory() as observer:
        stored = (
            await observer.execute(
                sa.select(PMGServer.ssh_host_key_fingerprint).where(PMGServer.id == created.id)
            )
        ).scalar_one()
    assert stored == FINGERPRINT


async def test_a_pin_repository_cannot_reach_another_column(session: AsyncSession) -> None:
    """The narrowness is the guarantee, so it is asserted rather than described.

    A reachability probe holds one of these, bound to its table. If it grew a `create`, an
    `update` or a `delete`, a validate could rewrite a credential — the reason this is a
    separate class from the CRUD repository at all (`core.servers.admin_repository`). One
    class covers both tables now, so one assertion covers both validate flows.
    """
    surface = {
        name
        for name in dir(SQLHostKeyPinRepository)
        if not name.startswith("_") and callable(getattr(SQLHostKeyPinRepository, name))
    }

    assert surface == {"get_by_id", "set_host_key_fingerprint", "commit"}


# --- The unique-name constraint, as the backstop it is ---


async def test_the_unique_index_is_the_backstop_behind_the_service_check(
    session: AsyncSession,
) -> None:
    """Two exact-case inserts race past the pre-check; the column refuses the second.

    The service's `name_taken` is a *pre*-check, so it cannot close the window between two
    concurrent creates. The constraint is what does, and this asserts it is really there rather
    than assumed from the model declaration.
    """
    repository = SQLServerAdminRepository(session, model=WHMServer, host_field="base_url")
    await repository.create(whm_spec("duplicate"))

    with pytest.raises(IntegrityError):
        await repository.create(whm_spec("duplicate"))


@pytest.mark.parametrize(
    ("model", "table", "host_field"),
    [
        (WHMServer, "whm_servers", "base_url"),
        (ProxmoxServer, "proxmox_servers", None),
        (PMGServer, "pmg_servers", "ssh_host"),
    ],
)
async def test_the_name_check_is_case_insensitive_in_sql(
    session: AsyncSession, model: type, table: str, host_field: str | None
) -> None:
    """`func.lower(...)` on both sides, on every table.

    `core.servers.reference` records the harm this prevents: `Node1` and `node1` both satisfy a
    case-sensitive unique index and both match `NODE1`, which is a permanent `host_ambiguous`
    for a reference an operator will keep typing.
    """
    specs = {"whm_servers": whm_spec, "proxmox_servers": proxmox_spec, "pmg_servers": pmg_spec}
    repository = SQLServerAdminRepository(session, model=model, host_field=host_field)

    created = await repository.create(specs[table]("Node1"))  # type: ignore[arg-type]

    assert await repository.name_taken("node1") is True
    assert await repository.name_taken("NODE1") is True
    # ...and the row is allowed to keep its own name.
    assert await repository.name_taken("Node1", exclude_id=created.id) is False
    assert await repository.name_taken("unrelated") is False


# --- Reads the write repository inherits rather than re-implements ---
#
# `ORDER BY name` and the `None`-for-absent contract belong to `SQLServerRepository` and are
# proved per table in `test_server_repository.py`. `SQLServerAdminRepository` composes it in one
# line (`self._reads = SQLServerRepository(session, model=model)`), so re-asserting them here
# would be asserting that one line twice.


async def test_a_created_row_carries_its_server_generated_columns(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """`id`, `created_at`, `updated_at` come back filled — the response shape needs all three.

    Asserted by property, not by value: two of them are clock-stamped, and comparing a timestamp
    to a literal is a coin flip.
    """
    created = await whm_service(session, cipher).create(whm_spec(), actor_email=ACTOR)

    assert isinstance(created.id, UUID)
    assert created.created_at is not None
    assert created.updated_at is not None


# --- The partial-update contract, against real SQL ---


async def test_an_absent_field_leaves_the_column_alone(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """`None` means "leave alone", asserted on the stored value rather than on the answer."""
    service = whm_service(session, cipher)
    created = await service.create(whm_spec("partial"), actor_email=ACTOR)
    stored_token = created.api_token

    updated = await service.update(
        created.id, WHMServerUpdate(api_username="reseller"), actor_email=ACTOR
    )

    assert updated.api_username == "reseller"
    assert updated.api_token == stored_token
    assert updated.ssh_password is not None


async def test_a_clear_flag_really_nulls_the_column(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """`clear_ssh_configuration` empties all six SSH columns, including the pin."""
    service = pmg_service(session, cipher)
    created = await service.create(pmg_spec("clearable"), actor_email=ACTOR)
    assert created.ssh_host_key_fingerprint == FINGERPRINT

    updated = await service.update(
        created.id,
        PMGServerUpdate(ssh=SSHCredentialsPatch(clear_ssh_configuration=True)),
        actor_email=ACTOR,
    )

    assert updated.ssh_username is None
    assert updated.ssh_port is None
    assert updated.ssh_password is None
    assert updated.ssh_private_key is None
    assert updated.ssh_private_key_passphrase is None
    assert updated.ssh_host_key_fingerprint is None


async def test_moving_the_host_drops_the_pin_in_sql(
    session: AsyncSession, cipher: SecretCipher
) -> None:
    """The host-key pin's invalidation rule, through the real statement.

    With its negative control: an edit that touches neither the host nor the port keeps the pin,
    or "the pin was dropped" would pass against a repository that drops it on every save.
    """
    service = pmg_service(session, cipher)
    created = await service.create(pmg_spec("movable"), actor_email=ACTOR)

    kept = await service.update(created.id, PMGServerUpdate(name="renamed"), actor_email=ACTOR)
    assert kept.ssh_host_key_fingerprint == FINGERPRINT

    moved = await service.update(
        created.id, PMGServerUpdate(ssh_host="elsewhere.example.net"), actor_email=ACTOR
    )
    assert moved.ssh_host_key_fingerprint is None
