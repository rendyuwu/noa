"""The recursive `__subclasses__()` walk the error-tree assertions share.

`__subclasses__()` only sees classes whose module has been imported, so callers must import the
module defining the tree they walk — `test_error_status_taxonomy.py` imports `noa_api.main` for
exactly that.
"""

from __future__ import annotations

from core.errors import NoaError


def error_subclasses(root: type[NoaError]) -> set[type[NoaError]]:
    """`root` and every subclass below it, for the taxonomy coverage assertions."""
    found = {root}
    for child in root.__subclasses__():
        found |= error_subclasses(child)
    return found
