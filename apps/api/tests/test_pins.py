"""Dependency-pin guards.

These pins are load-bearing decisions, not incidental versions. A drive-by bump
should fail here and force a deliberate re-verification.
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import LATEST_PROTOCOL_VERSION

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
REPO_ROOT = Path(__file__).resolve().parents[3]

# The era C23 rejected. Absent from both SDKs at 1.29.0, so the docs may name it —
# they just may not claim NOA serves it.
REJECTED_ERA = "2026-07-28"

# `2025-11-25`, not 2025-11-25: only era strings wear backticks in these files, so a
# verification date can never be mistaken for a protocol version.
QUOTED_DATE = re.compile(r"`(\d{4}-\d{2}-\d{2})`")


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


@pytest.mark.parametrize("doc_name", ["ARCHITECTURE.md", "README.md"])
def test_pin_docs_track_the_servable_set(doc_name: str) -> None:
    """T70: the prose about the pin is bound to the SDK, not written once and trusted.

    The `2025-06-18`-as-the-era claim survived in four files because nothing compared it
    to the SDK. This closes that: both directions are asserted, so widening the
    supported set leaves the docs incomplete and narrowing it leaves them wrong, and
    either way a bump has to stop and edit the prose.

    Convention this depends on, in these two files only: **backticks mean protocol era**.
    Write verification dates bare (2026-08-07), or they get read as versions.
    """
    doc = (REPO_ROOT / doc_name).read_text(encoding="utf-8")

    quoted_eras = set(QUOTED_DATE.findall(doc))
    servable = set(SUPPORTED_PROTOCOL_VERSIONS)

    assert servable <= quoted_eras, "docs omit an era the server now answers"
    assert quoted_eras - servable <= {REJECTED_ERA}, "docs quote an era the server cannot serve"


def test_architecture_doc_records_the_pin_and_its_re_open_trigger() -> None:
    """T70: the pin reads as a decision with an expiry condition, not as an accident."""
    doc = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    assert f"`{LATEST_PROTOCOL_VERSION}`" in doc
    assert "`fastmcp==3.4.5`" in doc
    # The trigger itself: a v2 client is the one that can ask for something else.
    assert "@modelcontextprotocol/sdk" in doc
    assert "v2" in doc


def test_all_dependencies_exactly_pinned() -> None:
    """No caret/range specifiers — same discipline as C2 for the web apps."""
    deps = _project()["dependencies"]
    unpinned = [dep for dep in deps if dep != "noa-core" and "==" not in dep]

    assert unpinned == []
