#!/usr/bin/env bash
# Claude Desktop's own view of the messenger server: connects, disconnects,
# every message exchanged, and whatever the server wrote to stderr.
#
#   scripts/desktop-logs.sh           follow the server's log
#   scripts/desktop-logs.sh errors    just disconnects and errors, with times
#   scripts/desktop-logs.sh app       the app's lifecycle lines for this server
#
# Run from WSL. Looks in %LOCALAPPDATA%\Claude\Logs (the MCP docs name
# %APPDATA%\Claude\logs, which newer installs no longer write to). Set
# CLAUDE_DESKTOP_LOGS to point somewhere else.
set -euo pipefail
LOGS="${CLAUDE_DESKTOP_LOGS:-$(ls -d /mnt/c/Users/*/AppData/Local/Claude/Logs 2>/dev/null | head -n 1)}"
[ -d "$LOGS" ] || { echo "Claude Desktop's log folder not found; set CLAUDE_DESKTOP_LOGS" >&2; exit 1; }
SERVER_LOG="$LOGS/mcp-server-messenger.log"

case "${1:-follow}" in
  follow) exec tail -n 40 -F "$SERVER_LOG" ;;
  errors) grep -aE '\[error\]|closed unexpectedly|disconnected|"level": "(error|warning)"' "$SERVER_LOG" | tail -n 40 ;;
  app)    grep -a 'messenger' "$LOGS/main.log" | grep -av UtilityProcess | tail -n 40 ;;
  *)      sed -n '2,10p' "$0"; exit 2 ;;
esac
