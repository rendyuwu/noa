"""CSF target classification and `csf -g` parsing.

Ported from `noa-old` branch `MCP` (`test_whm_csf_parsing.py`) with the HTML-path cases dropped
— that render path is not ported (see `core.integrations.whm.csf`) — and the target-kind case
widened to cover every branch, because `parse_csf_target` is the classifier the IPv4-only
CHANGE rule is enforced with: the CHANGE tools reject anything that is not `ip`.

The grep fixtures are real `csf -g` output shapes, not invented ones. That matters: the
verdict is read from marker strings in human-facing text, so a fixture that does not look like
the box's output tests nothing.
"""

from __future__ import annotations

import pytest

from core.integrations.whm.csf import parse_csf_grep_output, parse_csf_target

# --- target classification ---


@pytest.mark.parametrize(
    ("raw", "kind", "field", "value"),
    [
        ("1.2.3.4", "ip", "ip", "1.2.3.4"),
        ("1.2.3.0/24", "cidr", "cidr", "1.2.3.0/24"),
        # `strict=False`: csf accepts a host bit set, and normalising to the network is what
        # the operator meant.
        ("1.2.3.4/24", "cidr", "cidr", "1.2.3.0/24"),
        ("2001:db8::1", "ipv6", "ip", "2001:db8::1"),
        ("2001:db8::/32", "ipv6_cidr", "cidr", "2001:db8::/32"),
        ("app-01.example.com", "hostname", "hostname", "app-01.example.com"),
    ],
)
def test_parse_csf_target_classifies_ipv4_cidr_ipv6_hostname_unknown(
    raw: str, kind: str, field: str, value: str
) -> None:
    """Every kind the CHANGE tools branch on gets its own answer, never a guess."""
    target = parse_csf_target(raw)

    assert target.kind == kind
    assert getattr(target, field) == value
    assert target.raw == raw


@pytest.mark.parametrize("raw", ["bad_target", "999.999.999.999", "-leading-dash.com", "nodot"])
def test_parse_csf_target_returns_unknown_rather_than_guessing(raw: str) -> None:
    """`unknown` is a verdict. `999.999.999.999` is label-legal but has no letter, so it is
    never classified as a hostname and never reaches DNS."""
    assert parse_csf_target(raw).kind == "unknown"


def test_parse_csf_target_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        parse_csf_target("   ")


def test_parse_csf_target_does_not_normalize_a_bare_address_to_a_cidr() -> None:
    """`1.2.3.4` stays `ip`. Turning it into `1.2.3.4/32` would erase the ip/cidr distinction."""
    target = parse_csf_target("1.2.3.4")

    assert target.kind == "ip"
    assert target.cidr is None


# --- verdicts ---


def test_parse_csf_grep_output_detects_temporary_allow() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination

filter ALLOWIN          1        0     0 ACCEPT     all  --  ens192 *       203.0.113.10         0.0.0.0/0

filter ALLOWOUT         1        0     0 ACCEPT     all  --  *      ens192  0.0.0.0/0            203.0.113.10


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables

Temporary Allows: IP:203.0.113.10 Port: Dir:inout TTL:600 (NOA ttl allow test)
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "allowlisted"
    assert any("Temporary Allows:" in match for match in parsed.matches)


def test_parse_csf_grep_output_detects_temporary_block() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination

filter DENYIN           69       0     0 DROP       all  --  ens192 *       203.0.113.10         0.0.0.0/0


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables

Temporary Blocks: IP:203.0.113.10 Port: Dir:in TTL:600 (NOA ttl deny test)
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "blocked"
    assert any("Temporary Blocks:" in match for match in parsed.matches)


def test_parse_csf_grep_output_detects_not_found() -> None:
    output = """
Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in iptables


ip6tables:

Table  Chain            num   pkts bytes target     prot opt in     out     source               destination
No matches found for 203.0.113.10 in ip6tables
"""

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "not_found"
    assert parsed.matches == [
        "No matches found for 203.0.113.10 in iptables",
        "No matches found for 203.0.113.10 in ip6tables",
    ]


def test_blocked_beats_allowlisted_when_the_ip_is_in_both_lists() -> None:
    """The release-and-allow tool releases *and* allows in one action, so the intermediate state is real: an IP in
    `csf.deny` and `csf.allow` at once is, operationally, still blocked."""
    output = "Found 203.0.113.10 in /etc/csf/csf.allow\nFound 203.0.113.10 in /etc/csf/csf.deny\n"

    assert parse_csf_grep_output(output, target="203.0.113.10").verdict == "blocked"


