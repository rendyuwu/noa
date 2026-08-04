"""Dependency-pin guards (C1, C23).

These pins are load-bearing decisions, not incidental versions. A drive-by bump
should fail here and force a deliberate re-verification.
"""

import sys
import tomllib
from pathlib import Path

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
    """C23: handshake era 2025-06-18; fastmcp 4.x is beta and breaks T11."""
    deps = _project()["dependencies"]

    assert "fastmcp==3.4.5" in deps


def test_all_dependencies_exactly_pinned() -> None:
    """No caret/range specifiers — same discipline as C2 for the web apps."""
    deps = _project()["dependencies"]
    unpinned = [dep for dep in deps if dep != "noa-core" and "==" not in dep]

    assert unpinned == []
