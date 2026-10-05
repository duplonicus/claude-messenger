"""The tools as the app sees them: plain words in, plain sentence out."""

import asyncio

import pytest

from messenger import discord, server, whatsapp
from tests.conftest import CHATS


@pytest.fixture
def wired(store, monkeypatch):
    """The fake store from conftest, with real sends replaced by a recorder."""
    log = store / "logs" / "sent.log"
    monkeypatch.setattr(server, "SEND_LOG", log)
    monkeypatch.setattr(server, "load_env", lambda: {"DISCORD_BOT_TOKEN": "tok"})

    sent = []
    monkeypatch.setattr(whatsapp, "send", lambda jid, text: sent.append(("whatsapp", jid, text)))
    monkeypatch.setattr(
        discord, "send_dm", lambda token, uid, text: sent.append(("discord", token, uid, text)) or "1"
    )
    return sent, log


def tool_schemas():
    return {t.name: sorted(t.inputSchema["properties"]) for t in asyncio.run(server.mcp.list_tools())}


def test_only_the_send_tools_are_marked_as_acting_on_the_world():
    hints = {t.name: (t.annotations.readOnlyHint, t.annotations.destructiveHint) for t in asyncio.run(server.mcp.list_tools())}
    assert hints == {
        "list_contacts": (True, None),
        "search_contacts": (True, None),
        "list_chats": (True, None),
        "messenger_status": (True, None),
        "send_whatsapp": (False, False),
        "send_discord_dm": (False, False),
    }


def test_server_is_started_with_one_of_the_two_instruction_sets():
    # Which one depends on MESSENGER_CONFIRM_SENDS; tests/test_setup.py covers the choice.
    assert server.mcp.instructions in {server.ALWAYS + server.CONFIRM_FIRST, server.ALWAYS + server.SEND_DIRECTLY}
    assert "Never send on your own initiative" in server.ALWAYS
    assert "IS the approval" in server.SEND_DIRECTLY and "Do not show a draft first" in server.SEND_DIRECTLY
    assert "wait for a clear yes" in server.CONFIRM_FIRST


def test_tool_surface():
    assert tool_schemas() == {
        "list_contacts": [],
        "search_contacts": ["query"],
        "list_chats": ["limit"],
        "send_whatsapp": ["text", "to"],
        "send_discord_dm": ["alias", "text"],
        "messenger_status": [],
    }


@pytest.mark.parametrize(
    "to, jid, reply",
    [
        ("Mom", "15550000001@s.whatsapp.net", "Sent to mom (15550000001@s.whatsapp.net) on WhatsApp."),
        ("zoe", "15550000002@s.whatsapp.net", "Sent to Zoe (15550000002@s.whatsapp.net) on WhatsApp."),
        ("band chat", "120363000000000001@g.us", "Sent to Band Chat (120363000000000001@g.us) on WhatsApp."),
        ("447700900123", "447700900123@s.whatsapp.net", "Sent to +447700900123 (447700900123@s.whatsapp.net) on WhatsApp."),
    ],
)
def test_whatsapp_send_by_alias_name_group_or_number(wired, to, jid, reply):
    sent, log = wired
    assert server.send_whatsapp(to, "want to get together?") == reply
    assert sent == [("whatsapp", jid, "want to get together?")]
    assert log.read_text().split()[1:] == ["whatsapp", jid, "chars=21", "sent"]


def test_ambiguous_name_sends_nothing_and_returns_the_matches(wired):
    sent, log = wired
    result = server.send_whatsapp("sam c", "hi")  # unique: only Sam Carter
    assert sent == [("whatsapp", "15550000003@s.whatsapp.net", "hi")]
    sent.clear()

    result = server.send_whatsapp("carol b", "hi")
    assert result.startswith("Sent to Carol Baker")
    sent.clear()

    result = server.send_whatsapp("Sam", "hi")  # the alias "sam" wins: no ambiguity
    assert sent == [("whatsapp", "15550000004@s.whatsapp.net", "hi")]
    sent.clear()

    result = server.send_whatsapp("sa", "hi")  # three Sams, no exact name, no alias
    assert sent == []
    assert result.startswith("Not sent: 'sa' matches 3 WhatsApp contacts.")
    for line in (
        "Sam Carter - +15550000003 - 15550000003@s.whatsapp.net",
        "Sam Okafor - +15550000004 - 15550000004@s.whatsapp.net",
        "Samantha - +15550000005 - 15550000005@s.whatsapp.net",
    ):
        assert line in result
    assert log.read_text().splitlines()[-1].split()[1:] == ["whatsapp", "sa", "chars=2", "FAILED"]


