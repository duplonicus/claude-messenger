#!/usr/bin/env bash
# MCP Inspector: the protocol's own test UI. Lists the tools, lets you call
# them by hand and shows every message. First stop when something is off.
#
#   scripts/inspect.sh         open the web UI (prints a localhost URL)
#   scripts/inspect.sh list    print the tool list and exit, no browser
#
# Careful: calling a send tool in the Inspector really sends the message.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="@modelcontextprotocol/inspector@2.9.0"   # pinned; bump on purpose

if [ "${1:-}" = "list" ]; then
  exec npx -y "$PKG" --cli "$ROOT/scripts/run-server.sh" --method tools/list
fi
exec npx -y "$PKG" "$ROOT/scripts/run-server.sh"
