"""Resolving an operator's word to one WHM server.

V18 is a rule about what NOA does when it *cannot* tell: it returns candidates, it does not
pick. So most of this file is about the failure shapes, and the assertions are on the
`error_code` and the `choices` rather than on the prose — those strings are what the model
and the admin panel branch on.

The resolver runs for real against `FakeWHMServerRepository`; only the SQL is doubled. Rows
are real `WHMServer` instances (see `support.servers`), so a `choices` entry is built from
the same object production would build it from.
"""

from __future__ import annotations

from uuid import uuid4

from core.servers.whm_ref import (
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    resolve_whm_server_ref,
)
from support.servers import FakeWHMServerRepository, whm_server

# --- The three ways a reference resolves ---


async def test_a_uuid_resolves_through_a_direct_read() -> None:
    """The id path answers without listing every server.

    Asserted on the read counter as well as the result: the id branch exists to be the cheap
    unambiguous one, and a resolver that fell through to `list_servers` for it would scale
    with the inventory on the hot path every other tool starts from.
    """
    wanted = whm_server("alpha")
    repository = FakeWHMServerRepository([wanted, whm_server("beta")])

    resolution = await resolve_whm_server_ref(str(wanted.id), repository=repository)

    assert resolution.ok
    assert resolution.server is wanted
    assert resolution.server_id == wanted.id


async def test_a_name_resolves_case_insensitively() -> None:
    """An operator typing `ALPHA` means the server named `alpha`."""
    wanted = whm_server("alpha")
    repository = FakeWHMServerRepository([wanted, whm_server("beta")])

    resolution = await resolve_whm_server_ref("  ALPHA  ", repository=repository)

    assert resolution.ok
    assert resolution.server is wanted


async def test_a_base_url_hostname_resolves_when_the_name_does_not() -> None:
    """V18's third form: the host out of `base_url`, not the NOA name.

    Operators read hostnames off tickets and monitoring, so the reference they have is often
    the machine's, not the label NOA stores.
    """
    wanted = whm_server("alpha", base_url="https://cp1.example.net:2087")
    repository = FakeWHMServerRepository([wanted, whm_server("beta")])

    resolution = await resolve_whm_server_ref("CP1.example.net", repository=repository)

    assert resolution.ok
    assert resolution.server is wanted


async def test_a_name_wins_over_another_servers_hostname() -> None:
    """Order is a decision, not an accident.

    A server deliberately named `cp1.example.net` beats a *different* server that merely
    lives at that host: the name was typed into NOA on purpose, the hostname is derived.
    Without the fixed order this input would be a coin flip between two real servers.
    """
    named = whm_server("cp1.example.net", base_url="https://alpha.example.net:2087")
    hosted = whm_server("beta", base_url="https://cp1.example.net:2087")
    repository = FakeWHMServerRepository([named, hosted])

    resolution = await resolve_whm_server_ref("cp1.example.net", repository=repository)

    assert resolution.ok
    assert resolution.server is named


# --- V18: a tie returns candidates, never a pick ---


async def test_two_servers_sharing_a_name_return_candidates_not_a_guess() -> None:
    """V18, the case that matters: `Node1` and `node1` both match `NODE1`.

    Reachable despite `whm_servers.name` being `unique=True` — Postgres uniqueness is
    case-sensitive and the match is not. A resolver that picked one would route a CHANGE to
    whichever row the `ORDER BY` happened to put first.
    """
    upper = whm_server("Node1", base_url="https://one.example.net:2087")
    lower = whm_server("node1", base_url="https://two.example.net:2087")
    repository = FakeWHMServerRepository([upper, lower])

    resolution = await resolve_whm_server_ref("NODE1", repository=repository)

    assert not resolution.ok
    assert resolution.server is None
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert {choice["id"] for choice in resolution.choices} == {str(upper.id), str(lower.id)}
    # Each candidate carries what an operator needs to tell them apart and then name one.
    assert {choice["base_url"] for choice in resolution.choices} == {
        upper.base_url,
        lower.base_url,
    }


async def test_two_servers_sharing_a_hostname_return_candidates() -> None:
    """Two WHM instances behind one host — different ports, same machine."""
    first = whm_server("alpha", base_url="https://shared.example.net:2087")
    second = whm_server("beta", base_url="https://shared.example.net:2088")
    repository = FakeWHMServerRepository([first, second])

    resolution = await resolve_whm_server_ref("shared.example.net", repository=repository)

    assert not resolution.ok
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert len(resolution.choices) == 2


async def test_candidate_lists_are_capped() -> None:
    """A tie between forty servers must not paste forty rows into the transcript.

    V64 is the answer for genuinely large results; a refusal is not the place for one.
    """
    servers = [
        whm_server(f"node-{index}", base_url="https://shared.example.net:2087")
        for index in range(MAX_CHOICES + 5)
    ]
    repository = FakeWHMServerRepository(servers)

    resolution = await resolve_whm_server_ref("shared.example.net", repository=repository)

    assert resolution.error_code == ERROR_AMBIGUOUS
    assert len(resolution.choices) == MAX_CHOICES


async def test_candidates_carry_no_credential_material() -> None:
    """`choices` is transcript: id, name and base URL, nothing else."""
    repository = FakeWHMServerRepository(
        [
            whm_server("alpha", base_url="https://shared.example.net:2087"),
            whm_server("beta", base_url="https://shared.example.net:2088"),
        ]
    )

    resolution = await resolve_whm_server_ref("shared.example.net", repository=repository)

    assert [set(choice) for choice in resolution.choices] == [{"id", "name", "base_url"}] * 2


# --- Refusals ---


async def test_an_unmatched_reference_is_not_found_rather_than_the_only_server() -> None:
    """V18 again, from the other side: one server does not make it the answer.

    The tempting shortcut — "there is only one WHM server, so they must mean that one" —
    is exactly the guess V18 forbids, and it is most dangerous in the deployment where it
    looks safest, because a second server appearing later silently changes behaviour.
    """
    repository = FakeWHMServerRepository([whm_server("alpha")])

    resolution = await resolve_whm_server_ref("gamma", repository=repository)

    assert not resolution.ok
    assert resolution.server is None
    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.choices == []


async def test_a_well_formed_id_that_matches_nothing_stops_at_not_found() -> None:
    """An id does not fall through to the name and hostname passes.

    A mistyped id must not resolve to a different server whose *name* happens to be that
    string — ruling that out is the whole reason to accept an id.
    """
    missing = uuid4()
    repository = FakeWHMServerRepository([whm_server(str(missing))])

    resolution = await resolve_whm_server_ref(str(missing), repository=repository)

    assert not resolution.ok
    assert resolution.error_code == ERROR_NOT_FOUND


async def test_a_whitespace_only_reference_is_refused() -> None:
    """V21: a required string that is only whitespace is a bad call, not a broad search."""
    repository = FakeWHMServerRepository([whm_server("alpha")])

    resolution = await resolve_whm_server_ref("   ", repository=repository)

    assert not resolution.ok
    assert resolution.error_code == ERROR_REQUIRED
    # Refused before any read: a blank reference cannot match anything.
    assert repository.reads == 0


async def test_no_servers_configured_is_not_found() -> None:
    """An empty inventory answers `host_not_found`, not an empty success."""
    resolution = await resolve_whm_server_ref("alpha", repository=FakeWHMServerRepository())

    assert not resolution.ok
    assert resolution.error_code == ERROR_NOT_FOUND
