import pytest

from messenger import directory, paths
from messenger.contacts import ContactError
from tests.conftest import ADDRESS_BOOK, ARCHIVED, CHATS, LID_CONTACTS


def jids(entries):
    return [e.jid for e in entries]


def test_directory_is_one_entry_per_person(store):
    # Everyone in the address book and chats, plus the one linked ID with no
    # number behind it. Mapped linked IDs and archived chats add nothing.
    expected = {row[0] for row in ADDRESS_BOOK} | {row[0] for row in CHATS} | {"99900000000002@lid"}
    got = jids(directory.load_directory())
    assert len(got) == len(set(got))
    assert set(got) == expected


def test_linked_id_and_number_are_one_person(store):
    """WhatsApp lists Zoe twice (number + linked ID); that must not be two Zoes."""
    assert jids(directory.search("zoe")) == ["15550000002@s.whatsapp.net"]
    assert directory.resolve_recipient("zoe") == ("15550000002@s.whatsapp.net", "Zoe")


def test_linked_id_without_a_number_is_not_shown_as_a_phone_number(store):
    (lidia,) = directory.search("lidia")
    assert lidia.label() == "Lidia - no number known - 99900000000002@lid"


@pytest.mark.parametrize("settings_jid, chat_jid, name", ARCHIVED)
def test_archived_chats_cannot_be_found_or_listed(store, settings_jid, chat_jid, name):
    assert chat_jid not in jids(directory.load_directory())
    assert chat_jid not in jids(directory.list_chats(200))
    assert directory.search(name) == []
    assert directory.search("archi") == []
    with pytest.raises(ContactError, match="no WhatsApp contact or chat matches"):
        directory.resolve_recipient(name)


def test_archived_filter_removes_exactly_the_archived(store):
    # Archived rows are the newest chats in the fixture, so a leak shows first.
    assert jids(directory.list_chats(200)) == [r[0] for r in sorted(CHATS, key=lambda r: r[2], reverse=True)]


def test_a_chat_with_settings_but_not_archived_stays(store):
    assert "15550000002@s.whatsapp.net" in jids(directory.list_chats(200))


def test_naming_an_archived_chat_outright_still_reaches_it(store):
    assert directory.resolve_recipient("15550000011")[0] == "15550000011@s.whatsapp.net"
    assert directory.resolve_recipient("120363000000000002@g.us")[0] == "120363000000000002@g.us"


def test_saved_name_wins_and_a_bare_number_is_not_a_name(store):
    by_jid = {e.jid: e for e in directory.load_directory()}
    assert by_jid["15550000003@s.whatsapp.net"].name == "Sam Carter"
    assert by_jid["15550000003@s.whatsapp.net"].names == ("Sam Carter", "Sam", "sammy")
    assert by_jid["15550000008@s.whatsapp.net"].names == ()
    assert by_jid["15550000006@s.whatsapp.net"].name == ""


def test_list_chats_is_newest_first_and_honours_limit(store):
    assert jids(directory.list_chats(10)) == [row[0] for row in sorted(CHATS, key=lambda r: r[2], reverse=True)]
    assert jids(directory.list_chats(2)) == ["120363000000000001@g.us", "15550000002@s.whatsapp.net"]


def test_list_chats_names_each_chat_the_way_search_does(store):
    """The chats table only has numbers; names come from the shared directory."""
    assert [c.label() for c in directory.list_chats(10)] == [
        "Band Chat - group - 120363000000000001@g.us",
        "Zoe - +15550000002 - 15550000002@s.whatsapp.net",  # address book
        "+15550000008 - 15550000008@s.whatsapp.net",  # no name anywhere: number only
        "Carol Baker - +15550000001 - 15550000001@s.whatsapp.net",  # address book beats alias "mom"
        "+15550000006 - 15550000006@s.whatsapp.net",  # nameless address-book entry
        "plumber - +15550000009 - 15550000009@s.whatsapp.net",  # alias
        "Dee - +15550000010 - 15550000010@s.whatsapp.net",  # push name
    ]


def test_list_chats_and_search_agree_on_every_name(store):
    searched = {e.jid: e.name for e in directory.load_directory()}
    for chat in directory.list_chats(50):
        assert chat.name == searched[chat.jid], chat.jid


@pytest.mark.parametrize(
    "query, expected",
    [
        ("carol", ["15550000001@s.whatsapp.net"]),
        ("BAKER", ["15550000001@s.whatsapp.net"]),
        ("sammy", ["15550000003@s.whatsapp.net"]),  # push name
        ("band", ["120363000000000001@g.us"]),  # groups are searchable
        ("élodie", ["15559990007@s.whatsapp.net"]),  # non-ASCII case folding
        ("9990007", ["15559990007@s.whatsapp.net"]),  # by number
        ("nobody", []),
        ("  ", []),
        ("555", []),  # too few digits to be a number search
    ],
)
def test_search(store, query, expected):
    assert jids(directory.search(query)) == expected


