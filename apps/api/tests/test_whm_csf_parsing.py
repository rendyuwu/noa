from __future__ import annotations


def test_parse_csf_target_ip() -> None:
    from noa_api.whm.integrations.csf import parse_csf_target

    target = parse_csf_target("1.2.3.4")
    assert target.kind == "ip"
    assert target.ip == "1.2.3.4"


def test_parse_csf_target_cidr() -> None:
    from noa_api.whm.integrations.csf import parse_csf_target

    target = parse_csf_target("1.2.3.0/24")
    assert target.kind == "cidr"
    assert target.cidr == "1.2.3.0/24"


def test_parse_csf_target_hostname() -> None:
    from noa_api.whm.integrations.csf import parse_csf_target

    target = parse_csf_target("app-01.example.com")
    assert target.kind == "hostname"
    assert target.hostname == "app-01.example.com"


def test_parse_csf_target_rejects_invalid_hostname() -> None:
    from noa_api.whm.integrations.csf import parse_csf_target

    target = parse_csf_target("bad_target")
    assert target.kind == "unknown"


def test_parse_csf_grep_html_returns_verdict_and_bounded_matches() -> None:
    from noa_api.whm.integrations.csf import parse_csf_grep_html

    lines = [
        "Found 1.2.3.4 in /etc/csf/csf.deny",
    ] + [f"Log entry {i} 1.2.3.4" for i in range(50)]
    html = "<html><body><pre>" + "\n".join(lines) + "</pre></body></html>"

    parsed = parse_csf_grep_html(html, target="1.2.3.4")
    assert parsed.verdict == "blocked"
    assert len(parsed.matches) <= 20
    assert any("csf.deny" in m for m in parsed.matches)


def test_parse_csf_grep_output_detects_temporary_allow() -> None:
    from noa_api.whm.integrations.csf import parse_csf_grep_output

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
    from noa_api.whm.integrations.csf import parse_csf_grep_output

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
    from noa_api.whm.integrations.csf import parse_csf_grep_output

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
