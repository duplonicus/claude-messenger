import json

import httpx
import pytest

from messenger import discord

TOKEN = "test-token-not-real"
USER = "123456789012345678"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_opens_dm_then_posts_signed_message():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), json.loads(request.content)))
        assert request.headers["Authorization"] == f"Bot {TOKEN}"
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "999"})
        return httpx.Response(200, json={"id": "555"})

    message_id = discord.send_dm(TOKEN, USER, "on my way", client_for(handler))

    assert message_id == "555"
    assert calls == [
        ("POST", "https://discord.com/api/v10/users/@me/channels", {"recipient_id": USER}),
        ("POST", "https://discord.com/api/v10/channels/999/messages", {"content": "on my way -claude"}),
    ]


@pytest.mark.parametrize(
    "text, expected",
    [("hi", "hi -claude"), ("hi\n", "hi -claude"), ("two\nlines  ", "two\nlines -claude")],
)
def test_sign(text, expected):
    assert discord.sign(text) == expected


@pytest.mark.parametrize("failing_path", ["/users/@me/channels", "/messages"])
def test_cannot_dm_is_said_plainly(failing_path):
    """50007 can come back from either call; both must read the same."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(failing_path):
            return httpx.Response(403, json={"code": 50007, "message": "Cannot send messages to this user"})
        return httpx.Response(200, json={"id": "999"})

    with pytest.raises(discord.DiscordError) as exc:
        discord.send_dm(TOKEN, USER, "hi", client_for(handler))
    assert str(exc.value).startswith("Discord says: cannot send messages to this user.")
    assert "share a server with the bot" in str(exc.value)


def test_bad_token_points_at_env():
    handler = lambda request: httpx.Response(401, json={"code": 0, "message": "401: Unauthorized"})
    with pytest.raises(discord.DiscordError, match=r"Check DISCORD_BOT_TOKEN in \.env"):
        discord.send_dm(TOKEN, USER, "hi", client_for(handler))


def test_missing_token_never_calls_discord():
    def handler(request):
        raise AssertionError("no request should be made without a token")

    with pytest.raises(discord.DiscordError, match="DISCORD_BOT_TOKEN is not set"):
        discord.send_dm("", USER, "hi", client_for(handler))


def test_over_length_is_refused_before_sending():
    def handler(request):
        raise AssertionError("no request should be made for an over-length message")

    limit = discord.MAX_CONTENT - len(" " + discord.SIGNATURE)
    with pytest.raises(discord.DiscordError, match="Discord's limit is 2000"):
        discord.send_dm(TOKEN, USER, "x" * (limit + 1), client_for(handler))


def test_exactly_at_the_limit_goes_through():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            sent["content"] = json.loads(request.content)["content"]
        return httpx.Response(200, json={"id": "1"})

    limit = discord.MAX_CONTENT - len(" " + discord.SIGNATURE)
    discord.send_dm(TOKEN, USER, "x" * limit, client_for(handler))
    assert len(sent["content"]) == discord.MAX_CONTENT


def test_network_failure_does_not_leak_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(discord.DiscordError) as exc:
        discord.send_dm(TOKEN, USER, "hi", client_for(handler))
    assert str(exc.value) == "could not reach Discord: ConnectError"
    assert TOKEN not in repr(exc.value)
    assert exc.value.__cause__ is None
