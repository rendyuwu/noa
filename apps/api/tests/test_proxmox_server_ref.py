"""Operator word → one Proxmox server (T27, §V.18, §V.21).

The third sibling of `test_whm_server_ref.py` and `test_pmg_server_ref.py`, and the one whose
existence changed the code under it: T19 parked the question of whether a third resolver would be
two parameters or a third shape, and Proxmox turned out to be WHM's shape exactly, so the policy
now lives once in `core.servers.reference` (§V.66). **These assertions are therefore doing two
jobs**: they cover Proxmox's own adapter — the `base_url` host accessor and the `choices` fields
— and, together with the two files they mirror, they are what says the extraction changed no
behaviour.

**A tie produces candidates, never a pick**, and the stakes are one notch higher than at T31: the
caller is a CHANGE tool, so a guess opens an approval card for a machine the operator never named
and resets a password on it.

Ties are reachable even though `proxmox_servers.name` is `unique=True` (§T.4): Postgres
uniqueness is case-sensitive and the match is not, so `Pve1` and `pve1` can both exist and both
answer to `PVE1`.

Real `ProxmoxServer` rows over an in-memory repository (`support.servers`), because the
resolver's UUID branch needs a real id and the row it hands back is the row a tool then builds a
client from.
"""

from __future__ import annotations

from uuid import uuid4

from core.servers.proxmox_ref import (
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    MESSAGE_REQUIRED,
    describe,
    resolve_proxmox_server_ref,
)
from support.servers import (
    PROXMOX_API_TOKEN_SECRET,
    FakeProxmoxServerRepository,
    proxmox_server,
)

# --- The three match kinds, in order ---


async def test_an_id_resolves() -> None:
    """The unambiguous form, and what a `choices` list tells the caller to come back with."""
    wanted = proxmox_server("pve1")
    repository = FakeProxmoxServerRepository([wanted, proxmox_server("pve2")])

    resolution = await resolve_proxmox_server_ref(str(wanted.id), repository=repository)

    assert resolution.ok is True
    assert resolution.server is wanted
    assert resolution.server_id == wanted.id


async def test_a_name_resolves_case_insensitively() -> None:
    """Operators type what they remember, in whatever case they remember it."""
    wanted = proxmox_server("Pve1")
    repository = FakeProxmoxServerRepository([wanted])

    resolution = await resolve_proxmox_server_ref("PVE1", repository=repository)

    assert resolution.server is wanted


async def test_a_hostname_resolves_out_of_the_base_url() -> None:
    """Proxmox stores an endpoint URL, so the host is parsed rather than read off a column.

    The port is part of the `base_url` and not part of the host, which is the case a naive
    string comparison against the URL would get wrong — an operator says `pve1.example.net`, not
    `https://pve1.example.net:8006`.
    """
    wanted = proxmox_server("alpha", base_url="https://pve1.example.net:8006")
    repository = FakeProxmoxServerRepository([wanted, proxmox_server("bravo")])

    resolution = await resolve_proxmox_server_ref("PVE1.example.net", repository=repository)

    assert resolution.server is wanted


async def test_a_name_wins_over_another_server_s_hostname() -> None:
    """Order is not cosmetic: a name is what an admin typed into NOA on purpose, while a
    hostname is whatever the row's URL happens to point at."""
    by_name = proxmox_server("pve1.example.net", base_url="https://alpha.example.net:8006")
    by_host = proxmox_server("bravo", base_url="https://pve1.example.net:8006")
    repository = FakeProxmoxServerRepository([by_name, by_host])

    resolution = await resolve_proxmox_server_ref("pve1.example.net", repository=repository)

    assert resolution.server is by_name


# --- V18: a tie is candidates ---


async def test_a_name_tie_returns_choices_rather_than_a_pick() -> None:
    """§V.18. Reachable because Postgres uniqueness is case-sensitive and matching is not."""
    repository = FakeProxmoxServerRepository([proxmox_server("Pve1"), proxmox_server("pve1")])

    resolution = await resolve_proxmox_server_ref("PVE1", repository=repository)

    assert resolution.ok is False
    assert resolution.server is None
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [choice["name"] for choice in resolution.choices] == ["Pve1", "pve1"]


async def test_a_hostname_tie_returns_choices() -> None:
    """Two rows can name one endpoint — the same cluster entered twice under different names.

    On a CHANGE path a pick here is a password reset on whichever row sorted first.
    """
    shared = "https://pve1.example.net:8006"
    repository = FakeProxmoxServerRepository(
        [proxmox_server("alpha", base_url=shared), proxmox_server("bravo", base_url=shared)]
    )

    resolution = await resolve_proxmox_server_ref("pve1.example.net", repository=repository)

    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [choice["base_url"] for choice in resolution.choices] == [shared, shared]


