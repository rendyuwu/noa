"""The one door onto the firewall backends (T68, V57).

`noa-old` built its task mapping from the usable backends and handed it to `asyncio.gather`
unconditionally. With neither backend usable the mapping was empty, `gather()` returned `[]`,
and the tool reported success having changed nothing — on an approved CHANGE, a firewall
release that never happened, on a server NOA could not drive at all.

T24 refused that for the exposed preflight READ, with a check written inside the tool. T68 moved
it to the fan-out, because a per-tool check is a check the next tool can forget, and V57's harm
lives on the CHANGE side (T25/T26) where forgetting it is silent.

Four properties, and one that is about the *shape* of the code rather than its behaviour:

- zero usable backends raises, and neither backend is asked (§V.57);
- zero backends with `sudo -n` denied says `ssh_sudo_required` instead — the binaries are there
  and the remedy is a sudoers line, not an install (`noa-old` GH #82, §V.55);
- only usable backends run, and they run at once, not in sequence;
- the fan-out has exactly one home, so T25/T26 inherit the guard rather than re-authoring it
  (§V.84c: a machine-readable property is bound by a test, not by a docstring).

No SSH here. The gate's whole input is a `FirewallAvailability` struct and two callables, so
these tests exercise it directly; `test_whm_firewall_availability.py` covers how the struct is
produced and `test_whm_tools_firewall_preflight.py` covers what the refusal looks like once it
has travelled through `sanitize_tool_errors` to the model (§V.19).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from core.integrations.whm.availability import BACKEND_CSF, BACKEND_IMUNIFY, FirewallAvailability
from core.integrations.whm.errors import WHMFirewallCLIError
from core.integrations.whm.firewall_gate import (
    ERROR_NO_FIREWALL_BACKEND,
    MESSAGE_NO_FIREWALL_BACKEND,
    MESSAGE_SUDO_REQUIRED,
    require_usable_backends,
    run_on_usable_backends,
    usable_backends,
)
from core.remote_exec.sudo import SSH_SUDO_REQUIRED_CODE

REPO_ROOT = Path(__file__).resolve().parents[3]

# Every `asyncio.gather` allowed to exist on the WHM firewall path, as `module::function`.
#
# `check_firewall_binaries` is not gated by availability — it is what *computes* it, and it
# always probes both backends — so it is a second fan-out by necessity, not by drift.
# `run_on_usable_backends` is the door. A third entry means some tool grew its own fan-out, and
# with it its own chance to gather nothing; route it through the door or add it here
# deliberately.
FIREWALL_FAN_OUT_SITES = {
    "core/integrations/whm/availability.py::check_firewall_binaries",
    "core/integrations/whm/firewall_gate.py::run_on_usable_backends",
}

# Where the scan looks: a directory, or a glob. The tool-layer entry is a glob rather than a
# filename so a firewall tool module written later is scanned without being listed here — T25
# split the CHANGE tools into `whm_firewall_change.py`, and a literal path would have quietly
# stopped covering the half of the firewall path where V57's harm actually lives. Deliberately
# not the whole `mcp_tools` package: a PMG tool's fan-out is not this guard's business, and a
# rule that failed for an unrelated module is a rule the next author routes around.
FIREWALL_MODULES = (
    "core/integrations/whm",
    "apps/api/src/noa_api/mcp_tools/whm_firewall*.py",
)


def availability(
    *, csf: bool = False, imunify: bool = False, sudo: bool = False
) -> FirewallAvailability:
    """`check_firewall_binaries`' answer, written by hand."""
    return FirewallAvailability(csf=csf, imunify=imunify, sudo_required=sudo)


