"""Dependency-pin guards (C1, C23).

These pins are load-bearing decisions, not incidental versions. A drive-by bump
should fail here and force a deliberate re-verification.
"""

import sys
import tomllib
from pathlib import Path

from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import LATEST_PROTOCOL_VERSION

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project() -> dict:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_python_upper_bound_excludes_3_13() -> None:
    """C1: pgpy 0.6.0 imports the removed `imghdr` module on 3.13+."""
    assert _project()["requires-python"] == ">=3.11,<3.13"


def test_runtime_python_within_supported_range() -> None:
    assert (3, 11) <= sys.version_info[:2] < (3, 13)


def test_fastmcp_pinned_to_3_4_5() -> None:
    """C23: handshake era, negotiated per client; fastmcp 4.x is beta and breaks T11."""
    deps = _project()["dependencies"]

    assert "fastmcp==3.4.5" in deps


def test_server_serves_every_era_a_v1_client_can_ask_for() -> None:
    """C23 as corrected by T71: the pin is a *set*, and the set is what must hold.

    LibreChat sends its SDK's `LATEST_PROTOCOL_VERSION` and nothing else (T71: exact-locked
    `@modelcontextprotocol/sdk` 1.29.0, stock `Client`, no version knob), so a bump on the
    client side lands here as an era NOA has never been asked for. This asserts both ends of
    C23: today's client era negotiates, and the sessionless `2026-07-28` era C23 rejected is
    still absent — reaching it means bumping the SDK, not flipping a setting.
    """
    assert {"2025-06-18", "2025-11-25"} <= set(SUPPORTED_PROTOCOL_VERSIONS)
    assert "2026-07-28" not in SUPPORTED_PROTOCOL_VERSIONS
    assert LATEST_PROTOCOL_VERSION == "2025-11-25"


def test_all_dependencies_exactly_pinned() -> None:
    """No caret/range specifiers — same discipline as C2 for the web apps."""
    deps = _project()["dependencies"]
    unpinned = [dep for dep in deps if dep != "noa-core" and "==" not in dep]

    assert unpinned == []
