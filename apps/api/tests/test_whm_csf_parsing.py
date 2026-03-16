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