class Recorder:
    """A backend operation that records whether it was ever asked to run."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return self.answer


# --- V57: zero usable backends is an error, never an empty success ---


async def test_zero_usable_backends_refuses_rather_than_gathering_nothing() -> None:
    """§V.57, and `noa-old`'s bug stated as a test.

    An empty mapping back from here would be a caller's "done, nothing to report" — which on a
    CHANGE is a change that never happened, reported as success.
    """
    csf, imunify = Recorder("csf"), Recorder("imunify")

    with pytest.raises(WHMFirewallCLIError) as raised:
        await run_on_usable_backends(availability(), csf=csf, imunify=imunify)

    assert raised.value.error_code == ERROR_NO_FIREWALL_BACKEND
    assert raised.value.message == MESSAGE_NO_FIREWALL_BACKEND
    # Nothing ran, so nothing can be reported.
    assert (csf.calls, imunify.calls) == (0, 0)


async def test_zero_backends_with_denied_sudo_names_sudo_rather_than_the_install() -> None:
    """`noa-old` GH #82, §V.55: two causes, two remedies, two codes.

    The binaries are installed; the sudoers line is not. "No firewall tools on this server"
    sends the operator to install software that is already there.
    """
    with pytest.raises(WHMFirewallCLIError) as raised:
        await run_on_usable_backends(
            availability(sudo=True), csf=Recorder("csf"), imunify=Recorder("imunify")
        )

    assert raised.value.error_code == SSH_SUDO_REQUIRED_CODE
    assert raised.value.message == MESSAGE_SUDO_REQUIRED


def test_the_gate_returns_the_backends_it_let_through() -> None:
    """The success half of the same function: a caller gets the set it may act on."""
    assert require_usable_backends(availability(csf=True, imunify=True)) == (
        BACKEND_CSF,
        BACKEND_IMUNIFY,
    )
    assert require_usable_backends(availability(imunify=True)) == (BACKEND_IMUNIFY,)


def test_a_usable_backend_beats_a_denied_one() -> None:
    """`sudo_required` alongside one working backend is not a refusal.

    It is why the other backend is missing, and the tool reports it beside a real answer — one
    dead backend is no reason to withhold the other's (§V.86).
    """
    assert usable_backends(availability(csf=True, sudo=True)) == (BACKEND_CSF,)
    assert require_usable_backends(availability(csf=True, sudo=True)) == (BACKEND_CSF,)


# --- V57: only the usable backends, and both at once ---


async def test_only_usable_backends_run() -> None:
    """CSF-only is the common shape — plenty of cPanel boxes never install Imunify.

    Asking anyway produces a failure entry that says "not installed" in a code that means "the
    command broke". The callables are not coroutines for this reason: the one for an unusable
    backend is never even created.
    """
    csf, imunify = Recorder("csf"), Recorder("imunify")

    results = await run_on_usable_backends(availability(csf=True), csf=csf, imunify=imunify)

    assert results == {BACKEND_CSF: "csf"}
    assert (csf.calls, imunify.calls) == (1, 0)


async def test_both_backends_are_in_flight_at_once() -> None:
    """`asyncio.gather`, ⊥ two sequential awaits.

    Each operation is a full SSH handshake to the same host while an operator waits on a chat
    turn. The wait below deadlocks unless the second starts before the first returns, so a
    sequential rewrite fails here rather than merely being slower.
    """
    both_started = asyncio.Event()
    in_flight = 0

    async def operation() -> str:
        nonlocal in_flight
        in_flight += 1
        if in_flight == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        return "ran"

    results = await run_on_usable_backends(
        availability(csf=True, imunify=True), csf=operation, imunify=operation
    )

    assert both_started.is_set()
    assert results == {BACKEND_CSF: "ran", BACKEND_IMUNIFY: "ran"}


async def test_the_result_reads_in_a_fixed_backend_order() -> None:
    """Two identical calls produce one ordering, whichever backend answered first.

    The preflight merges evidence in the key order it gets back, and §V.85 wants a bound that is
    reproducible across identical calls — a race-ordered mapping is not.
    """
    slow_csf_first = await run_on_usable_backends(
        availability(csf=True, imunify=True),
        csf=_after(0.01, "csf"),
        imunify=_after(0, "imunify"),
    )

    assert list(slow_csf_first) == [BACKEND_CSF, BACKEND_IMUNIFY]


def _after(delay: float, answer: str):  # type: ignore[no-untyped-def]
    async def operation() -> str:
        await asyncio.sleep(delay)
        return answer

    return operation


# --- the guard has one home, so the next firewall tool inherits it ---


def test_the_firewall_fan_out_lives_in_one_place() -> None:
    """T68: a hand-rolled `asyncio.gather` is how `noa-old`'s bug comes back.

    Scans the whole firewall path rather than a list of tools, so T25/T26 — and anything after
    them — inherit the rule without being named here (the shape `test_support_layout.py` and
    `test_pins.py` already use).
    """
    assert _gather_sites() == FIREWALL_FAN_OUT_SITES, (
        "route the fan-out through firewall_gate.run_on_usable_backends, so zero usable "
        "backends cannot become an empty success (V57)"
    )


def _gather_sites() -> set[str]:
    """`module::function` for every `asyncio.gather` call on the firewall path."""
    sites: set[str] = set()
    for path in _firewall_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if any(_is_gather(node) for node in ast.walk(parent)):
                sites.add(f"{path.relative_to(REPO_ROOT)}::{parent.name}")
    return sites


def _firewall_sources() -> list[Path]:
    """Every `.py` on the firewall path, from a directory or a glob.

    A glob that matches nothing would make this whole guard vacuous, so it is refused rather than
    silently scanning less than it says it does (V87's shape: a predicate that cannot fail is not
    a predicate).
    """
    sources: list[Path] = []
    for entry in FIREWALL_MODULES:
        target = REPO_ROOT / entry
        if target.is_dir():
            sources.extend(sorted(target.rglob("*.py")))
            continue
        matched = sorted(REPO_ROOT.glob(entry))
        assert matched, f"no firewall source matches `{entry}`"
        sources.extend(matched)
    return sources


def _is_gather(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "gather"
    )
