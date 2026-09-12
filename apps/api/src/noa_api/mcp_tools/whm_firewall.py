"""WHM firewall tools (`whm_preflight_firewall_entries`).

**This preflight is exposed, and it is the only one that is** (DECISIONS section 6.5,
owner-confirmed 2026-08-04). The "a preflight runs in-process inside its workflow" rule is about
evidence a CHANGE tool gathers for its own approval card; this one is step 1 of the operator's
actual working pattern — *check whether an IP is denied → release it → allowlist it* — and the
decision it feeds is the operator's, not a gate's. They read the verdict and decide whether to
call the release-and-allow tool at all.

Both backends are asked, in parallel, and only the ones that are usable — through
`core.integrations.whm.firewall_gate.run_on_usable_backends`, which is where zero usable
backends becomes `no_firewall_backend` rather than a success that read nothing. This tool
holds no copy of that check: one lives at the door every firewall tool goes through, so the two
CHANGE tools cannot ship without it. CSF answers in human-facing text and Imunify in JSON;
`core.integrations.whm.csf` and `.imunify` turn each into a verdict, and this module is where the
two become one answer.

Three things the result does that `noa-old`'s did not:

- **No raw output.** `noa-old` returned csf's whole `-g` dump and Imunify's whole JSON document
  alongside the parsed verdict. DECISIONS section 6.5 says the before-state shows the
  `csf.deny`/`csf.allow` log line and never a raw iptables table, and the result persists in
  LibreChat's MongoDB. The bounded `matches` list is the evidence.
- **A backend that did not answer is named, and never reads as clean.** `noa-old` computed the
  combined verdict from whichever backend succeeded and
  otherwise fell through to `not_found`, so a broken CSF plus a clean Imunify reported "this IP
  is not blocked" on a box whose *blocking* backend was silent — a fabrication the tool
  authored. Here a backend that errored, or that returned CSF's own `unknown` verdict, is listed
  in `unanswered_backends`, and a call where nothing answered has `combined_verdict: "unknown"`.
  The zero-backend error bounds only the zero case; this is the partial one.
- **Evidence is not repeated per backend.** The lines are labelled by their own content
  (`csf.deny`, `Imunify blacklist: …`), so a per-backend copy would double the transcript to say
  the same thing twice. Each backend entry carries its verdict, or the code its failure has.
- **A comment NOA wrote is cut back out of every evidence line, and this module is where.** This
  tool reads csf's and Imunify's own text straight into a transcript, and the release-and-allow
  tool writes the operator's approval reason into the comment of every allow entry it creates — so
  without the cut, the one operator-typed reason field would come back through this result the
  way WHM's `suspendreason` came back through
  `whm_search_accounts`. The cut is made in `BackendLookup` rather than in this tool's body,
  because the same lines are the two CHANGE tools' before-state and go from there onto the
  approval card: uncut there, an *earlier* approval's reason rides NOA's own allow entry onto a
  *later* card. See `without_noa_comment_text` for what makes the cut possible and
  `BackendLookup.__post_init__` for what it costs.

The tool holds no session while it talks to the server. The row is resolved and turned into an
`SSHConnectionConfig` inside one session, which then closes: the account search's rule, and the
reason
`run_csf_command`/`check_firewall_binaries` take a config rather than a row as of this task —
holding a pooled Postgres connection across four SSH handshakes is how a slow WHM host becomes a
database outage, and an ORM row cannot be read once its session is gone.

Registration declares `ToolRisk.READ`. Nothing here records anything: the `tool_runs`
row is the audit middleware's, beside the RBAC gate — one seam, never per-tool code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.errors import NoaError
from core.integrations.whm.availability import (
    BACKEND_CSF,
    BACKEND_IMUNIFY,
    FirewallAvailability,
    check_firewall_binaries,
)
from core.integrations.whm.csf import parse_csf_grep_output, parse_csf_target
from core.integrations.whm.csf_cli import require_csf_success, run_csf_command
from core.integrations.whm.firewall_gate import run_on_usable_backends
from core.integrations.whm.imunify import (
    format_imunify_matches,
    parse_imunify_ip_list_response,
)
from core.integrations.whm.imunify_cli import parse_imunify_json_output, run_imunify_command
from core.integrations.whm.ssh import resolve_whm_ssh_config
from core.remote_exec.types import SSHConnectionConfig
from core.servers.whm_ref import resolve_whm_server_ref
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)

TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES = "whm_preflight_firewall_entries"

# The combined answer. `unknown` is the honest one: no backend produced a verdict.
VERDICT_BLOCKED = "blocked"
VERDICT_ALLOWLISTED = "allowlisted"
VERDICT_NOT_FOUND = "not_found"
VERDICT_UNKNOWN = "unknown"

# Backend-native verdicts that mean the same thing. CSF says `blocked`/`allowlisted`, Imunify
# says `blacklisted`/`whitelisted`; the operator asked one question.
_BLOCKING_VERDICTS = frozenset({"blocked", "blacklisted"})
_ALLOWING_VERDICTS = frozenset({"allowlisted", "whitelisted"})

ERROR_TARGET_REQUIRED = "target_required"
ERROR_INVALID_TARGET = "invalid_target"
# Verbatim from `noa-old`: csf exited 0 with nothing to read.
ERROR_INVALID_RESPONSE = "invalid_response"

MESSAGE_TARGET_REQUIRED = "A firewall target is required."
MESSAGE_INVALID_TARGET = "Target must be an IP address, a CIDR network, or a hostname."
MESSAGE_CSF_GREP_FAILED = "CSF did not return the firewall entries for this target."
MESSAGE_CSF_EMPTY = "CSF returned an empty response for this target."

# The prefix NOA stamps onto every firewall comment it writes, and the thing that makes the
# operator's reason findable again in text nobody else formats. It is not decoration:
# csf stores a comment as free text at the tail of an entry and echoes it back through `csf -g`,
# so a cut with no marker to aim at would either miss NOA's own comments or take LFD's evidence
# with them.
NOA_COMMENT_MARKER = "noa:"

# `noa:` plus the action request's id, and nothing after it. Case-insensitive because csf and
# Imunify both round-trip the comment through their own storage and neither promises case.
_NOA_COMMENT_RE = re.compile(
    rf"{NOA_COMMENT_MARKER}[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}",
    re.IGNORECASE,
)

DESCRIPTION_WHM_PREFLIGHT_FIREWALL_ENTRIES = (
    "Check whether an IP, network or hostname is blocked or allowed on one WHM server, in both "
    "CSF and Imunify360. Use it before asking to release an address, and report the verdict and "
    "the evidence lines to the operator so they can decide. Returns a combined verdict, each "
    "backend's own answer, and the matching firewall entries. Read-only: it changes nothing."
)


@dataclass(frozen=True)
class BackendLookup:
    """One backend's answer about one target.

    `verdict` is the backend's own word (`blocked`, `whitelisted`, …) and is `None` when the backend
    failed. `answered` is the property the combined verdict is built from, and it is deliberately
    stricter than `ok`: CSF's `unknown` means "csf said something we do not recognise", which is not
    evidence that the address is clean (see `core.integrations.whm.csf`).

    `allow_entry` is the one fact the verdict cannot carry. Both backends resolve a
    conflict block-first, so an address on a deny list *and* an allow list reports as blocked
    and the allow entry disappears from the answer. An allowlist removal's postflight asks about
    that entry and nothing else, and it is only meaningful where `answered` is true — a backend
    that said nothing has not said there is no allow entry.
    """

    ok: bool
    verdict: str | None = None
    matches: list[str] = field(default_factory=list)
    total_matches: int = 0
    error_code: str | None = None
    message: str | None = None
    allow_entry: bool = False

    def __post_init__(self) -> None:
        """Cut NOA's own comment out of every evidence line, once, here.

        This is the single point every firewall evidence line crosses: both parse sites build one
        of these, and both readers — this module's own tool payload and `firewall_state`'s
        before-state — take `matches` off it. A cut written at one reader leaves the other one
        carrying the text, which is exactly the defect this replaced: the before-state went onto
        `action_requests.approval_context` uncut, so the reason an operator typed at an *earlier*
        approval rode the `csf.allow` entry NOA itself wrote back onto a *later* approval card and
        into the block copied off it. The reason on a card belongs to the decision being made now
        and reaches it from `action_requests`, never from a firewall line.

        **The trade, stated where it happens.** An evidence line is no longer byte-verbatim for an
        allow entry NOA wrote — on the approval card and in the receipt as well as in a tool
        result — and that is deliberate. The alternative is rendering one decision's typed reason
        on another decision's card and from there into a ticket.

        Only NOA's own comment goes. The marker is what tells the two apart, and it is a marker
        rather than a guess for that reason (`noa_firewall_comment`): an administrator's own
        comment on a hand-written entry is the target system's text and is what the evidence block
        exists to show.

        `total_matches`, `verdict` and `allow_entry` are all computed from the full lines before
        this runs, and none of them moves: the cut shortens a line, it never drops one.
        """
        object.__setattr__(
            self, "matches", [without_noa_comment_text(line) for line in self.matches]
        )

    @property
    def answered(self) -> bool:
        """True when this backend produced a verdict a decision can rest on."""
        return self.ok and self.verdict is not None and self.verdict != VERDICT_UNKNOWN

    def as_payload(self) -> ToolPayload:
        """The per-backend entry in the tool result. No evidence — that is merged above."""
        if not self.ok:
            return {"ok": False, "error_code": self.error_code, "message": self.message}
        return {"ok": True, "verdict": self.verdict}


def _backend_failure(error_code: str, message: str) -> BackendLookup:
    return BackendLookup(ok=False, error_code=error_code, message=message)


def noa_firewall_comment(action_request_id: UUID, *, reason: str) -> str:
    """The comment NOA writes onto a firewall entry it creates.

    The operator's own words, behind a marker that says NOA wrote them. The one reason field may
    *leave* NOA — a firewall allow entry has a comment field whose only honest
    content is why the address was allowed, and the alternative is a NOA-authored placeholder in
    a record a human reads on the box. The other half: it has to be unreadable on every
    path back to a model, and the only surface that answers a model here is this module's own
    `matches`, which `without_noa_comment_text` cuts.

    The marker is what makes that cut possible at all. It is written by NOA, so NOA knows where
    the text it owns begins; a rule that guessed instead would have to decide whether the trailing
    words of a `csf.deny` line are LFD's evidence or somebody's reason, and it would get that
    wrong in one direction or the other.

    The id travels with it for the human on the server: `noa:<id>` is what survives the cut, and
    it is the address of the approval row where the reason is readable behind the operator's own
    cookie rather than in a transcript.
    """
    return f"{NOA_COMMENT_MARKER}{action_request_id} {reason}"


def without_noa_comment_text(line: str) -> str:
    """One evidence line with NOA's own comment text cut back to its marker.

    From the marker to the end of the line, because csf gives a comment **no closing boundary**:
    it is free text at the tail of an entry, and a reason containing a bracket or a quote would
    walk straight through any rule that tried to find the end of it. So the bound is stated
    rather than hidden — a token csf placed *after* the comment is dropped with it. Imunify's
    renderer puts its structured fields ahead of the comment for exactly this reason
    (`core.integrations.whm.format_imunify_matches`), so on that side the cut costs nothing.

    A line without the marker is returned unchanged, which is most of them: `csf.deny` entries,
    LFD's own block reasons and Imunify's `smtpauth brute force` are the evidence this tool
    exists to show, and they are nobody's approval reason.

    Applied once, in `BackendLookup.__post_init__` — every evidence line crosses that one point,
    and the card and the receipt are cut there too. See it for the trade that buys.

    Not applied in `parse_csf_grep_output`: `core.integrations.whm.csf` is a pure parser with no
    NOA imports, and it is the parser the verdict is read off — which is why the verdict is
    unaffected by the cut either way. `backend_change_failure` is the second caller and cuts
    something else: a backend's refusal message, which can quote the entry back.
    """
    match = _NOA_COMMENT_RE.search(line)
    return line if match is None else line[: match.end()]


async def csf_firewall_entries(config: SSHConnectionConfig, *, target: str) -> BackendLookup:
    """What CSF holds for `target`. Internal — never an MCP tool.

    `NoaError` is caught rather than raised on: it is the tree both `CSFCLIError` and
    `SSHExecutionError` sit in, and one dead backend must not take the other's answer with it.
    The code travels intact — `ssh_sudo_required` and `csf_command_failed` name different
    remedies, and collapsing them is `noa-old` GH #82.
    """
    try:
        result = await run_csf_command(config, args=["-g", target])
        output = require_csf_success(result, default_message=MESSAGE_CSF_GREP_FAILED)
    except NoaError as exc:
        return _backend_failure(exc.error_code, exc.message)

    if not output.strip():
        # Exit 0 with nothing to read is not "clean", it is unparseable.
        return _backend_failure(ERROR_INVALID_RESPONSE, MESSAGE_CSF_EMPTY)

    parsed = parse_csf_grep_output(output, target=target)
    return BackendLookup(
        ok=True,
        verdict=parsed.verdict,
        matches=parsed.matches,
        total_matches=parsed.total_matches,
        allow_entry=parsed.allow_entry,
    )


async def imunify_firewall_entries(config: SSHConnectionConfig, *, target: str) -> BackendLookup:
    """What Imunify360 holds for `target`. Internal — never an MCP tool.

    `--by-ip` filters server-side and `parse_imunify_ip_list_response` filters again against
    the target, because the flag has been observed returning neighbours and a verdict read off
    someone else's row is worse than no verdict.
    """
    try:
        result = await run_imunify_command(
            config,
            args=["ip-list", "local", "list", "--by-ip", target, "--json"],
        )
        data = parse_imunify_json_output(result)
    except NoaError as exc:
        return _backend_failure(exc.error_code, exc.message)

    parsed = parse_imunify_ip_list_response(data, target)
    matches = format_imunify_matches(parsed.entries)
    return BackendLookup(
        ok=True,
        verdict=parsed.verdict,
        matches=matches,
        total_matches=len(matches),
        allow_entry=parsed.allow_entry,
    )


def combine_firewall_verdict(lookups: Sequence[BackendLookup]) -> str:
    """One answer from however many backends answered.

    Block beats allow, exactly as each backend's own parser resolves the same conflict: an address
    in both `csf.deny` and `csf.allow` is, operationally, still blocked, and the release-and-allow
    tool releases *and* allows in one action so that intermediate state is real.

    `not_found` requires a backend to have said so. Everything else — no usable backend answer
    at all — is `unknown`, never a clean bill: that distinction is the whole reason this
    function exists rather than a default.
    """
    verdicts = [lookup.verdict for lookup in lookups if lookup.answered]
    if any(verdict in _BLOCKING_VERDICTS for verdict in verdicts):
        return VERDICT_BLOCKED
    if any(verdict in _ALLOWING_VERDICTS for verdict in verdicts):
        return VERDICT_ALLOWLISTED
    if any(verdict == VERDICT_NOT_FOUND for verdict in verdicts):
        return VERDICT_NOT_FOUND
    return VERDICT_UNKNOWN


async def gather_firewall_entries(
    config: SSHConnectionConfig,
    *,
    target: str,
    availability: FirewallAvailability,
) -> dict[str, BackendLookup]:
    """Ask every usable backend at once, through the one door that refuses none.

    The zero-backend refusal is `run_on_usable_backends`', not this function's: a check written here
    is a check the next firewall tool can forget, and a CHANGE tool that forgets it reports an
    approved change it never made (`noa-old`'s empty `gather()`). Only usable backends are queried —
    asking a machine with no Imunify installed produces a failure entry that says nothing.
    """
    return await run_on_usable_backends(
        availability,
        csf=lambda: csf_firewall_entries(config, target=target),
        imunify=lambda: imunify_firewall_entries(config, target=target),
    )


@sanitize_tool_errors(TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES)
async def whm_preflight_firewall_entries(
    *,
    server_ref: str,
    target: str,
    context: McpToolContext,
) -> ToolPayload:
    """Is this address blocked on this WHM server, and on the strength of what?

    Two guards run before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `target` is refused — `csf -g ""` greps for everything;
    - a target that classifies as `unknown` is refused. Every *other* kind is accepted, and that
      is the IPv4-only-on-CHANGE rule read correctly: the CHANGE tools reject anything but IPv4
      because they write
      firewall rules, while "you asked about an IPv6 address and here is what CSF says" is a
      useful answer even where changing it is not permitted.

    Then one database session: resolve the operator's word to a server — a tie is
    `choices`, never a pick — and turn that row into a connection. Both happen inside the
    session because both read mapped attributes; everything after it is SSH, and the session is
    closed by then.

    `resolve_whm_ssh_config` is also the third guard, and it is deliberately allowed to raise:
    its three refusals (`ssh_invalid_host`, `ssh_not_configured`, `ssh_host_key_not_validated`)
    are `NoaError`s, so `sanitize_tool_errors` hands the model the code that names the fix
    — raw exceptions never reach the model. Reaching the probe with an unpinned row would instead
    report "no firewall backends",
    which is a different problem and the wrong thing to go fix.
    """
    normalized_target = target.strip()
    if not normalized_target:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    parsed_target = parse_csf_target(normalized_target)
    if parsed_target.kind == "unknown":
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_INVALID_TARGET)

    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        resolution = await resolve_whm_server_ref(server_ref, repository=repository)
        if not resolution.ok or resolution.server is None:
            return tool_failure(
                # `ERROR_UNKNOWN` lives in `results` rather than beside one system's tools:
                # the same fallback for the same resolver shape, and two copies is how one of
                # them drifts.
                resolution.error_code or ERROR_UNKNOWN,
                resolution.message,
                choices=resolution.choices,
            )
        server_id = str(resolution.server.id)
        config = resolve_whm_ssh_config(
            resolution.server,
            cipher=context.secret_cipher,
            require_host_key_fingerprint=True,
        )

    availability = await check_firewall_binaries(config)
    lookups = await gather_firewall_entries(
        config, target=normalized_target, availability=availability
    )

    # Evidence in a fixed backend order, so two identical calls read alike. Each backend's own
    # lines are already ordered by the system that produced them (see `csf.total_matches`).
    #
    # No cut here: `BackendLookup` made it as each lookup was built, so a line reaching this list
    # already carries no approval reason and a second cut would only be a second place to forget.
    # The count below is deliberately taken from the parsers rather than from this list: the cut
    # shortens lines, it never drops one, so `total_matches` still answers "how many entries
    # mention this address".
    matches = [line for name in (BACKEND_CSF, BACKEND_IMUNIFY) for line in _lines(lookups, name)]
    total_matches = sum(lookup.total_matches for lookup in lookups.values())

    payload: ToolPayload = {
        "server_id": server_id,
        "target": normalized_target,
        # Which CHANGE tools could act on it at all, answered here so the model does not
        # have to classify an address itself.
        "target_kind": parsed_target.kind,
        "available_backends": availability.as_tools_dict(),
        # Present-but-denied ≠ absent: the operator is told to fix sudoers, not to install csf.
        "sudo_required": availability.sudo_required,
        "combined_verdict": combine_firewall_verdict(list(lookups.values())),
        # A verdict read from a subset says so. Without this, "not_found" from a
        # half-answering pair reads as "this address is clean".
        "unanswered_backends": [name for name, lookup in lookups.items() if not lookup.answered],
        "matches": matches,
        # The cut is csf's (`max_matches`), so the bound travels with the rows.
        "total_matches": total_matches,
        "truncated": total_matches > len(matches),
    }
    for name, lookup in lookups.items():
        payload[name] = lookup.as_payload()
    return tool_ok(**payload)


def _lines(lookups: dict[str, BackendLookup], backend: str) -> list[str]:
    lookup = lookups.get(backend)
    return list(lookup.matches) if lookup is not None else []


def register_whm_firewall_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register the WHM firewall READ tool on `server`; return its name and risk.

    One entry, and it stays one. `whm_firewall_release_and_allow` and
    `whm_firewall_allowlist_remove` are CHANGE tools and register from a module each —
    `whm_firewall_change` and `whm_firewall_allowlist` — because a CHANGE tool is two halves (the
    tool and its post-approval runner) and any two of the three here would push one file past
    the line budget. What they share is *code*, not a file: the lookups above are their
    in-process before-state (DECISIONS section 6.5), `noa_firewall_comment` /
    `without_noa_comment_text` are the two ends of the reason-cut bound, and what the two CHANGE
    tools share with each other is in `whm_firewall_change_common` — imported rather than
    re-spelled.
    """

    @server.tool(
        name=TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES,
        description=DESCRIPTION_WHM_PREFLIGHT_FIREWALL_ENTRIES,
        # Standard MCP hint, and nothing NOA relies on — the READ/CHANGE split that matters is
        # enforced by the approval gate, not by an annotation a client may ignore.
        annotations={"readOnlyHint": True},
    )
    async def whm_preflight_firewall_entries_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which WHM server: its id, its name in NOA, or its hostname. Call "
                    "`whm_list_servers` first if the operator has not named one."
                )
            ),
        ],
        target: Annotated[
            str,
            Field(
                description=(
                    "The address to check: an IPv4 address or network, an IPv6 address or "
                    "network, or a hostname. Pass it exactly as the operator gave it."
                )
            ),
        ],
    ) -> ToolPayload:
        return await whm_preflight_firewall_entries(
            server_ref=server_ref, target=target, context=context
        )

    return {TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES: ToolRisk.READ}


__all__: list[str] = [
    "DESCRIPTION_WHM_PREFLIGHT_FIREWALL_ENTRIES",
    "ERROR_INVALID_RESPONSE",
    "ERROR_INVALID_TARGET",
    "ERROR_TARGET_REQUIRED",
    "MESSAGE_INVALID_TARGET",
    "MESSAGE_TARGET_REQUIRED",
    "NOA_COMMENT_MARKER",
    "TOOL_WHM_PREFLIGHT_FIREWALL_ENTRIES",
    "VERDICT_ALLOWLISTED",
    "VERDICT_BLOCKED",
    "VERDICT_NOT_FOUND",
    "VERDICT_UNKNOWN",
    "BackendLookup",
    "combine_firewall_verdict",
    "csf_firewall_entries",
    "gather_firewall_entries",
    "imunify_firewall_entries",
    "noa_firewall_comment",
    "register_whm_firewall_tools",
    "whm_preflight_firewall_entries",
    "without_noa_comment_text",
]