def test_an_allow_entry_is_reported_even_when_a_block_outranks_it() -> None:
    """The precedence above is what makes `allow_entry` a second field rather than a
    re-reading of the first.

    "What is this box doing to this address" and "is there still an allow entry for it" are two
    questions, and an allowlist removal's postflight asks the second. Read off the verdict, a
    removal that left `csf.allow` untouched on an address csf also denies would report as done —
    the deny entry would be answering for it.
    """
    output = "Found 203.0.113.10 in /etc/csf/csf.allow\nFound 203.0.113.10 in /etc/csf/csf.deny\n"

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "blocked"
    assert parsed.allow_entry is True


def test_no_allow_entry_is_reported_when_only_a_block_matches() -> None:
    """The negative control: without it, "an allow entry is reported" passes just as well
    against a parser that reports one for every line it sees."""
    output = "Found 203.0.113.10 in /etc/csf/csf.deny\n"

    parsed = parse_csf_grep_output(output, target="203.0.113.10")

    assert parsed.verdict == "blocked"
    assert parsed.allow_entry is False


def test_an_unparseable_answer_reports_no_allow_entry() -> None:
    """`unknown` carries `allow_entry: False`, and that pairing is only safe because a caller
    has to check `answered` first.

    A backend that said nothing recognisable has not said there is no allow entry. The
    field cannot express that on its own, so `holds_allow_entry` skips unanswered backends and
    every caller checks `unanswered_backends` before trusting an absence.
    """
    parsed = parse_csf_grep_output(
        "203.0.113.10 in some shape we have no marker for\n", target="203.0.113.10"
    )

    assert parsed.verdict == "unknown"
    assert parsed.allow_entry is False


def test_unrecognised_output_is_unknown_not_not_found() -> None:
    """A parse regression must never read as a clean IP — `not_found` needs csf to have said so."""
    output = "203.0.113.10 appears somewhere we do not have a marker for\n"

    assert parse_csf_grep_output(output, target="203.0.113.10").verdict == "unknown"


def test_matches_are_bounded_and_the_total_is_reported() -> None:
    """A busy box greps hundreds of log lines; the result is headed for an LLM context.

    The row cap's own bound: the cut happens here, so the count before it is reported here.
    Twenty lines with no
    other signal read as "there are twenty entries" — a fabrication the tool would be handing
    the model rather than one the model invented (the firewall preflight is the caller that states it).
    """
    lines = ["Found \x1b[31m1.2.3.4\x1b[0m in /etc/csf/csf.deny"]
    lines += [f"Log entry {index} 1.2.3.4" for index in range(50)]

    parsed = parse_csf_grep_output("\n".join(lines), target="1.2.3.4")

    assert parsed.verdict == "blocked"
    assert len(parsed.matches) == 20
    assert parsed.total_matches == 51
    assert "\x1b[" not in parsed.matches[0]


def test_an_uncut_result_reports_its_own_length_as_the_total() -> None:
    """The other side of the truncation rule: `total_matches` is never a constant, and never only meaningful when
    the list was cut. A caller compares the two to decide whether to say "truncated"."""
    output = "Found 1.2.3.4 in /etc/csf/csf.deny\nTemporary Blocks: IP:1.2.3.4 Port: Dir:in\n"

    parsed = parse_csf_grep_output(output, target="1.2.3.4")

    assert len(parsed.matches) == 2
    assert parsed.total_matches == 2


def test_the_kept_evidence_is_the_prefix_of_csf_s_own_order() -> None:
    """The cap rule asks for a stable ordering before the cut; csf's own is it.

    `csf -g` renders the current tables and files, so identical calls against unchanged state
    yield an identical prefix — unlike `listaccts`, the tool the stable-ordering rule was written at. Sorting the
    lines would also scramble the deny/allow grouping that makes the evidence readable.
    """
    lines = [f"Found 1.2.3.4 in /etc/csf/csf.deny entry {index:02d}" for index in range(30)]

    parsed = parse_csf_grep_output("\n".join(lines), target="1.2.3.4")

    assert parsed.matches == lines[:20]


def test_parse_csf_grep_output_requires_a_target() -> None:
    with pytest.raises(ValueError):
        parse_csf_grep_output("anything", target="  ")