async def test_choices_are_bounded() -> None:
    """A tie between forty endpoints must not paste forty rows into a transcript (§V.26)."""
    repository = FakeProxmoxServerRepository(
        [
            proxmox_server(f"pve{index}", base_url="https://shared.example.net:8006")
            for index in range(MAX_CHOICES + 5)
        ]
    )

    resolution = await resolve_proxmox_server_ref("shared.example.net", repository=repository)

    assert len(resolution.choices) == MAX_CHOICES


def test_a_choice_names_the_server_without_a_credential() -> None:
    """§V.8, §V.26: enough to recognise an endpoint and name it, and nothing else.

    `api_token_id` is absent as well as the secret. It is not a credential on its own, but it
    names the Proxmox user NOA authenticates as, and this text goes into a LibreChat transcript
    a LibreChat administrator can read.
    """
    server = proxmox_server("pve1", base_url="https://pve1.example.net:8006")

    assert describe(server) == {
        "id": str(server.id),
        "name": "pve1",
        "base_url": "https://pve1.example.net:8006",
    }
    assert PROXMOX_API_TOKEN_SECRET not in describe(server).values()


# --- V21 and the not-found cases ---


async def test_a_blank_reference_is_refused() -> None:
    """§V.21: a whitespace-only required string is a bad call, not a wildcard.

    `reads == 0` is the part worth asserting: the guard runs before inventory is touched, so a
    malformed call costs no query.
    """
    repository = FakeProxmoxServerRepository([proxmox_server("pve1")])

    resolution = await resolve_proxmox_server_ref("   ", repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_REQUIRED
    assert resolution.message == MESSAGE_REQUIRED
    assert repository.reads == 0


async def test_the_refusal_names_proxmox_rather_than_another_system() -> None:
    """The shared resolver takes the subject noun as a parameter, so this is what says the
    Proxmox adapter passed its own — a message reading "WHM server reference is required" would
    send an operator to the wrong inventory (§V.66's cost if the extraction were careless)."""
    repository = FakeProxmoxServerRepository()

    blank = await resolve_proxmox_server_ref("", repository=repository)
    missing = await resolve_proxmox_server_ref("nope", repository=repository)

    assert "Proxmox" in blank.message
    assert "Proxmox" in missing.message
    assert "WHM" not in blank.message
    assert "PMG" not in missing.message


async def test_an_unknown_reference_is_host_not_found() -> None:
    repository = FakeProxmoxServerRepository([proxmox_server("pve1")])

    resolution = await resolve_proxmox_server_ref("nope", repository=repository)

    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.choices == []


async def test_a_wellformed_id_that_matches_nothing_does_not_fall_through() -> None:
    """A mistyped id must not resolve to a *different* server whose name happened to be that
    string — the one outcome an id is supposed to rule out."""
    missing = uuid4()
    repository = FakeProxmoxServerRepository([proxmox_server(str(missing))])

    resolution = await resolve_proxmox_server_ref(str(missing), repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_NOT_FOUND
    assert str(missing) in resolution.message


async def test_a_reference_is_trimmed_before_matching() -> None:
    """A pasted trailing newline is an operator artifact, not an unknown server."""
    wanted = proxmox_server("pve1")
    repository = FakeProxmoxServerRepository([wanted])

    resolution = await resolve_proxmox_server_ref("  pve1\n", repository=repository)

    assert resolution.server is wanted


async def test_a_row_whose_base_url_does_not_parse_simply_does_not_match() -> None:
    """A malformed URL is a bad row, not a bad *reference*.

    It must not raise and must not shadow another row: the accessor answers `None` and that row
    is skipped, so a healthy endpoint entered beside it still resolves.
    """
    healthy = proxmox_server("bravo", base_url="https://pve1.example.net:8006")
    repository = FakeProxmoxServerRepository(
        [proxmox_server("alpha", base_url="not a url"), healthy]
    )

    resolution = await resolve_proxmox_server_ref("pve1.example.net", repository=repository)

    assert resolution.server is healthy


async def test_an_empty_inventory_is_host_not_found() -> None:
    """No Proxmox servers configured is a refusal the model can act on, not an exception."""
    resolution = await resolve_proxmox_server_ref("pve1", repository=FakeProxmoxServerRepository())

    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.server_id is None
