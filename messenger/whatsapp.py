"""WhatsApp via the local bridge (vendor/whatsapp-mcp). Loopback only."""

from pathlib import Path

import httpx

from .paths import BRIDGE_TOKEN_FILE

# Loopback literal, not "localhost": nothing here should ever resolve a name.
BRIDGE_URL = "http://127.0.0.1:8080/api"


class WhatsAppError(Exception):
    pass


def _token(path: Path = BRIDGE_TOKEN_FILE) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        raise WhatsAppError(
            "no bridge token yet - the WhatsApp bridge has never started. "
            "Run: systemctl --user start whatsapp-bridge"
        ) from None


def _down() -> WhatsAppError:
    # The bridge only opens its port once it is paired and connected.
    return WhatsAppError(
        "the WhatsApp bridge is not answering - it is stopped, or waiting for "
        "a QR scan. Check: systemctl --user status whatsapp-bridge"
    )


def health(client: httpx.Client | None = None, token_file: Path = BRIDGE_TOKEN_FILE) -> str:
    owns_client = client is None
    client = client or httpx.Client(timeout=5)
    try:
        headers = {"Authorization": f"Bearer {_token(token_file)}"}
        response = client.get(f"{BRIDGE_URL}/health", headers=headers)
    except WhatsAppError as exc:
        return str(exc)
    except httpx.HTTPError:
        return str(_down())
    finally:
        if owns_client:
            client.close()
    if response.status_code == 200:
        return "connected"
    if response.status_code == 503:
        return "bridge is up but not connected to WhatsApp (needs a QR scan, or still starting)"
    return f"bridge answered {response.status_code}"


def send(
    jid: str,
    text: str,
    client: httpx.Client | None = None,
    token_file: Path = BRIDGE_TOKEN_FILE,
) -> None:
    """Send `text` as the linked account. No signature: this is the user talking."""
    headers = {"Authorization": f"Bearer {_token(token_file)}"}
    owns_client = client is None
    client = client or httpx.Client(timeout=30)
    try:
        response = client.post(
            f"{BRIDGE_URL}/send", headers=headers, json={"recipient": jid, "message": text}
        )
    except httpx.HTTPError:
        raise _down() from None
    finally:
        if owns_client:
            client.close()

    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and body.get("success"):
        return
    if response.status_code == 401:
        raise WhatsAppError("the bridge rejected its own token (401); restart the bridge")
    detail = body.get("message") or response.text.strip() or "no detail"
    raise WhatsAppError(f"WhatsApp send failed ({response.status_code}): {detail}")
