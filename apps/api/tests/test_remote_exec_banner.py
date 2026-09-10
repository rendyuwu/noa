"""SSH banner stripping.

Ported with the implementation from `noa-old` branch `MCP`. Half these cases guard
*against* stripping: the removal is signature-gated, so legitimate boxed output — a build
report, a table of stars — must survive byte-exact. `noa-old` GH #82/#83 is the incident that
bought the LVE case; the false-positive cases are what keep the fix from eating real output.
"""

from __future__ import annotations

from core.remote_exec.banner_strip import strip_ssh_banners

_LVE_BANNER = (
    "***************************************************************************\n"
    "*             !!!!  WARNING: YOU ARE INSIDE LVE !!!!                      *\n"
    "*IF YOU RESTART ANY SERVICES STABILITY OF YOUR SYSTEM WILL BE COMPROMIZED *\n"
    "*        CHANGE YOUR USER'S GROUP TO wheel to safely use SU/SUDO          *\n"
    "*                             MORE INFO:                                  *\n"
    "*      http://docs.cloudlinux.com/index.html?lve_pam_module.html          *\n"
    "***************************************************************************"
)


def test_strip_banner_then_json_payload() -> None:
    """The incident case: Imunify `--json` output behind an LVE banner."""
    payload = '{"max_count": 0, "items": []}'
    text = f"{_LVE_BANNER}\n{payload}"
    assert strip_ssh_banners(text) == payload


def test_strip_banner_then_csf_grep_lines() -> None:
    grep = "Temporary Blocks: IP:1.2.3.4 Port: Dir:in TTL:432000\ncsf.deny: 1.2.3.4 # manual"
    text = f"{_LVE_BANNER}\n{grep}"
    assert strip_ssh_banners(text) == grep


def test_strip_banner_only_returns_blank() -> None:
    assert strip_ssh_banners(_LVE_BANNER).strip() == ""


def test_no_banner_is_byte_exact_identity() -> None:
    """Untouched output returns the *same object* — no re-joining, no normalisation."""
    payload = '{"ok": true}'
    out = strip_ssh_banners(payload)
    assert out is payload


def test_false_positive_guard_preserves_crlf_and_trailing_newline() -> None:
    # Legit boxed output WITHOUT the LVE signature, plus a star separator line.
    legit = (
        "*** Build report ***\r\n"
        "all green\r\n"
        "********************\r\n"
        "* footer note      *\r\n"
        "********************\r\n"
    )
    out = strip_ssh_banners(legit)
    assert out is legit  # byte-exact, no CRLF/trailing-newline normalisation


def test_border_with_trailing_spaces_still_stripped() -> None:
    payload = '{"v": 1}'
    banner = (
        "***************************************************************   \n"
        "*   WARNING: YOU ARE INSIDE LVE   *\n"
        "***************************************************************\t\n"
    )
    assert strip_ssh_banners(f"{banner}{payload}") == payload


def test_banner_mid_output_keeps_surrounding_payload() -> None:
    text = f"before line\n{_LVE_BANNER}\nafter line"
    assert strip_ssh_banners(text) == "before line\nafter line"


def test_multiple_banners_all_stripped() -> None:
    payload = '{"ok": true}'
    text = f"{_LVE_BANNER}\n{_LVE_BANNER}\n{payload}"
    assert strip_ssh_banners(text) == payload


def test_unterminated_banner_kept_verbatim() -> None:
    """No closing border ⇒ the block is not a banner we recognise. Keep everything."""
    text = (
        "***************************************************************\n"
        "*   WARNING: YOU ARE INSIDE LVE   *\n"
        "no closing border here\n"
        "more output"
    )
    out = strip_ssh_banners(text)
    assert out is text


def test_fast_path_no_asterisk_identity() -> None:
    payload = "plain output without stars"
    assert strip_ssh_banners(payload) is payload
