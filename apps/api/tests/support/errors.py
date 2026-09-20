"""The subclass walk the taxonomy-coverage assertions share.

Each error tree has a test asserting every member declares its own `status_code` rather than
inheriting `NoaError`'s 503 — so a class added later without a decision fails there instead of
answering "service unavailable" for a permission problem or a malformed request. Every one of
those tests needs the same recursive `__subclasses__()` walk, and it was written out in each.

`__subclasses__()` only sees classes whose module has been imported, so each caller imports the
module defining the tree it walks. That is already true of every test using this: the
parametrised cases above each assertion name the classes.
"""

from __future__ import annotations

from core.errors import NoaError


def error_subclasses(root: type[NoaError]) -> set[type[NoaError]]:
    """`root` and every subclass below it, for the taxonomy coverage assertions."""
    found = {root}
    for child in root.__subclasses__():
        found |= error_subclasses(child)
    return found