def test_search_puts_exact_name_matches_first(store):
    # "Sam" is the whole first name of two people and a fragment of a third.
    assert jids(directory.search("sam")) == [
        "15550000003@s.whatsapp.net",
        "15550000004@s.whatsapp.net",
        "15550000005@s.whatsapp.net",
    ]


@pytest.mark.parametrize(
    "to, expected",
    [
        # alias first, even though "sam" also matches three address-book names
        ("sam", ("15550000004@s.whatsapp.net", "sam")),
        ("Mom", ("15550000001@s.whatsapp.net", "mom")),
        # a unique name
        ("Carol Baker", ("15550000001@s.whatsapp.net", "Carol Baker")),
        ("carol", ("15550000001@s.whatsapp.net", "Carol Baker")),
        ("band chat", ("120363000000000001@g.us", "Band Chat")),
        # an exact name beats names that merely contain it
        ("samantha", ("15550000005@s.whatsapp.net", "Samantha")),
        # JIDs and numbers go straight through, labelled with the saved name if any
        ("15550000002@s.whatsapp.net", ("15550000002@s.whatsapp.net", "Zoe")),
        ("+1 (555) 000-0002", ("15550000002@s.whatsapp.net", "Zoe")),
        ("120363000000000001@g.us", ("120363000000000001@g.us", "Band Chat")),
        # a number nobody has saved is still sendable
        ("447700900123", ("447700900123@s.whatsapp.net", "+447700900123")),
        # typed without the country code: finds the saved contact it belongs to
        ("555-000-0002", ("15550000002@s.whatsapp.net", "Zoe")),
    ],
)
def test_resolve_recipient(store, to, expected):
    assert directory.resolve_recipient(to) == expected


def test_ambiguous_first_name(store, monkeypatch):
    # Without the "sam" alias, "sam" is two people's exact first name.
    monkeypatch.setattr(paths, "CONTACTS_FILE", store / "no-aliases.json")
    with pytest.raises(directory.Ambiguous) as exc:
        directory.resolve_recipient("sam")
    assert jids(exc.value.matches) == ["15550000003@s.whatsapp.net", "15550000004@s.whatsapp.net"]
    message = str(exc.value)
    assert "'sam' matches 2 WhatsApp contacts" in message
    assert "Sam Carter - +15550000003 - 15550000003@s.whatsapp.net" in message
    assert "Sam Okafor - +15550000004 - 15550000004@s.whatsapp.net" in message
    assert "Samantha" not in message


def test_ambiguous_partial_number(store):
    with pytest.raises(directory.Ambiguous) as exc:
        directory.resolve_recipient("5550000")  # too short to dial; shared by nine numbers
    assert set(jids(exc.value.matches)) == {r[0] for r in ADDRESS_BOOK + CHATS if "5550000" in r[0]}
    assert len(exc.value.matches) == 9


def test_short_number_matching_nothing_is_not_dialled(store):
    with pytest.raises(ContactError, match="no WhatsApp contact or chat matches '8675309'"):
        directory.resolve_recipient("8675309")


def test_discord_alias_is_not_a_whatsapp_recipient(store):
    with pytest.raises(ContactError, match="no WhatsApp contact or chat matches 'zoe-discord'"):
        directory.resolve_recipient("zoe-discord")


def test_unknown_name(store):
    with pytest.raises(ContactError, match="no WhatsApp contact or chat matches 'bob'"):
        directory.resolve_recipient("bob")


def test_missing_store_degrades_to_aliases_and_numbers(store, monkeypatch):
    monkeypatch.setattr(paths, "WHATSMEOW_DB", store / "gone.db")
    monkeypatch.setattr(paths, "MESSAGES_DB", store / "gone2.db")
    # With no bridge store, the directory is exactly the WhatsApp aliases.
    assert sorted((e.jid, e.name) for e in directory.load_directory()) == [
        ("15550000001@s.whatsapp.net", "mom"),
        ("15550000004@s.whatsapp.net", "sam"),
        ("15550000009@s.whatsapp.net", "plumber"),
    ]
    assert directory.list_chats() == []
    assert directory.resolve_recipient("mom") == ("15550000001@s.whatsapp.net", "mom")
    assert directory.resolve_recipient("15550000002") == ("15550000002@s.whatsapp.net", "+15550000002")


def test_store_is_opened_read_only(store):
    before = (paths.MESSAGES_DB.read_bytes(), paths.WHATSMEOW_DB.read_bytes())
    directory.load_directory()
    directory.list_chats()
    with pytest.raises(Exception, match="readonly"):
        directory._rows(paths.MESSAGES_DB, "DELETE FROM chats")
    assert (paths.MESSAGES_DB.read_bytes(), paths.WHATSMEOW_DB.read_bytes()) == before
