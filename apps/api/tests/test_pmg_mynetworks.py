"""`mynetworks` normalisation and line parsing (T31, §V.58, §V.59).

§V.59 is one rule applied twice — `1.2.3.4` ≡ `1.2.3.4/32` — and it has to hold on *both* sides
of a comparison, so this file exercises the normaliser directly rather than only through the
tool: the operator's target and every stored line go through the same function, and a rule that
held on one side would report "not whitelisted" for an address PMG whitelists.

The parser's real job is the noise. `pmgsh ls` interleaves a `200 OK` status line and an
`id cidr` header with the rows, so "finds the entries" and "produces none for anything else"
are the same property asserted from two directions.

Two departures from `noa-old` get their own cases, because both are silent when wrong: a line
whose candidate was already seen must not fall through to the id column, and a repeated CIDR
must survive rather than being deduplicated away.
"""

from __future__ import annotations

import pytest

from core.integrations.pmg.mynetworks import (
    MynetworksEntry,
    find_matching_entries,
    normalize_cidr,
    parse_mynetworks_entries,
    sort_entries,
)
from support.pmg import mynetworks_output

# --- V59: one host and its host route are one entry ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1.2.3.4", "1.2.3.4/32", id="ipv4-host"),
        pytest.param("1.2.3.4/32", "1.2.3.4/32", id="ipv4-host-route"),
        pytest.param("2001:db8::1", "2001:db8::1/128", id="ipv6-host"),
        pytest.param("2001:db8::1/128", "2001:db8::1/128", id="ipv6-host-route"),
    ],
)
def test_a_bare_address_normalizes_to_its_host_route(value: str, expected: str) -> None:
    """§V.59's exact words. Without this, membership is a string comparison against whichever
    spelling PMG happens to store."""
    assert normalize_cidr(value) == expected


def test_a_network_keeps_its_prefix() -> None:
    assert normalize_cidr("10.10.10.0/24") == "10.10.10.0/24"


def test_host_bits_are_masked_off_a_network() -> None:
    """`strict=False`, the ported behaviour — and the reason a caller reports the normalised
    form beside its verdict instead of echoing what it was handed."""
    assert normalize_cidr("1.2.3.4/24") == "1.2.3.0/24"


def test_surrounding_whitespace_is_trimmed() -> None:
    assert normalize_cidr("  1.2.3.4  ") == "1.2.3.4/32"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="whitespace"),
        pytest.param("not-a-cidr", id="word"),
        pytest.param("1.2.3.4/33", id="prefix-out-of-range"),
        pytest.param("1.2.3.256", id="octet-out-of-range"),
        pytest.param("example.com", id="hostname"),
        pytest.param("200", id="bare-decimal"),
    ],
)
def test_anything_that_is_not_an_address_or_network_is_none(value: str) -> None:
    """`None`, never a raise: the callers are a tool refusing an argument (§V.18, §V.19) and a
    parser skipping a line that was never meant to be an address.

    `"200"` is in here on purpose — it is the status line `pmgsh` prints, and `ipaddress`
    accepting a bare decimal would put `0.0.0.200/32` in the whitelist.
    """
    assert normalize_cidr(value) is None


# --- Reading `pmgsh ls` output ---


def test_entries_are_read_from_the_second_column() -> None:
    """`<id> <cidr>` rows, which is what `pmgsh ls` prints."""
    entries = parse_mynetworks_entries(mynetworks_output("10.10.10.0/24", "1.2.3.4/32"))

    assert [entry.normalized for entry in entries] == ["10.10.10.0/24", "1.2.3.4/32"]


def test_the_status_line_and_the_header_produce_no_entries() -> None:
    """The property that makes the parser safe rather than lucky: nothing in `200 OK` or
    `id cidr` parses, so no phantom entry is manufactured from the framing."""
    assert parse_mynetworks_entries("200 OK\nid cidr\n") == []


def test_a_line_that_is_not_an_address_is_skipped() -> None:
    entries = parse_mynetworks_entries("200 OK\nid cidr\n1 10.10.10.0/24\ninvalid not-a-cidr\n")

    assert [entry.normalized for entry in entries] == ["10.10.10.0/24"]


def test_a_bare_cidr_line_is_read_from_the_first_column() -> None:
    """The fallback `noa-old` carried: a variant of the output with no id column."""
    entries = parse_mynetworks_entries("10.10.10.0/24\n")

    assert [entry.normalized for entry in entries] == ["10.10.10.0/24"]


def test_the_raw_spelling_travels_beside_the_normalized_form() -> None:
    """What an operator sees on the box. A bare host in `mynetworks` normalises to a /32, and
    only the raw form says which of the two is actually in the file."""
    [entry] = parse_mynetworks_entries("1 1.2.3.4\n")

    assert entry == MynetworksEntry(cidr="1.2.3.4", normalized="1.2.3.4/32")


def test_entries_keep_the_order_pmg_printed() -> None:
    """Not re-sorted: the caller answers membership, and an entry's position is the one thing
    this layer cannot improve on."""
    entries = parse_mynetworks_entries(mynetworks_output("10.0.0.0/8", "1.2.3.4/32", "9.9.9.9/32"))

    assert [entry.cidr for entry in entries] == ["10.0.0.0/8", "1.2.3.4/32", "9.9.9.9/32"]


