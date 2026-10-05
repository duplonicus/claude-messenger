"""Where things live. Everything is resolved from the repo root so the server
works no matter what cwd wsl.exe launches it in."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("MESSENGER_HOME") or Path(__file__).resolve().parents[1])

ENV_FILE = ROOT / ".env"
CONTACTS_FILE = ROOT / "config" / "contacts.json"
SEND_LOG = ROOT / "logs" / "sent.log"
SERVER_LOG = ROOT / "logs" / "server.log"

BRIDGE_DIR = ROOT / "vendor" / "whatsapp-mcp" / "whatsapp-bridge"
BRIDGE_TOKEN_FILE = BRIDGE_DIR / "store" / ".bridge-token"
# The bridge's own SQLite store: chats it has seen, and the synced address book.
MESSAGES_DB = BRIDGE_DIR / "store" / "messages.db"
WHATSMEOW_DB = BRIDGE_DIR / "store" / "whatsapp.db"


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse a plain NAME=value file. Real environment variables win."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip().strip("'\"")
    for name in list(values):
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values