def test_unknown_whatsapp_recipient_sends_nothing(wired):
    sent, _ = wired
    assert server.send_whatsapp("bob", "hi").startswith("Not sent: no WhatsApp contact or chat matches 'bob'")
    assert sent == []


def test_search_contacts_lists_name_number_and_jid(wired):
    assert server.search_contacts("sam") == (
        "Sam Carter - +15550000003 - 15550000003@s.whatsapp.net\n"
        "Sam Okafor - +15550000004 - 15550000004@s.whatsapp.net\n"
        "Samantha - +15550000005 - 15550000005@s.whatsapp.net"
    )
    assert server.search_contacts("band") == "Band Chat - group - 120363000000000001@g.us"
    assert server.search_contacts("nobody") == "No WhatsApp contact or chat matches 'nobody'."


def test_search_contacts_caps_long_results(wired, monkeypatch):
    monkeypatch.setattr(server, "MAX_RESULTS", 2)
    result = server.search_contacts("sam").splitlines()
    assert len(result) == 3 and result[-1] == "...and 1 more; narrow the search."


def test_list_chats_is_newest_first(wired):
    assert server.list_chats(2) == (
        "2026-10-03 22:00:00  Band Chat - group - 120363000000000001@g.us\n"
        "2026-10-03 21:00:00  Zoe - +15550000002 - 15550000002@s.whatsapp.net"
    )


def test_list_chats_never_returns_message_text(wired):
    # The fake store has no messages table at all: list_chats must not need one.
    assert len(server.list_chats(50).splitlines()) == len(CHATS)


def test_discord_send_resolves_alias_to_user_id(wired):
    sent, _ = wired
    assert server.send_discord_dm("zoe-discord", "there in a minute") == "Sent to zoe-discord on Discord."
    assert sent == [("discord", "tok", "123456789012345678", "there in a minute")]


@pytest.mark.parametrize(
    "alias, reason",
    [
        ("mom", "'mom' is a whatsapp contact, not discord"),
        ("bob", "no contact named 'bob'"),
        ("999999999999999999", "no contact named"),  # Discord stays alias-only
    ],
)
def test_discord_refuses_anything_but_a_discord_alias(wired, alias, reason):
    sent, log = wired
    result = server.send_discord_dm(alias, "hi")
    assert result.startswith("Not sent: ") and reason in result
    assert sent == []
    assert log.read_text().split()[-1] == "FAILED"


def test_log_never_holds_message_text_and_stays_one_token_per_field(wired):
    _, log = wired
    server.send_whatsapp("mom", "a very private sentence")
    server.send_whatsapp("someone with spaces", "another private sentence")
    text = log.read_text()
    assert "private" not in text
    assert [len(line.split()) for line in text.splitlines()] == [5, 5]


@pytest.mark.parametrize("tool, to", [("send_whatsapp", "mom"), ("send_discord_dm", "zoe-discord")])
def test_empty_message_is_refused(wired, tool, to):
    sent, _ = wired
    assert getattr(server, tool)(to, "  \n") == "Error: empty message"
    assert sent == []


def test_discord_failure_reaches_the_user_verbatim(wired, monkeypatch):
    def boom(token, uid, text):
        raise discord.DiscordError("Discord says: cannot send messages to this user. ...")

    monkeypatch.setattr(discord, "send_dm", boom)
    assert server.send_discord_dm("zoe-discord", "hi").startswith(
        "Not sent: Discord says: cannot send messages to this user."
    )


def test_list_contacts_shows_platform_but_not_ids(wired):
    assert server.list_contacts() == "mom: whatsapp\nplumber: whatsapp\nsam: whatsapp\nzoe-discord: discord"
