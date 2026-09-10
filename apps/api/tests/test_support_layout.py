"""One home per test double.

The SSH transport doubles and `build_cipher` were born in `support/whm.py` and moved out
as soon as a second caller appeared — `support/remote_exec.py` for PMG,
`support/secrets.py` for Proxmox. Both moves left a re-export behind so the four WHM
test files could stay untouched, which meant nine shared names had two import paths at once.
The shims are gone now.

Nothing stops the next port from re-adding one, and a re-export is invisible at the call site:
`from support.whm import command_result` reads exactly like a WHM-owned double. So the property
is asserted rather than left to discipline — grep the claim, not the citation, the same reason
`test_pins.py` binds the pin prose to the SDK.
"""

from __future__ import annotations

import ast
from pathlib import Path

import support.whm

TESTS_ROOT = Path(__file__).resolve().parent

# `support/whm.py` still imports these two — `FakeWHMServer` uses them as field defaults, so
# they are a real use and remain module attributes. `__all__` is the surface that matters.
WHM_OWNED = {"FakeWHMServer"}


def _import_froms(path: Path) -> list[ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]


def test_whm_support_owns_only_its_own_double() -> None:
    """`support/whm.py` declares the WHM row and nothing else."""
    assert support.whm.__all__ == sorted(WHM_OWNED)


def test_no_test_imports_a_shared_double_from_support_whm() -> None:
    """The regression that matters: a shared double reached through the WHM name.

    Scans every test module, so a new file inherits the rule without being listed here.
    """
    borrowed = {
        f"{path.relative_to(TESTS_ROOT)}: {alias.name}"
        for path in sorted(TESTS_ROOT.rglob("*.py"))
        for node in _import_froms(path)
        if node.module == "support.whm"
        for alias in node.names
        if alias.name not in WHM_OWNED
    }

    assert borrowed == set(), "import these from support.remote_exec or support.secrets"
