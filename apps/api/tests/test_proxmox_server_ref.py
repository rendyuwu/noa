"""Operator word → one Proxmox server.

The third sibling of `test_whm_server_ref.py` and `test_pmg_server_ref.py`, and the one whose
existence changed the code under it: the server-list work parked the question of whether a third
resolver would be two parameters or a third shape, and Proxmox turned out to be WHM's shape exactly,
so the policy now lives once in `core.servers.reference`. **These assertions are therefore doing two
jobs**: they cover Proxmox's own adapter — the `base_url` host accessor and the `choices` fields —
and, together with the two files they mirror, they are what says the extraction changed no
behaviour.

**A tie produces candidates, never a pick**, and the stakes are one notch higher than on the
earlier resolvers: the caller is a CHANGE tool, so a guess opens an approval card for a machine
the operator never named and resets a password on it.

Ties are reachable even though `proxmox_servers.name` is `unique=True`: Postgres
uniqueness is case-sensitive and the match is not, so `Pve1` and `pve1` can both exist and both
answer to `PVE1`.

Real `ProxmoxServer` rows over an in-memory repository (`support.servers`), because the
resolver's UUID branch needs a real id and the row it hands back is the row a tool then builds a
client from.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

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


# --- A tie is candidates ---


async def test_a_name_tie_returns_choices_rather_than_a_pick() -> None:
    """Reachable because Postgres uniqueness is case-sensitive and matching is not."""
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
    """A tie between forty endpoints must not paste forty rows into a transcript."""
    repository = FakeProxmoxServerRepository(
        [
            proxmox_server(f"pve{index}", base_url="https://shared.example.net:8006")
            for index in range(MAX_CHOICES + 5)
        ]
    )

    resolution = await resolve_proxmox_server_ref("shared.example.net", repository=repository)

    assert len(resolution.choices) == MAX_CHOICES


def test_a_choice_names_the_server_without_a_credential() -> None:
    """Enough to recognise an endpoint and name it, and nothing else.

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


# --- The blank-reference guard and the not-found cases ---


async def test_a_blank_reference_is_refused() -> None:
    """A whitespace-only required string is a bad call, not a wildcard.

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
    send an operator to the wrong inventory — the cost if the extraction were careless."""
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


# --- A node name resolves to the server that runs it ---
#
# The operator pastes a VM lookup and sends its `Host Node` as both `node` and `server_ref`, so a
# reference that matches nothing and ends in a node suffix is tried once more as its cluster.
# These six bind the retry's shape; a seventh case needs no test of its own, because a blank
# reference never reaches the retry and `test_a_blank_reference_is_refused` above already pins
# that — it asserts `host_required`, which is the code the retry cannot produce.
#
# All six assert on the resolution. `test_a_reference_with_no_node_suffix_is_looked_up_once` also
# asserts on `reads`, and that second assertion is the only thing binding one half of the
# derivation guard, which has no signature in a resolution at all — see its docstring.
#
# **One coupling, stated rather than bound.** Two of these read a refusal's *wording*, not just its
# code: the miss test requires `'example'` to be absent from the message and the tie test requires
# it to be present. Both depend on `core/servers/reference.py` wrapping the reference in single
# quotes, because `example` is a prefix of `examplepve09` and that delimiter is the only thing
# separating them. Nothing pins that wording — no test in this repo does, and it is shared with WHM
# and PMG, so the reword that would break a pin here is a three-system change to begin with. The
# asymmetry to know: a reword breaks the tie assertion loudly, and disarms the miss one *silently*,
# since a bare `example` in the message still satisfies a negative.


@pytest.mark.parametrize(
    "node",
    ["examplepve09", "example-pve09", "example_pve09", "example.pve09", "EXAMPLEPVE09"],
)
async def test_a_node_name_resolves_to_the_server_that_runs_it(node: str) -> None:
    """The fleet names nodes `<cluster>pve<NN>` and names each NOA row after the cluster.

    The upper-case row is not decoration: the derived string goes through the same
    case-insensitive name comparison as a typed one, and nothing else here would say so.

    One row per separator the suffix pattern accepts — none, `-`, `_`, `.` — so the set the regex
    allows and the set something reads are the same set. A separator dropped from the pattern
    reddens its own row rather than passing unnoticed.
    """
    wanted = proxmox_server("example")
    repository = FakeProxmoxServerRepository([wanted, proxmox_server("other")])

    resolution = await resolve_proxmox_server_ref(node, repository=repository)

    assert resolution.ok is True
    assert resolution.server is wanted


