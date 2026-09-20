"""Operator word → one server, for all three inventories at once.

`core.servers.reference.resolve_server_ref` is one function serving WHM, PMG and Proxmox, so
its policy is proved once and re-run per system rather than restated three times. What varies
is bound in `System` below — the subject noun, the wrapper, the in-memory repository, how a row
carries its host, and the ciphertext a `choices` entry must never contain. Everything else is
the policy, and the policy is what must not drift: id, then name, then host; **any** tie is
`choices` rather than a pick; a well-formed id that matches nothing stops rather than falling
through; both fallbacks compare case-insensitively; a whitespace-only reference is a bad call
rather than a wildcard.

**A tie produces candidates, never a pick**, and the stakes rise with the caller: at the PMG
whitelist search a guess answers "is this address whitelisted?" about a node the operator never
named, and at the Proxmox password reset it opens an approval card for a machine they never
named.

Ties are reachable even though every `name` column is `unique=True`: Postgres uniqueness is
case-sensitive and the match is not, so `Node1` and `node1` can both exist and both answer to
`NODE1`. Dropping the ambiguity branch because "the column is unique" would be wrong for exactly
that case, so it is asserted rather than reasoned about.

The resolvers run for real; only the SQL is doubled. Rows are real mapped instances (see
`support.servers`), because the UUID branch needs a real id and a `choices` entry has to be
built from the object production would build it from.

Proxmox's node-suffix retry is its own and gets its own section at the bottom — WHM and PMG
would inherit a naming convention that is not theirs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from core.servers.reference import (
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    resolve_pmg_server_ref,
    resolve_proxmox_server_ref,
    resolve_whm_server_ref,
)
from support.servers import (
    API_TOKEN,
    PROXMOX_API_TOKEN_SECRET,
    SSH_PASSWORD,
    FakePMGServerRepository,
    FakeProxmoxServerRepository,
    FakeWHMServerRepository,
    pmg_server,
    proxmox_server,
    whm_server,
)


@dataclass(frozen=True)
class System:
    """One vertical's binding of the shared policy."""

    subject: str  # the noun in every message
    resolve: Callable[..., Any]  # the wrapper under test
    repository: Callable[[list[Any]], Any]  # the in-memory double
    row: Callable[[str, str], Any]  # (name, host) -> a row whose host is `host`
    host_field: str  # the `choices` key an operator recognises
    secret: str  # ciphertext a `choices` entry must never carry


WHM = System(
    subject="WHM",
    resolve=resolve_whm_server_ref,
    repository=FakeWHMServerRepository,
    row=lambda name, host: whm_server(name, base_url=f"https://{host}:2087"),
    host_field="base_url",
    secret=API_TOKEN,
)
PMG = System(
    subject="PMG",
    resolve=resolve_pmg_server_ref,
    repository=FakePMGServerRepository,
    row=lambda name, host: pmg_server(name, ssh_host=host),
    host_field="ssh_host",
    secret=SSH_PASSWORD,
)
PROXMOX = System(
    subject="Proxmox",
    resolve=resolve_proxmox_server_ref,
    repository=FakeProxmoxServerRepository,
    row=lambda name, host: proxmox_server(name, base_url=f"https://{host}:8006"),
    host_field="base_url",
    secret=PROXMOX_API_TOKEN_SECRET,
)

SYSTEMS = pytest.mark.parametrize("system", [WHM, PMG, PROXMOX], ids=lambda system: system.subject)


# --- The three ways a reference resolves, in order ---


@SYSTEMS
async def test_an_id_resolves_through_a_direct_read(system: System) -> None:
    """The unambiguous form, and what a `choices` list tells the caller to come back with."""
    wanted = system.row("alpha", "alpha.example.net")
    repository = system.repository([wanted, system.row("bravo", "bravo.example.net")])

    resolution = await system.resolve(str(wanted.id), repository=repository)

    assert resolution.ok
    assert resolution.server is wanted


@SYSTEMS
async def test_a_name_resolves_case_insensitively(system: System) -> None:
    """An operator typing `NODE1` means the server named `node1`."""
    wanted = system.row("node1", "node1.example.net")
    repository = system.repository([wanted, system.row("bravo", "bravo.example.net")])

    resolution = await system.resolve("NODE1", repository=repository)

    assert resolution.server is wanted


@SYSTEMS
async def test_a_host_resolves_when_no_name_matches(system: System) -> None:
    """The third form: the machine's host, not the label NOA stores.

    Operators read hostnames off tickets and monitoring, so the reference they have is often the
    machine's. WHM and Proxmox parse it out of `base_url` — where the port is part of the URL and
    not part of the host, the case a naive string comparison gets wrong — while PMG reads the
    bare `ssh_host` column.
    """
    wanted = system.row("alpha", "h1.example.net")
    repository = system.repository([wanted, system.row("bravo", "bravo.example.net")])

    resolution = await system.resolve("H1.example.net", repository=repository)

    assert resolution.server is wanted


@SYSTEMS
async def test_a_name_wins_over_another_servers_host(system: System) -> None:
    """Order is a decision, not an accident.

    A server deliberately named `h1.example.net` beats a *different* server that merely lives at
    that host: the name was typed into NOA on purpose, the host is derived from a connection
    field. Without the fixed order this input would be a coin flip between two real servers.
    """
    named = system.row("h1.example.net", "alpha.example.net")
    hosted = system.row("bravo", "h1.example.net")
    repository = system.repository([named, hosted])

    resolution = await system.resolve("h1.example.net", repository=repository)

    assert resolution.server is named


# --- A tie returns candidates, never a pick ---


