"""One async driver runs this suite, and it is pytest-asyncio.

`anyio` is not a choice this repo made: it arrives underneath starlette and httpx, and its
distribution ships a pytest plugin that registers itself. Both plugins implement
`pytest_fixture_setup` as a wrapper, and neither declares `tryfirst`, so which one wraps a given
async fixture is decided by plugin *registration* order — which comes from entry-point discovery
and therefore differs between machines. When the two split a single test, driving the fixture on
one event loop and the test body on another, a database connection opened in the test body cannot
be closed in fixture teardown: asyncpg raises "got Future attached to a different loop", the
connection leaks, and the scratch-database DROP that follows fails with `ObjectInUseError`. That
is a real failure, not a hypothetical one — it is what made the GitLab `test:python` job red over
a tree that stayed green on a developer's machine, which is the worst shape a defect can take
because the lane that reports it is the lane that cannot reproduce it.

`addopts = "-p no:anyio"` in `pyproject.toml` settles it structurally rather than by convention.
These two checks are what fails if that line is dropped or if a test reaches for the other driver
again; without them the regression is invisible until a pipeline on some other host disagrees
with this one.

Both assertions were watched to fail before being kept: deleting the `addopts` line reddens the
first, and restoring a single `@pytest.mark.anyio` reddens the second.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# The mark is what pulls `anyio_backend` into a test's fixture closure and hands the item to the
# other plugin.
ANYIO_MARK = "@pytest.mark.anyio"


def test_the_anyio_plugin_is_not_loaded(pytestconfig: pytest.Config) -> None:
    """The plugin that would compete for async fixtures never registers."""
    assert not pytestconfig.pluginmanager.hasplugin("anyio"), (
        'anyio\'s pytest plugin is registered — `addopts = "-p no:anyio"` is missing or '
        "overridden, and async fixtures are again claimed by whichever plugin pluggy runs first"
    )


def test_no_test_asks_for_the_other_driver() -> None:
    """No test carries the mark that hands it to anyio.

    Read off the files rather than off a hand-kept list, and with `Path.rglob` rather than
    `git ls-files`: the CI Python image ships no git, so a git-gated scan skips exactly where this
    has to run. This file is excluded because its own prose names the mark it forbids.
    """
    marked = sorted(
        path.relative_to(TESTS_DIR).as_posix()
        for path in TESTS_DIR.rglob("*.py")
        if path != Path(__file__).resolve() and ANYIO_MARK in path.read_text(encoding="utf-8")
    )
    assert marked == [], f"{ANYIO_MARK} found in {marked}; this suite runs on pytest-asyncio"
