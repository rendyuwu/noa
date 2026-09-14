"""The one door onto the firewall backends.

`noa-old` built its task mapping from the usable backends and handed it to `asyncio.gather`
unconditionally. With neither backend usable the mapping was empty, `gather()` returned `[]`,
and the tool reported success having changed nothing — on an approved CHANGE, a firewall
release that never happened, on a server NOA could not drive at all.

The firewall-preflight READ refused that, with a check written inside the tool. The guard moved
to the fan-out, because a per-tool check is a check the next tool can forget, and the
zero-backend harm lives on the CHANGE side where forgetting it is silent.

Five properties, and two that are about the *shape* of the code rather than its behaviour:

- zero usable backends raises, and neither backend is asked;
- zero backends with `sudo -n` denied says `ssh_sudo_required` instead — the binaries are there
  and the remedy is a sudoers line, not an install (`noa-old` GH #82);
- only usable backends run, and they run at once, not in sequence;
- the fan-out has exactly one home, so the release-and-allow and allowlist-remove tools inherit
  the guard rather than re-authoring it (a machine-readable property is bound by a test, not by
  a docstring);
- every argv NOA can send sits inside the sudoers grant `docs/integrations/whm.md` publishes.
  The grant is per-argument, so an argv outside it is denied on a working server and read back
  as "no firewall tools" — which is what sent the availability probe's own `csf -v` into that
  answer on `web16-cpn` (2026-09-14) and is why the probes now reuse the READ path's lookups.

No SSH here. The gate's whole input is a `FirewallAvailability` struct and two callables, so
these tests exercise it directly; `test_whm_firewall_availability.py` covers how the struct is
produced and `test_whm_tools_firewall_preflight.py` covers what the refusal looks like once it
has travelled through `sanitize_tool_errors` to the model.
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
# filename so a firewall tool module written later is scanned without being listed here — the
# release-and-allow tool split the CHANGE tools into `whm_firewall_change.py`, and a literal
# path would have quietly stopped covering the half of the firewall path where the zero-backend
# harm actually lives. Deliberately
# not the whole `mcp_tools` package: a PMG tool's fan-out is not this guard's business, and a
# rule that failed for an unrelated module is a rule the next author routes around.
FIREWALL_MODULES = (
    "core/integrations/whm",
    "apps/api/src/noa_api/mcp_tools/whm_firewall*.py",
)

# Where the sudoers grant is published, and the marker that finds the block inside it. Parsed
# rather than copied: a second literal in this module would be a claim, not a check — no
# production or documentation edit could redden it.
WHM_DOC = "docs/integrations/whm.md"
SUDOERS_GRANT_MARKER = "<!-- whm-sudoers-grant -->"

# The call names that compose one firewall argv, split by backend because the grant is.
CSF_ARGV_CALLS = frozenset(
    {"run_csf_command", "build_csf_command", "tolerated_csf_step", "_tolerated_csf"}
)
IMUNIFY_ARGV_CALLS = frozenset(
    {"run_imunify_command", "build_imunify_command", "tolerated_imunify_step"}
)

# Every sub-command reachable in production, per backend. **Equality**, not subset: a new argv
# has to be declared here, and that is the moment its author finds out it also has to be inside
# the grant or behind an infra request.
CSF_PRODUCTION_ARGV = {"-g", "-tr", "-dr", "-ta", "-tra", "-ar"}
IMUNIFY_PRODUCTION_ARGV = {"ip-list"}

# The sites that pass on an argv they *received*, as `path::function`. Without them a rule of
# "not a literal → fail" would be red on a clean tree. Frozen rather than tolerated by shape,
# so a **new** forwarder is a declaration instead of a silent escape from the scan — the shape
# `FIREWALL_FAN_OUT_SITES` above already uses.
FIREWALL_ARGV_FORWARDERS = {
    "core/integrations/whm/csf_cli.py::run_csf_command",
    "core/integrations/whm/imunify_cli.py::run_imunify_command",
    "apps/api/src/noa_api/mcp_tools/whm_firewall_allowlist.py::_tolerated_csf",
    "apps/api/src/noa_api/mcp_tools/whm_firewall_change.py::_tolerated_csf",
    "apps/api/src/noa_api/mcp_tools/whm_firewall_change_common.py::tolerated_csf_step",
    "apps/api/src/noa_api/mcp_tools/whm_firewall_change_common.py::tolerated_imunify_step",
}


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


# --- Zero usable backends is an error, never an empty success ---


async def test_zero_usable_backends_refuses_rather_than_gathering_nothing() -> None:
    """The zero-backend rule, and `noa-old`'s bug stated as a test.

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
    """`noa-old` GH #82: two causes, two remedies, two codes.

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
    dead backend is no reason to withhold the other's.
    """
    assert usable_backends(availability(csf=True, sudo=True)) == (BACKEND_CSF,)
    assert require_usable_backends(availability(csf=True, sudo=True)) == (BACKEND_CSF,)


