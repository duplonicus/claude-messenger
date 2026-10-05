#!/usr/bin/env bash
# Clone the upstream WhatsApp bridge at a pinned commit and build it.
# Bump PIN deliberately: this code holds the WhatsApp session keys.
set -euo pipefail

REPO=https://github.com/verygoodplugins/whatsapp-mcp
PIN=895404542017f34a900f9f572a5497c275a96440   # v0.7.0, 2026-09-23

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/whatsapp-mcp"
# Use the Go on PATH; fall back to the usual install locations.
export PATH="$PATH:/usr/local/go/bin:$HOME/.local/opt/go/bin:$HOME/go/bin"

command -v go >/dev/null || { echo "Go 1.26+ not found. Install it from https://go.dev/dl/ and re-run."; exit 1; }
command -v gcc >/dev/null || { echo "gcc not found (the bridge's SQLite driver needs cgo). Install build-essential and re-run."; exit 1; }

[ -d "$DEST/.git" ] || git clone -q "$REPO" "$DEST"
git -C "$DEST" fetch -q origin
git -C "$DEST" checkout -q "$PIN"

cd "$DEST/whatsapp-bridge"
# readonly + local toolchain: build exactly what go.sum pins, download nothing extra.
GOFLAGS=-mod=readonly GOTOOLCHAIN=local CGO_ENABLED=1 go build -o whatsapp-bridge .
echo "built $DEST/whatsapp-bridge/whatsapp-bridge @ $(git -C "$DEST" log -1 --format='%h %s')"
