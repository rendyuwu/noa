"""The LibreChat config keys, bound by test rather than by prose.

The three keys each close a **total, silent** failure measured at the boot-inspect probes: with
`startup` and `requiresOAuth` left at their defaults, LibreChat's boot inspection draws a correct
401 from NOA, infers OAuth, and reports zero tools to every operator forever with no error anywhere
visible; without `mcpSettings.allowedDomains`, its SSRF guard refuses an internal URL outright. The
config is therefore part of the auth design, and it is machine-readable — so it binds by test, not
by prose.

**Two artifacts, one predicate.** `docs/integrations/librechat.md` is what an operator reads;
`spikes/librechat-embed-render-gate/librechat.yaml` is the config the live render-gate run actually
ran under. Asserting the same function against both is what stops the page drifting away from the
thing that was measured — the same "two truths must not live" problem applied to a config rather
than to a sentence. It cannot reach the operator's deployed file; the three-key rule says as much,
which is why a reference page exists at all.

**Negative controls, because a config assertion fails quietly.** A key path typo reads as an absent
key and compares equal to nothing; an extraction bug hands the prompt check an empty string that
contains no forbidden word. Both would pass. So each rule is also asserted to go red against a
mutated copy, and the prompt block is asserted to be present and substantial before it is searched.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from noa_api.mcp_tools.change_gate import FORBIDDEN_REASON_KEYS

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC = REPO_ROOT / "docs" / "integrations" / "librechat.md"
RIG_CONFIG = REPO_ROOT / "spikes" / "librechat-embed-render-gate" / "librechat.yaml"

# The heading the example prompt lives under, and the fence language it is written in. Both are
# part of the contract: a rename that orphans the extraction has to fail here, not silently stop
# checking (`_example_prompt` raises rather than returning "").
PROMPT_HEADING = "## Agent system prompt (example)"
PROMPT_FENCE_LANG = "text"

# `{{LIBRECHAT_BODY_CONVERSATIONID}}` is the only supported way to hand NOA a conversation id,
# because the tool call itself carries none — LibreChat has one emitter and no `_meta`. Exact
# strings — a placeholder LibreChat does not recognise is passed through as a literal and lands
# in the audit column.
REQUIRED_HEADERS: dict[str, str] = {
    "X-Noa-LibreChat-User": "{{LIBRECHAT_USER_ID}}",
    "X-Noa-Conversation-Ref": "{{LIBRECHAT_BODY_CONVERSATIONID}}",
}

# The SSRF-refusal probe measured against `noa.internal`. The allowlist is nonetheless checked
# against the host of the entry's OWN `url`, not against that constant, for two reasons:
#
#   * The host is a deployment property, not a fact about NOA. The rig this file also checks runs
#     on `noa.internal`; a deployed `librechat.yaml` names `noa-api.simondayce.my.id`. One
#     predicate has to fit both artifacts or the pair stops being "two artifacts, one predicate"
#     and becomes two truths.
#   * A fixed constant passes a config whose allowlist names one NOA host while `url` points at
#     another — which is precisely the misconfiguration this key exists to stop, since LibreChat
#     refuses the URL it was actually given. The reuse rule, one file over: the offered set must
#     not drift from the accepted set.

# The FastMCP mount answers at `/mcp/` and 307s the bare path. Pinned behaviourally by
# `test_mcp_mount.py::test_the_endpoint_is_mounted_at_mcp`; asserted here as prose the doc owes.
MCP_PATH_SUFFIX = "/mcp/"

FENCE = re.compile(r"^```(?P<lang>[a-zA-Z0-9_-]*)\s*$")


def _fenced_blocks(text: str, *, lang: str, after: str | None = None) -> list[str]:
    """Every fenced block written in `lang`, optionally only those after a heading line.

    Fences of other languages are skipped whole rather than ignored line by line, so a `yaml`
    block nested in the prose of an untagged one cannot be mistaken for a top-level match.
    """
    lines = text.splitlines()
    if after is not None:
        try:
            start = next(index for index, line in enumerate(lines) if line.strip() == after)
        except StopIteration as exc:
            raise AssertionError(f"{DOC.name} has no `{after}` heading") from exc
        lines = lines[start:]

    blocks: list[str] = []
    collecting: list[str] | None = None
    inside_other = False
    for line in lines:
        match = FENCE.match(line)
        if match is None:
            if collecting is not None:
                collecting.append(line)
            continue
        if collecting is not None:
            blocks.append("\n".join(collecting))
            collecting = None
        elif inside_other:
            inside_other = False
        elif match.group("lang") == lang:
            collecting = []
        else:
            inside_other = True
    return [block for block in blocks if block.strip()]


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _doc_config() -> Any:
    """The one YAML block in the doc. Exactly one, so "which block" is never a judgement."""
    blocks = _fenced_blocks(_doc_text(), lang="yaml")
    assert len(blocks) == 1, f"{DOC.name} should carry exactly one yaml block, found {len(blocks)}"
    return yaml.safe_load(blocks[0])


def _rig_config() -> Any:
    return yaml.safe_load(RIG_CONFIG.read_text(encoding="utf-8"))


def _example_prompt() -> str:
    blocks = _fenced_blocks(_doc_text(), lang=PROMPT_FENCE_LANG, after=PROMPT_HEADING)
    assert blocks, f"no ```{PROMPT_FENCE_LANG} block under `{PROMPT_HEADING}`"
    return blocks[0]


def config_problems(config: Any) -> list[str]:
    """Everything the three-key rule and the operator doc require of an `mcpServers.noa` entry that
    this one lacks.

    A list rather than a bool: the negative controls assert that a *specific* mutation is what
    turns red, and a bare `False` would let a broken parse stand in for a broken config.
    """
    problems: list[str] = []
    document = config if isinstance(config, dict) else {}

    entry = (document.get("mcpServers") or {}).get("noa")
    if not isinstance(entry, dict):
        return ["no `mcpServers.noa` entry"]

    if entry.get("type") != "streamable-http":
        problems.append(f"transport is {entry.get('type')!r}, not streamable-http")

    url = entry.get("url")
    if not isinstance(url, str) or not url.endswith("/"):
        problems.append(f"url {url!r} does not end in a slash — the bare path costs a 307")

    # `is not False`, not falsiness: an absent key is the default that breaks, and `startup: 0`
    # is not what LibreChat's schema takes.
    if entry.get("startup") is not False:
        problems.append("`startup: false` missing — boot inspect ends in zero tools")
    if entry.get("requiresOAuth") is not False:
        problems.append("`requiresOAuth: false` missing — a correct 401 reads as OAuth")

    domains = (document.get("mcpSettings") or {}).get("allowedDomains") or []
    url_host = urlsplit(url).hostname if isinstance(url, str) else None
    if url_host is None:
        problems.append(f"url {url!r} carries no host to allowlist")
    elif url_host not in domains:
        problems.append(f"{url_host!r} — the url's own host — not in mcpSettings.allowedDomains")

    headers = entry.get("headers") or {}
    authorization = headers.get("Authorization")
    if not isinstance(authorization, str) or "Bearer " not in authorization:
        problems.append(f"Authorization header is {authorization!r}, not a bearer token")
    for name, placeholder in REQUIRED_HEADERS.items():
        if headers.get(name) != placeholder:
            problems.append(f"header {name} is {headers.get(name)!r}, not {placeholder!r}")

    return problems


# --- the three config keys, on both artifacts ---


def test_doc_config_carries_the_keys_c24_forces() -> None:
    """The three-key rule, on the page an operator copies from: it is the page that is checked."""
    assert config_problems(_doc_config()) == []


def test_rig_config_carries_the_keys_c24_forces() -> None:
    """The same predicate against the config the render-gate's live run actually ran under.

    Two artifacts is the point: prose that no longer matches the measured config becomes two
    truths, and this is the moment it can be caught.
    """
    assert config_problems(_rig_config()) == []


def test_doc_url_is_noas_mcp_mount() -> None:
    """`/mcp/` answers, the bare path 307s. The rig points at its probe mount instead, so
    this half is the doc's alone."""
    url = _doc_config()["mcpServers"]["noa"]["url"]
    assert url.endswith(MCP_PATH_SUFFIX)


