#!/usr/bin/env bash
# First-time setup. Safe to re-run: it never overwrites your contacts or .env
# values, it only fills in what is missing or what you answer here.
#
#   scripts/setup.sh                      ask the questions
#   scripts/setup.sh --confirm yes|no     ask before each send? (default yes)
#   scripts/setup.sh --whatsapp|--no-whatsapp
#   scripts/setup.sh --no-install         skip the Python install (for tests)
#
# Linux only (including WSL). Needs uv; WhatsApp also needs Go, gcc and systemd.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

confirm="" whatsapp="" install=1
while [ $# -gt 0 ]; do
  case "$1" in
    --confirm)      confirm="${2:-}"; shift ;;
    --whatsapp)     whatsapp=yes ;;
    --no-whatsapp)  whatsapp=no ;;
    --no-install)   install=0 ;;
    *)              sed -n '2,11p' "$0"; exit 2 ;;
  esac
  shift
done

ask() {  # ask "question" default(y|n): prints yes or no. Only a clear opposite answer changes the default.
  local answer
  read -r -p "$1 " answer || true
  if [ "$2" = y ]; then
    case "$answer" in [Nn]*) echo no ;; *) echo yes ;; esac
  else
    case "$answer" in [Yy]*) echo yes ;; *) echo no ;; esac
  fi
}

set_env() {  # set_env NAME value: replace the line if present, else append
  touch .env && chmod 600 .env
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

[ "$(uname -s)" = Linux ] || { echo "This runs on Linux (including WSL) only."; exit 1; }

if [ "$install" = 1 ]; then
  command -v uv >/dev/null || { echo "uv not found: https://docs.astral.sh/uv/"; exit 1; }
  [ -d .venv ] || uv venv
  uv pip install -q -e '.[dev]'
  git config core.hooksPath .githooks 2>/dev/null || true
fi

[ -f config/contacts.json ] || { cp config/contacts.example.json config/contacts.json; echo "Created config/contacts.json from the example. Edit it."; }

# 1. Confirmation. A sent message cannot be recalled, so asking is the default.
if [ -z "$confirm" ]; then
  echo
  echo "Should the assistant show you each message and wait for a yes before sending?"
  echo "Say no only if you want 'tell Sam I'm running late' to send immediately."
  confirm="$(ask "Confirm before every send? [Y/n]" y)"
fi
case "$confirm" in yes|no) ;; *) echo "--confirm takes yes or no"; exit 2 ;; esac
set_env MESSENGER_CONFIRM_SENDS "$confirm"

# 2. Discord: nothing to install, just a token.
grep -q '^DISCORD_BOT_TOKEN=' .env || {
  echo
  echo "Discord: put your bot's token in .env as DISCORD_BOT_TOKEN=... (skip to leave Discord off)."
}

# 3. WhatsApp: optional, and the part with real risk.
if [ -z "$whatsapp" ]; then
  echo
  echo "WhatsApp uses an unofficial client (whatsmeow). WhatsApp's terms forbid those,"
  echo "and accounts do get banned, mostly for bulk or automated sending."
  whatsapp="$(ask "Set up WhatsApp anyway? [y/N]" n)"
fi
if [ "$whatsapp" = yes ]; then
  scripts/install-bridge.sh
  unit="$HOME/.config/systemd/user/whatsapp-bridge.service"
  mkdir -p "$(dirname "$unit")"
  sed "s|@ROOT@|$ROOT|g" config/whatsapp-bridge.service.in > "$unit"
  systemctl --user daemon-reload
  systemctl --user enable --now whatsapp-bridge
  echo "Bridge installed. Pair your phone with: scripts/pair.sh"
fi

echo
echo "Done. Confirm before sending: $confirm. Register the server with your client:"
echo "  Claude Code:     claude mcp add --scope user messenger -- $ROOT/scripts/run-server.sh"
if [ -n "${WSL_DISTRO_NAME:-}" ]; then
  echo "  Claude Desktop (Windows, via WSL), in claude_desktop_config.json:"
  echo "    \"messenger\": { \"command\": \"wsl.exe\", \"args\": [\"-d\", \"$WSL_DISTRO_NAME\", \"--\", \"$ROOT/scripts/run-server.sh\"] }"
else
  echo "  Claude Desktop, in claude_desktop_config.json:"
  echo "    \"messenger\": { \"command\": \"$ROOT/scripts/run-server.sh\" }"
fi
echo "Then check it: scripts/doctor.py"
