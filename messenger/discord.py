"""Discord bot DMs: open (or fetch) the DM channel, then post to it."""

import httpx

API = "https://discord.com/api/v10"
SIGNATURE = "-claude"
MAX_CONTENT = 2000

# https://discord.com/developers/docs/topics/opcodes-and-status-codes#json
CANNOT_DM = 50007
UNKNOWN_USER = 10013


class DiscordError(Exception):
    pass


def sign(text: str) -> str:
    """Messages come from the bot, so each one says who wrote it."""
    return f"{text.rstrip()} {SIGNATURE}"


def _explain(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("code")
    if code == CANNOT_DM:
        return (
            "Discord says: cannot send messages to this user. They don't share "
            "a server with the bot, have DMs from server members turned off, "
            "or have blocked the bot."
        )
    if code == UNKNOWN_USER:
        return "Discord says: unknown user. Check the user ID in the contacts file."
    if response.status_code == 401:
        return "Discord rejected the bot token (401). Check DISCORD_BOT_TOKEN in .env."
    if response.status_code == 429:
        return f"Discord rate limit hit; retry in {body.get('retry_after', '?')}s."
    return f"Discord error {response.status_code}: {body.get('message', 'no detail')}"


def send_dm(token: str, user_id: str, text: str, client: httpx.Client | None = None) -> str:
    """Send `text` (signed) to `user_id`. Returns the message ID."""
    if not token:
        raise DiscordError("DISCORD_BOT_TOKEN is not set in .env")
    content = sign(text)
    if len(content) > MAX_CONTENT:
        raise DiscordError(
            f"message is {len(content)} characters; Discord's limit is {MAX_CONTENT}"
        )

    owns_client = client is None
    client = client or httpx.Client(timeout=15)
    headers = {"Authorization": f"Bot {token}"}
    try:
        dm = client.post(
            f"{API}/users/@me/channels", headers=headers, json={"recipient_id": user_id}
        )
        if dm.status_code >= 400:
            raise DiscordError(_explain(dm))
        channel_id = dm.json()["id"]

        msg = client.post(
            f"{API}/channels/{channel_id}/messages",
            headers=headers,
            json={"content": content},
        )
        if msg.status_code >= 400:
            raise DiscordError(_explain(msg))
        return msg.json()["id"]
    except httpx.HTTPError as exc:
        # Never str(exc.request): it carries the Authorization header.
        raise DiscordError(f"could not reach Discord: {type(exc).__name__}") from None
    finally:
        if owns_client:
            client.close()