@SYSTEMS
async def test_a_name_tie_answers_choices_rather_than_a_pick(system: System) -> None:
    """The case that matters: `Node1` and `node1` both match `NODE1`.

    A resolver that picked one would route a CHANGE to whichever row the `ORDER BY` happened to
    put first.
    """
    repository = system.repository(
        [system.row("Node1", "one.example.net"), system.row("node1", "two.example.net")]
    )

    resolution = await system.resolve("NODE1", repository=repository)

    assert resolution.ok is False
    assert resolution.server is None
    assert resolution.error_code == ERROR_AMBIGUOUS
    assert len(resolution.choices) == 2


@SYSTEMS
async def test_a_host_tie_answers_choices(system: System) -> None:
    """Two rows can name one machine — the same host entered twice under different names."""
    shared = "shared.example.net"
    repository = system.repository([system.row("alpha", shared), system.row("bravo", shared)])

    resolution = await system.resolve(shared, repository=repository)

    assert resolution.error_code == ERROR_AMBIGUOUS
    assert [shared in choice[system.host_field] for choice in resolution.choices] == [True, True]


@SYSTEMS
async def test_choices_are_capped(system: System) -> None:
    """A tie between forty servers must not paste forty rows into the transcript.

    Summary-plus-URL is the answer for genuinely large results; a refusal is not the place for
    one.
    """
    repository = system.repository(
        [system.row("node1", f"h{index}.example.net") for index in range(MAX_CHOICES + 2)]
    )

    resolution = await system.resolve("node1", repository=repository)

    assert resolution.error_code == ERROR_AMBIGUOUS
    assert len(resolution.choices) == MAX_CHOICES


@SYSTEMS
async def test_a_choice_names_the_server_without_a_credential(system: System) -> None:
    """`choices` is transcript: id, name and the host field, nothing else.

    No API token, no SSH credential, and for Proxmox no `api_token_id` either — it is not a
    credential on its own, but it names the user NOA authenticates as and this text goes into a
    LibreChat transcript a LibreChat administrator can read.
    """
    shared = "shared.example.net"
    repository = system.repository([system.row("alpha", shared), system.row("bravo", shared)])

    resolution = await system.resolve(shared, repository=repository)

    assert resolution.choices
    for choice in resolution.choices:
        assert set(choice) == {"id", "name", system.host_field}
        assert system.secret not in choice.values()


# --- Refusals ---


@SYSTEMS
async def test_an_unmatched_reference_is_not_found_rather_than_the_only_server(
    system: System,
) -> None:
    """One server does not make it the answer.

    The tempting shortcut — "there is only one server, so they must mean that one" — is exactly
    the guess the ambiguous-identifier rule forbids, and it is most dangerous in the deployment
    where it looks safest, because a second server appearing later silently changes behaviour.
    """
    repository = system.repository([system.row("alpha", "alpha.example.net")])

    resolution = await system.resolve("gamma", repository=repository)

    assert resolution.ok is False
    assert resolution.server is None
    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.choices == []


@SYSTEMS
async def test_a_well_formed_id_that_matches_nothing_stops_at_not_found(system: System) -> None:
    """An id does not fall through to the name and host passes.

    A mistyped id must not resolve to a different server whose *name* happens to be that string —
    ruling that out is the whole reason to accept an id.
    """
    missing = uuid4()
    repository = system.repository([system.row(str(missing), "alpha.example.net")])

    resolution = await system.resolve(str(missing), repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_NOT_FOUND
    assert str(missing) in resolution.message


@SYSTEMS
async def test_a_whitespace_only_reference_is_refused(system: System) -> None:
    """A required string that is only whitespace is a bad call, not a broad search.

    `reads == 0` is the part worth asserting: the guard runs before inventory is touched, so a
    malformed call costs no query.
    """
    repository = system.repository([system.row("alpha", "alpha.example.net")])

    resolution = await system.resolve("   ", repository=repository)

    assert resolution.ok is False
    assert resolution.error_code == ERROR_REQUIRED
    assert resolution.message == f"{system.subject} server reference is required"
    assert repository.reads == 0


@SYSTEMS
async def test_a_reference_is_trimmed_before_matching(system: System) -> None:
    """A pasted trailing newline is an operator artifact, not an unknown server."""
    wanted = system.row("node1", "node1.example.net")
    repository = system.repository([wanted])

    resolution = await system.resolve("  node1  ", repository=repository)

    assert resolution.server is wanted


@SYSTEMS
async def test_an_empty_inventory_is_not_found(system: System) -> None:
    """No servers configured is a refusal the model can act on, not an exception."""
    resolution = await system.resolve("node1", repository=system.repository([]))

    assert resolution.error_code == ERROR_NOT_FOUND
    assert resolution.server is None


@SYSTEMS
async def test_a_refusal_names_its_own_system_and_no_other(system: System) -> None:
    """The shared resolver takes the subject noun as a parameter, so this is what says each
    adapter passed its own — a Proxmox refusal reading "WHM server reference is required" would
    send an operator to the wrong inventory, the cost if the extraction were careless."""
    others = [other.subject for other in (WHM, PMG, PROXMOX) if other is not system]
    repository = system.repository([])

    blank = await system.resolve("", repository=repository)
    missing = await system.resolve("nope", repository=repository)

    for message in (blank.message, missing.message):
        assert system.subject in message
        assert not [other for other in others if other in message]


# --- Proxmox: node names ---
#
# The operator pastes a VM lookup and sends its `Host Node` as both `node` and `server_ref`, so a
# reference that matches nothing and ends in a node suffix is tried once more as its cluster.
# These six bind the retry's shape; a seventh case needs no test of its own, because a blank
# reference never reaches the retry and `test_a_whitespace_only_reference_is_refused` above
# already pins that — it asserts `host_required`, which is the code the retry cannot produce.
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
