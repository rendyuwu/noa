"""Operator word → one PMG server.

The PMG sibling of `test_whm_server_ref.py`, and the same property is the one that matters:
**a tie produces candidates, never a pick.** At the whitelist search a guess answers "is this
address whitelisted?" about a node the operator never named; at the pmg-whitelist tool the same
guess whitelists on one.

Ties are reachable even though `pmg_servers.name` is `unique=True`: Postgres uniqueness
is case-sensitive and the match is not, so `Pmg1` and `pmg1` can both exist and both answer to
`PMG1`. Dropping the ambiguity branch because "the column is unique" would be wrong for exactly
that case, so it is asserted rather than reasoned about.

Real `PMGServer` rows over an in-memory repository (`support.servers`), because the resolver's
UUID branch needs a real id and the row it hands back is the row a tool then connects with.
"""

from __future__ import annotations

from uuid import uuid4

from core.servers.pmg_ref import (
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    describe,
    resolve_pmg_server_ref,
)
from support.servers import FakePMGServerRepository, pmg_server

# --- The three match kinds, in order ---


async def test_an_id_resolves() -> None:
    """The unambiguous form, and what a `choices` list tells the caller to come back with."""
    wanted = pmg_server("pmg1")
    repository = FakePMGServerRepository([wanted, pmg_server("pmg2")])

    resolution = await resolve_pmg_server_ref(str(wanted.id), repository=repository)

    assert resolution.ok is True
    assert resolution.server is wanted
    assert resolution.server_id == wanted.id


async def test_a_name_resolves_case_insensitively() -> None:
    """Operators type what they remember, in whatever case they remember it."""
    wanted = pmg_server("Pmg1")
    repository = FakePMGServerRepository([wanted])

    resolution = await resolve_pmg_server_ref("PMG1", repository=repository)

    assert resolution.server is wanted


async def test_an_ssh_host_resolves() -> None:
    """PMG stores a bare host, so there is nothing to parse out of a URL — unlike WHM, where
    the hostname is derived from `base_url`."""
    wanted = pmg_server("pmg1", ssh_host="mail-gw.example.com")
    repository = FakePMGServerRepository([wanted, pmg_server("pmg2")])

    resolution = await resolve_pmg_server_ref("MAIL-GW.example.com", repository=repository)

    assert resolution.server is wanted


async def test_a_name_wins_over_another_server_s_host() -> None:
    """Order is not cosmetic: a name is what an admin typed into NOA on purpose, while a host
    is whatever the row happens to point at."""
    by_name = pmg_server("gw.example.com", ssh_host="pmg1.example.net")
    by_host = pmg_server("pmg2", ssh_host="gw.example.com")
    repository = FakePMGServerRepository([by_name, by_host])

    resolution = await resolve_pmg_server_ref("gw.example.com", repository=repository)

    assert resolution.server is by_name


# --- A tie is candidates ---


async def test_a_name_tie_returns_choices_rather_than_a_pick() -> None:
    """Ambiguous identifier resolves to candidates. Reachable because Postgres uniqueness is
    case-sensitive and matching is not."""
    repository = FakePMGServerRepository([pmg_server("Pmg1"), pmg_server("pmg1")])

    resolution = await resolve_pmg_server_ref("PMG1", repository=repository)

    assert resolution.ok is False
    assert resolution.server is None
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [choice["name"] for choice in resolution.choices] == ["Pmg1", "pmg1"]


async def test_a_host_tie_returns_choices() -> None:
    """Two rows can name the same node — a cluster member reachable under one address, entered
    twice. Picking one would run the operator's question on whichever sorted first."""
    shared = "gw.example.com"
    repository = FakePMGServerRepository(
        [pmg_server("alpha", ssh_host=shared), pmg_server("bravo", ssh_host=shared)]
    )

    resolution = await resolve_pmg_server_ref(shared, repository=repository)

    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [choice["ssh_host"] for choice in resolution.choices] == [shared, shared]


async def test_choices_are_bounded() -> None:
    """A tie between forty nodes must not paste forty rows into a transcript — nothing exposed
    to the transcript should carry raw ops data."""
    repository = FakePMGServerRepository(
        [pmg_server(f"pmg{index}", ssh_host="gw.example.com") for index in range(MAX_CHOICES + 5)]
    )

    resolution = await resolve_pmg_server_ref("gw.example.com", repository=repository)

    assert len(resolution.choices) == MAX_CHOICES


def test_a_choice_names_the_server_without_a_credential() -> None:
    """The three fields that let an operator recognise a node and then name it
    unambiguously, and nothing that would be a leak in a LibreChat transcript."""
    server = pmg_server("pmg1", ssh_host="gw.example.com")

    assert describe(server) == {
        "id": str(server.id),
        "name": "pmg1",
        "ssh_host": "gw.example.com",
    }


# --- Rejected inputs and the not-found cases ---


async def test_a_blank_reference_is_refused() -> None:
    """A whitespace-only required string is a bad call, not a wildcard."""
    repository = FakePMGServerRepository([pmg_server("pmg1")])

    resolution = await resolve_pmg_server_ref("   ", repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_REQUIRED
    assert repository.reads == 0


async def test_an_unknown_reference_is_host_not_found() -> None:
    repository = FakePMGServerRepository([pmg_server("pmg1")])

    resolution = await resolve_pmg_server_ref("nope", repository=repository)

    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.choices == []


async def test_a_wellformed_id_that_matches_nothing_does_not_fall_through() -> None:
    """A mistyped id must not resolve to a *different* server whose name happened to be that
    string — the one outcome an id is supposed to rule out."""
    missing = uuid4()
    repository = FakePMGServerRepository([pmg_server(str(missing))])

    resolution = await resolve_pmg_server_ref(str(missing), repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_NOT_FOUND
    assert str(missing) in resolution.message


async def test_a_reference_is_trimmed_before_matching() -> None:
    """A pasted trailing newline is an operator artifact, not an unknown server."""
    wanted = pmg_server("pmg1")
    repository = FakePMGServerRepository([wanted])

    resolution = await resolve_pmg_server_ref("  pmg1\n", repository=repository)

    assert resolution.server is wanted


async def test_an_empty_inventory_is_host_not_found() -> None:
    """No PMG servers configured is a refusal the model can act on, not an exception."""
    resolution = await resolve_pmg_server_ref("pmg1", repository=FakePMGServerRepository())

    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.server_id is None