# --- Only the usable backends, and both at once ---


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
    """`asyncio.gather`, never two sequential awaits.

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

    The preflight merges evidence in the key order it gets back, and the row-cap rule wants a
    bound that is reproducible across identical calls — a race-ordered mapping is not.
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
    """A hand-rolled `asyncio.gather` is how `noa-old`'s bug comes back.

    Scans the whole firewall path rather than a list of tools, so the release-and-allow and
    allowlist-remove tools — and anything after them — inherit the rule without being named
    here (the shape `test_support_layout.py` and
    `test_pins.py` already use).
    """
    assert _gather_sites() == FIREWALL_FAN_OUT_SITES, (
        "route the fan-out through firewall_gate.run_on_usable_backends, so zero usable "
        "backends cannot become an empty success"
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
    silently scanning less than it says it does (the compare-must-still-separate rule's shape:
    a predicate that cannot fail is not a predicate).
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


# --- every argv NOA sends is inside the grant the fleet actually issues ---


def test_every_firewall_argv_sits_inside_the_sudoers_grant() -> None:
    """A sudoers grant scoped correctly is per-*argument*, so an argv nobody granted is denied
    on a server that works, and `is_sudo_rights_failure` turns that into "no firewall tools".

    That is not hypothetical: the availability probe sent `csf -v` and `imunify360-agent
    version` — the only two commands NOA never used for work — and on `web16-cpn`
    (2026-09-14) they were the only two denied. Nothing in the repository bound the argv NOA
    emits to the contract it was given. This is that binding, in two halves: the discovered set
    must equal what is declared above (a production edit reddens it), and the declared set must
    sit inside the grant parsed out of `docs/integrations/whm.md` (a documentation edit reddens
    it).

    **Stated ceiling: the scan is keyed on call names.** A wrapper introduced under a name in
    neither `CSF_ARGV_CALLS` nor `IMUNIFY_ARGV_CALLS` is invisible to it. Equality catches every
    change reached through the registered names; it does not catch a new indirection. Not
    bound — said.
    """
    csf, imunify, forwarders = _argv_scan()

    assert forwarders == FIREWALL_ARGV_FORWARDERS, (
        "an argv that is not a literal escapes this scan — declare the forwarder here, or pass "
        "a literal so its first token is checked against the grant"
    )
    assert csf == CSF_PRODUCTION_ARGV
    assert imunify == IMUNIFY_PRODUCTION_ARGV

    granted = _granted_subcommands()
    assert csf <= granted["csf"], f"{WHM_DOC} does not grant every csf sub-command NOA sends"
    assert imunify <= granted["imunify360-agent"], (
        f"{WHM_DOC} does not grant every imunify360-agent sub-command NOA sends"
    )


def _argv_scan() -> tuple[set[str], set[str], set[str]]:
    """First argv token per backend, and `path::function` for every site forwarding an argv."""
    csf: set[str] = set()
    imunify: set[str] = set()
    forwarders: set[str] = set()
    for path in _firewall_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for node in ast.walk(parent):
                name = _argv_call_name(node)
                if name is None:
                    continue
                first = _first_argv_token(node)  # type: ignore[arg-type]
                if first is None:
                    forwarders.add(f"{path.relative_to(REPO_ROOT)}::{parent.name}")
                elif name in CSF_ARGV_CALLS:
                    csf.add(first)
                else:
                    imunify.add(first)
    return csf, imunify, forwarders


def _argv_call_name(node: ast.AST) -> str | None:
    """The registered call name this node invokes, or `None` when it is not one of them."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name if name in CSF_ARGV_CALLS | IMUNIFY_ARGV_CALLS else None


def _first_argv_token(node: ast.Call) -> str | None:
    """The literal first element of the call's argv, or `None` when the argv is a name.

    `run_*`/`tolerated_*` take the argv as the keyword-only `args`; `build_*_command` takes it
    as the first positional, which is why the fallback is only read when no keyword is there.
    """
    argv = next((keyword.value for keyword in node.keywords if keyword.arg == "args"), None)
    if argv is None and node.args:
        argv = node.args[0]
    if isinstance(argv, ast.List) and argv.elts and isinstance(argv.elts[0], ast.Constant):
        return str(argv.elts[0].value)
    return None


def _granted_subcommands() -> dict[str, set[str]]:
    """The published sudoers grant, as `{binary name: {sub-command}}`.

    Read out of the document rather than restated here, so the code and the line an operator
    pastes cannot drift apart in silence.
    """
    text = (REPO_ROOT / WHM_DOC).read_text(encoding="utf-8")
    _, marker, after = text.partition(SUDOERS_GRANT_MARKER)
    assert marker, f"`{SUDOERS_GRANT_MARKER}` is not in {WHM_DOC}"
    _, _, entries = after.split("```")[1].partition("NOPASSWD:")
    assert entries.strip(), f"the grant block in {WHM_DOC} carries no `NOPASSWD:` line"

    granted: dict[str, set[str]] = {}
    for entry in entries.split("Defaults")[0].replace("\\", " ").split(","):
        tokens = entry.split()
        if not tokens:
            continue
        assert len(tokens) >= 2, f"grant entry names no sub-command: {entry.strip()!r}"
        granted.setdefault(tokens[0].rsplit("/", 1)[-1], set()).add(tokens[1])
    return granted