def test_doc_declares_the_token_as_a_masked_per_user_var() -> None:
    """One token per operator, entered by them, masked in the UI.

    `sensitive` defaults to masked upstream and is stated anyway — a default that flips would
    unmask a credential, and the explicit key costs one word.
    """
    variables = _doc_config()["mcpServers"]["noa"]["customUserVars"]
    token = variables["NOA_MCP_TOKEN"]

    assert token["sensitive"] is True
    assert token["title"].strip()
    assert token["description"].strip()


# --- proof each rule separates ---


def _mutate(config: dict[str, Any], mutation: str) -> dict[str, Any]:
    """One broken copy of a good config. Each mutation is a real-world edit, not noise."""
    broken = deepcopy(config)
    entry = broken["mcpServers"]["noa"]
    if mutation == "startup-true":
        entry["startup"] = True
    elif mutation == "startup-absent":
        del entry["startup"]
    elif mutation == "requires-oauth-absent":
        del entry["requiresOAuth"]
    elif mutation == "no-allowlist":
        broken["mcpSettings"]["allowedDomains"] = []
    elif mutation == "allowlist-names-another-host":
        # The hole a fixed `REQUIRED_DOMAIN` left open: an allowlist naming *a* plausible NOA host
        # while `url` points at a different one. LibreChat refuses the URL it was handed, so a
        # non-empty allowlist is not the same fact as a covering one.
        broken["mcpSettings"]["allowedDomains"] = ["noa-api.example.invalid"]
    elif mutation == "user-header-dropped":
        del entry["headers"]["X-Noa-LibreChat-User"]
    elif mutation == "conversation-header-blank":
        # LibreChat substitutes "" for a missing body field, and blank is not a label.
        entry["headers"]["X-Noa-Conversation-Ref"] = ""
    elif mutation == "url-without-slash":
        entry["url"] = entry["url"].rstrip("/")
    else:  # pragma: no cover - a typo in the parametrisation, not a config state
        raise AssertionError(f"unknown mutation {mutation!r}")
    return broken