async def test_a_server_named_after_its_own_node_wins_over_the_derived_one() -> None:
    """The exact lookup runs first and its answer stands.

    The ordering control: without it the retry could shadow a row genuinely named after a node and
    no assertion would notice.
    """
    itself = proxmox_server("examplepve09")
    repository = FakeProxmoxServerRepository([itself, proxmox_server("example")])

    resolution = await resolve_proxmox_server_ref("examplepve09", repository=repository)

    assert resolution.server is itself


async def test_a_node_name_whose_server_is_unknown_names_what_was_sent() -> None:
    """The refusal quotes the operator's string, not NOA's derivation of it.

    An operator reading `No Proxmox server found matching 'example'` for a word they never typed
    would be debugging NOA's inference instead of their own input.
    """
    repository = FakeProxmoxServerRepository([proxmox_server("other")])

    resolution = await resolve_proxmox_server_ref("examplepve09", repository=repository)

    assert resolution.error_code == ERROR_NOT_FOUND
    assert "examplepve09" in resolution.message
    assert "'example'" not in resolution.message


async def test_a_server_named_only_for_the_suffix_is_not_stripped_to_nothing() -> None:
    """A reference that is *only* a node suffix has no cluster to derive, so nothing is retried.

    The second half is what proves the empty-string search never happens: an unguarded strip
    would search `""`, and a whitespace-only reference answers `host_required`, so the error
    *code* separates the two failure shapes.
    """
    wanted = proxmox_server("pve1")
    repository = FakeProxmoxServerRepository([wanted])

    resolved = await resolve_proxmox_server_ref("pve1", repository=repository)
    missing = await resolve_proxmox_server_ref("pve9", repository=repository)

    assert resolved.server is wanted
    assert missing.error_code == ERROR_NOT_FOUND
    assert "pve9" in missing.message


async def test_a_reference_with_no_node_suffix_is_looked_up_once() -> None:
    """The half of the derivation guard that no assertion on a resolution can see.

    A derivation that equals what was sent would resolve to the same refusal, so `error_code`,
    `message` and `choices` come back identical whether or not it is suppressed — measured, by
    breaking the guard and running the whole suite: 3203 passed either way. The only trace is a
    second identical inventory read, so that is what this asserts.

    Written because the alternative was leaving it unbound and saying so. It is cheap to bind:
    the double already counts its reads for its own sake.
    """
    repository = FakeProxmoxServerRepository([proxmox_server("example")])

    resolution = await resolve_proxmox_server_ref("nope", repository=repository)

    assert resolution.error_code == ERROR_NOT_FOUND
    assert repository.reads == 1


async def test_a_tie_on_the_derived_name_answers_with_candidates() -> None:
    """A tie is news even though the operator never typed the name it ties on.

    With no Proxmox read tool in the catalog, `choices` is the only thing that unblocks the model,
    so the retry's answer is kept for every code except `host_not_found`. This is the only test
    here that separates that from `.ok`: a retry that kept its answer only when it succeeded
    would drop these candidates and refuse, and every other test in this section would stay green.
    """
    repository = FakeProxmoxServerRepository([proxmox_server("Example"), proxmox_server("example")])

    resolution = await resolve_proxmox_server_ref("examplepve09", repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [choice["name"] for choice in resolution.choices] == ["Example", "example"]
    # And the asymmetry with the miss above, which `docs/integrations/proxmox.md` states: a miss
    # quotes what the operator sent, a tie quotes the derived name. It has to — the candidates it
    # carries are answers to `example`, so a message naming `examplepve09` beside them would ask
    # the operator to pick between rows that match a different string.
    assert "'example'" in resolution.message
