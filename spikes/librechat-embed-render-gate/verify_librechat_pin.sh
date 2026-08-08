#!/usr/bin/env bash
# E1 — the rig is the pin C21 measured, and the render path still reads the way R11-R13 say.
#
# Every assertion here runs against *artifacts on disk* rather than against GitHub. That is
# the point: C21's chain was established by reading upstream, and V69 is the rule that says a
# read is not evidence the mechanism behaves. This script proves the tree under test is the
# one the SPEC names, so the live run that follows can be attributed to that pin.
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

# R15: the draft that would kill the uri-list path is not in this tree.
if grep -rq '@modelcontextprotocol/ext-apps' "$CLONE_DIR/client/package.json" 2>/dev/null; then
  fail "client depends on ext-apps — PR #13831 landed, C21's drift source is live"
fi
pass "no ext-apps dependency (PR #13831 still out of tree)"

# Item (f), source side: which list-changed notifications the client subscribes to.
conn="$CLONE_DIR/packages/api/src/mcp/connection.ts"
[ -f "$conn" ] || fail "missing $conn"
if grep -q 'ToolListChangedNotificationSchema' "$conn"; then
  pass "client registers a tools/list_changed handler"
else
  pass "client registers NO tools/list_changed handler (only resources — see report)"
fi

printf '\nE1 green: rig is pin %s\n' "$PIN"