@pytest.mark.parametrize(
    "mutation",
    [
        "startup-true",
        "startup-absent",
        "requires-oauth-absent",
        "no-allowlist",
        "allowlist-names-another-host",
        "user-header-dropped",
        "conversation-header-blank",
        "url-without-slash",
    ],
)
def test_the_predicate_separates(mutation: str) -> None:
    """Without this, a key-path typo passes against every config ever written."""
    assert config_problems(_mutate(_doc_config(), mutation)) != []


def test_a_missing_entry_is_not_a_pass() -> None:
    """The vacuous case on its own: no server entry at all must not read as a clean config."""
    assert config_problems({}) != []
    assert config_problems(None) != []


# --- the example prompt is there at all ---


def test_example_prompt_is_present_and_substantial() -> None:
    """Guard on the extraction before anything is asserted about its contents.

    An empty string satisfies every "does not contain" check below, so the check that matters is
    that there is something there at all — the setup gate must not be the thing under test.
    """
    prompt = _example_prompt()
    assert len(prompt) > 500
    # The clauses the example prompt owes, each identifiable without pinning wording.
    assert "noa_get_action_result" in prompt
    assert "UI Resource Marker" in prompt


# --- the example prompt tells the model how to read a pasted lookup block ---

# Two things the Proxmox paste clause has to mention, each a *mapping* the model cannot infer and
# neither a pinned sentence:
#
#   * `Host Node` — the block's field name, sent as both `node` and `server_ref`;
#   * `cloud-init` — the `username` the block does not carry, and which therefore comes from the
#     operator rather than from the contact address sitting in the block.
#
# It is asserted at all because Proxmox has no discovery tool: `whm_list_servers` has no Proxmox
# sibling, so a model that reads a pasted block wrongly has no read to recover with.
#
# **What this set binds, and what it does not.** It binds vocabulary: the clause is present and
# still talks about both things. It does **not** bind *direction* — a clause rewritten to invert
# the rule still carries every marker, because the difference is prose, and pinning the sentence
# would turn every reword into a false failure. The direction is bound on the code side instead,
# where it is no longer the tool description's job: `test_proxmox_server_ref.py` asserts the
# resolver itself derives a server from a node name. A reviewer reads this clause; the suite reads
# the mechanism.
PASTE_CLAUSE_MARKERS: tuple[str, ...] = ("Host Node", "cloud-init")


def paste_clause_problems(prompt: str) -> list[str]:
    """Which markers of the paste clause `prompt` is missing.

    A list for the same argument `config_problems` is one: the separating test below asserts that a
    *specific* absence is what turns it red.
    """
    return [marker for marker in PASTE_CLAUSE_MARKERS if marker not in prompt]


def test_example_prompt_carries_the_proxmox_paste_clause() -> None:
    assert paste_clause_problems(_example_prompt()) == []


@pytest.mark.parametrize("marker", PASTE_CLAUSE_MARKERS)
def test_the_paste_clause_check_separates(marker: str) -> None:
    """Without this, a clause quietly dropped from the doc reads the same as one still there.

    The mutation is to a copy of the doc's *own* text — the lines carrying one marker are removed —
    rather than to the assertion, which is `_mutate`'s shape above: break the production artifact
    and require red, never weaken the check.
    """
    without_marker = "\n".join(
        line for line in _example_prompt().splitlines() if marker not in line
    )

    assert paste_clause_problems(without_marker) != []


# --- the example prompt names no justification field ---


def test_example_prompt_never_names_a_reason_field() -> None:
    """The word is born on the card, at decision time.

    Reusing `FORBIDDEN_REASON_KEYS` rather than a local list — the schemas' forbidden set and
    model-facing text's forbidden set are one decision, and the model-facing safety text was
    widened at the change-gate response builder precisely because they are the same surface one
    step apart.
    """
    prompt = _example_prompt().lower()

    named = sorted(key for key in FORBIDDEN_REASON_KEYS if key in prompt)
    assert named == [], (
        f"the example prompt names {named} — a model told the field exists is a model that can "
        "be argued into filling it"
    )


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_REASON_KEYS))
def test_the_prompt_check_separates(forbidden: str) -> None:
    """Proof this separates, for the reason-field half: an injected field name has to be found,
    for every spelling."""
    injected = f"{_example_prompt()}\n\nAlways state the {forbidden} for the change.".lower()

    assert forbidden in [key for key in FORBIDDEN_REASON_KEYS if key in injected]