def test_an_empty_output_yields_no_entries() -> None:
    assert parse_mynetworks_entries("") == []


# --- The two departures from `noa-old` ---


def test_a_repeated_cidr_is_not_deduplicated_away() -> None:
    """`noa-old` collapsed duplicates while parsing. Two spellings of one address in
    `mynetworks` is a fact about the whitelist, and a search that drops one reports a list its
    own source does not have."""
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.4/32", "1.2.3.4"))

    assert [entry.cidr for entry in entries] == ["1.2.3.4/32", "1.2.3.4"]
    assert {entry.normalized for entry in entries} == {"1.2.3.4/32"}


def test_a_repeated_cidr_never_makes_the_id_column_stand_in_for_the_address() -> None:
    """The `noa-old` defect not carried forward (T21 (b)).

    There a candidate already in the `seen` set was skipped and the *next column* was tried, so
    a repeated CIDR could put the id column's value in the entry list. Here the first parsable
    candidate ends the line.

    The id column is a decimal — which `ipaddress` rejects — so the mutation this guards against
    is not "wrong entry" but "the fall-through exists at all": every entry has to come from the
    column that held a CIDR.
    """
    entries = parse_mynetworks_entries("1 1.2.3.4/32\n10.0.0.9 1.2.3.4/32\n")

    assert [entry.cidr for entry in entries] == ["1.2.3.4/32", "1.2.3.4/32"]


# --- V85: a listing's order, which parsing deliberately does not impose ---


def test_entries_sort_by_address_rather_than_by_text() -> None:
    """§V.85's ordering clause, for `pmg_whitelist_list` (T30).

    `10.9.0.0/24` belongs before `10.10.0.0/24`, and sorting the strings puts it after. That is
    the whole reason this is a key rather than `sorted(entries, key=lambda e: e.normalized)`.
    """
    entries = parse_mynetworks_entries(mynetworks_output("10.10.0.0/24", "10.9.0.0/24"))

    assert [entry.cidr for entry in sort_entries(entries)] == ["10.9.0.0/24", "10.10.0.0/24"]


def test_a_network_sorts_before_a_host_route_inside_it() -> None:
    """Same address, two prefixes: the tie breaks on the prefix length, widest first, so a
    supernet and the hosts under it read down the page in the order an operator expects."""
    entries = parse_mynetworks_entries(mynetworks_output("10.0.0.0/32", "10.0.0.0/8"))

    assert [entry.cidr for entry in sort_entries(entries)] == ["10.0.0.0/8", "10.0.0.0/32"]


def test_mixed_families_sort_without_raising() -> None:
    """An `IPv4Network` and an `IPv6Network` do not compare, so a key that sorted the objects
    themselves would raise on any whitelist holding both — and `mynetworks` routinely does.
    IPv4 leads, because the family is the first element of the key."""
    entries = parse_mynetworks_entries(
        mynetworks_output("2001:db8::/32", "10.0.0.0/8", "2001:db8::1")
    )

    assert [entry.cidr for entry in sort_entries(entries)] == [
        "10.0.0.0/8",
        "2001:db8::/32",
        "2001:db8::1",
    ]


def test_sorting_keeps_both_spellings_of_a_repeated_entry() -> None:
    """The cut a table applies is a prefix, so dropping a duplicate here would drop a line
    `mynetworks` really holds — and the raw spelling breaks the last tie, so the pair has a
    defined order rather than the input's."""
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.4/32", "1.2.3.4"))

    assert [entry.cidr for entry in sort_entries(entries)] == ["1.2.3.4", "1.2.3.4/32"]


def test_sorting_an_empty_whitelist_is_an_empty_list() -> None:
    assert sort_entries([]) == []


# --- V59: exact membership, never containment ---


def test_a_target_matches_its_own_entry() -> None:
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.4/32", "10.10.10.0/24"))

    matches = find_matching_entries(entries, normalized_target="1.2.3.4/32")

    assert [entry.cidr for entry in matches] == ["1.2.3.4/32"]


def test_a_containing_network_is_not_a_match() -> None:
    """§V.59 says exact CIDR membership. Answering otherwise tells an operator their address is
    whitelisted when the entry a removal would have to name is a different CIDR."""
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.0/24"))

    assert find_matching_entries(entries, normalized_target="1.2.3.4/32") == []


def test_a_contained_host_is_not_a_match_for_its_network() -> None:
    """The other direction, and the reason this is not a subnet test in disguise."""
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.4/32"))

    assert find_matching_entries(entries, normalized_target="1.2.3.0/24") == []


def test_every_spelling_of_one_address_matches() -> None:
    """The point of normalising both sides: PMG's spelling and the operator's need not agree."""
    entries = parse_mynetworks_entries(mynetworks_output("1.2.3.4"))

    matches = find_matching_entries(entries, normalized_target=normalize_cidr("1.2.3.4/32") or "")

    assert [entry.cidr for entry in matches] == ["1.2.3.4"]
