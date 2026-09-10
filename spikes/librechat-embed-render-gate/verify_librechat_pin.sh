#!/usr/bin/env bash
# E1 — the rig is the pin C21 measured, and the render path still reads the way R11-R13 say.
#
# Every assertion here runs against *artifacts on disk* rather than against GitHub. That is
# the point: C21's chain was established by reading upstream, and V69 is the rule that says a
# read is not evidence the mechanism behaves. This script proves the tree under test is the
# one the SPEC names, so the live run that follows can be attributed to that pin.
#
# It also runs V88's config binding before touching the clone, so "the three keys are
# there" is a check rather than a habit.
#
#   spikes/librechat-embed-render-gate/verify_librechat_pin.sh [clone-dir]
#
# Exits non-zero on the first failed assertion.

set -euo pipefail

CLONE_DIR="${1:-/home/ubuntu/noa/librechat-embed-render-gate}"
PIN="45cc53c40b47645b887c3bb996168e06aaa83f4c"
MCP_UI_VERSION="5.7.0"
SDK_VERSION="1.29.0"

fail() { printf 'FAIL  %s\n' "$1" >&2; exit 1; }
pass() { printf 'ok    %s\n' "$1"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# V88's three keys, the header set and the trailing slash, asserted against this directory's
# `librechat.yaml` *and* against the config block of `docs/integrations/librechat.md`.
# First, and before the clone gate, because it needs no clone: a config or doc drift should be
# catchable without 2 GB of LibreChat on disk. The predicate itself lives in the test rather than
# here so there is one copy of it; this is where C21's trigger reaches it.
# A subshell, so the script's own working directory is still the caller's afterwards.
(cd "$REPO_ROOT" && uv run pytest -q apps/api/tests/test_librechat_config_doc.py) \
  || fail "config binding red — see apps/api/tests/test_librechat_config_doc.py"
pass "librechat.yaml + docs/integrations/librechat.md carry V88's keys"

[ -d "$CLONE_DIR" ] || fail "no clone at $CLONE_DIR"

head_sha="$(git -C "$CLONE_DIR" rev-parse HEAD)"
[ "$head_sha" = "$PIN" ] || fail "clone HEAD is $head_sha, not the pin $PIN"
pass "clone HEAD == $PIN"

# R11: the versions are locked, not merely declared with a caret.
lock_versions="$(python3 - "$CLONE_DIR" <<'PY'
import json, sys, pathlib
lock = json.loads((pathlib.Path(sys.argv[1]) / "package-lock.json").read_text())
pkgs = lock["packages"]
print(pkgs["node_modules/@mcp-ui/client"]["version"])
print(pkgs["node_modules/@modelcontextprotocol/sdk"]["version"])
PY
)"
locked_mcp_ui="$(printf '%s\n' "$lock_versions" | sed -n 1p)"
locked_sdk="$(printf '%s\n' "$lock_versions" | sed -n 2p)"
[ "$locked_mcp_ui" = "$MCP_UI_VERSION" ] || fail "@mcp-ui/client locked at $locked_mcp_ui, not $MCP_UI_VERSION"
[ "$locked_sdk" = "$SDK_VERSION" ] || fail "@modelcontextprotocol/sdk locked at $locked_sdk, not $SDK_VERSION"
pass "lockfile: @mcp-ui/client $locked_mcp_ui, @modelcontextprotocol/sdk $locked_sdk"

installed="$CLONE_DIR/node_modules/@mcp-ui/client"
[ -f "$installed/dist/index.mjs" ] || fail "@mcp-ui/client not installed (run npm ci first)"
installed_version="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$installed/package.json")"
[ "$installed_version" = "$MCP_UI_VERSION" ] || fail "installed @mcp-ui/client is $installed_version"
pass "installed @mcp-ui/client == $installed_version"

# R12: `text/uri-list` is still a branch in the shipped bytes, not only in the repo source.
grep -q 'text/uri-list' "$installed/dist/index.mjs" || fail "no text/uri-list branch in shipped bytes"
pass "shipped bytes still branch on text/uri-list"

# R13 / V80: same-origin granted, forms not. The second half is the one V80 depends on, and
# it is asserted as an absence — if a bump adds `allow-forms`, V80's reason evaporates and
# this line is where that gets noticed.
grep -q 'allow-scripts allow-same-origin' "$installed/dist/index.mjs" \
  || fail "shipped bytes no longer merge 'allow-scripts allow-same-origin'"
pass "shipped bytes merge 'allow-scripts allow-same-origin'"
if grep -q 'allow-forms' "$installed/dist/index.mjs"; then
  fail "shipped bytes now mention allow-forms — V80's premise changed, re-read R13"
fi
pass "shipped bytes never mention allow-forms (V80 premise holds)"

