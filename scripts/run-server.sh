#!/usr/bin/env bash
# Entry point for MCP clients (Claude Desktop via wsl.exe, Claude Code).
# stdout is the MCP channel: nothing here may print to it.
exec "$(dirname "${BASH_SOURCE[0]}")/../.venv/bin/python" -m messenger.server