# V69: the shipped @mcp-ui/client bytes, not the repo source, are what actually runs — same
# standard as the text/uri-list and allow-forms checks above. V69's own subject is a ported SSH
# host-key control; its RULE — upstream provenance is not evidence a control works, so a control
# needs a test against the real mechanism — is applied here one surface over, to shipped bytes
# versus the source they were published from. The real mechanism is what npm installed.
#
# Frame sizing needs TWO things from these bytes, and either check alone is blind to the other:
#
#   1. `ui-size-change` — the postMessage the frame-sizing work reads to learn the rendered UI's
#      size. Drop the handler and sizing dies inside the library, where a read of LibreChat's own
#      source would never catch it.
#   2. `autoResizeIframe` — the option LibreChat passes at each of the 3 render sites checked
#      below. The library writes the reported size onto the iframe only when that option is set.
#      A bump that renames or drops it severs the wiring between "LibreChat asks for resize" and
#      "the library honours the ask" while leaving check 1 AND all 3 render-site checks green:
#      the protocol constant is untouched and LibreChat's own source is untouched.
#
# The option name is the only stable anchor for (2). The gate that consumes it is minified — at
# 5.7.0 the destructured binding is a single letter — so a pattern naming the binding would break
# on any rebuild without the property having changed.
#
# C21's re-verify-on-bump duty is what these two lines discharge.
grep -q 'ui-size-change' "$installed/dist/index.mjs" \
  || fail "shipped bytes no longer handle the ui-size-change message"
pass "shipped bytes still handle ui-size-change"
grep -q 'autoResizeIframe' "$installed/dist/index.mjs" \
  || fail "shipped bytes no longer accept the autoResizeIframe option"
pass "shipped bytes still accept the autoResizeIframe option"

# R15: the draft that would kill the uri-list path is not in this tree.
if grep -rq '@modelcontextprotocol/ext-apps' "$CLONE_DIR/client/package.json" 2>/dev/null; then
  fail "client depends on ext-apps — PR #13831 landed, C21's drift source is live"
fi
pass "no ext-apps dependency (PR #13831 still out of tree)"

# C21 / R12: R12 names these 3 sites as where LibreChat delegates render mode to
# `UIResourceRenderer`; `autoResizeIframe` is what makes the frame the frame-sizing work
# depends on report its own size instead of a fixed one. A bump can drop the prop at one site
# without touching the others, so each site is its own assertion — a single combined grep
# would tell an operator sizing broke somewhere without saying where to look.
#
# The pattern is value-aware, and has to be. At this pin the key and its value sit on ONE line,
# `autoResizeIframe: { width: true, height: true },` — so a grep for the bare token still passes
# when the value is flipped to `false`, and still passes when the whole line is commented out.
# Both disable sizing while leaving the token in the file. Hence: the line anchor allows only
# whitespace before the key, which rules out a `//` or `/*` prefix, and both axes must read
# `true`.
#
# Two known bounds, both from grep being line-oriented. It misses a MULTI-line block comment
# wrapping an unchanged line, which still matches; catching that needs a parser, not a pattern,
# and the live browser run this harness supports is what would catch it. And it fires on a
# LibreChat that merely REFORMATS the option across several lines — a red gate there means the
# pattern is stale, not that the render path is broken, so the fix is to re-read the source at
# the new pin and re-anchor the pattern to it, never to loosen it back toward a bare-token grep.
autoresize_re='^[[:space:]]*autoResizeIframe:[[:space:]]*\{[[:space:]]*width:[[:space:]]*true,[[:space:]]*height:[[:space:]]*true[[:space:]]*\}'
assert_autoresize_site() {
  local file="$1" label="$2"
  [ -f "$file" ] || fail "missing $file"
  grep -Eq "$autoresize_re" "$file" \
    || fail "$label no longer passes autoResizeIframe with both axes enabled (dropped, commented out, or width/height not true)"
}
assert_autoresize_site \
  "$CLONE_DIR/client/src/components/MCPUIResource/MCPUIResource.tsx" "MCPUIResource.tsx"
assert_autoresize_site \
  "$CLONE_DIR/client/src/components/Chat/Messages/Content/ToolCallInfo.tsx" "ToolCallInfo.tsx"
assert_autoresize_site \
  "$CLONE_DIR/client/src/components/Chat/Messages/Content/UIResourceCarousel.tsx" "UIResourceCarousel.tsx"
pass "autoResizeIframe still passed with both axes enabled at all 3 render sites (MCPUIResource, ToolCallInfo, UIResourceCarousel)"

# Item (f), source side: which list-changed notifications the client subscribes to.
conn="$CLONE_DIR/packages/api/src/mcp/connection.ts"
[ -f "$conn" ] || fail "missing $conn"
if grep -q 'ToolListChangedNotificationSchema' "$conn"; then
  pass "client registers a tools/list_changed handler"
else
  pass "client registers NO tools/list_changed handler (only resources — see report)"
fi

printf '\nE1 green: rig is pin %s\n' "$PIN"
