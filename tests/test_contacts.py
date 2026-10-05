import json

import pytest

from messenger.contacts import Contact, ContactError, load_contacts, resolve


def write(tmp_path, data):
    path = tmp_path / "contacts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_both_platforms_exactly(tmp_path):
    path = write(
        tmp_path,
        {
            "_comment": "ignored",
            "Mom": {"platform": "whatsapp", "id": "15557654321@s.whatsapp.net"},
            "zoe": {"platform": "Discord", "id": "123456789012345678"},
        },
    )
    assert load_contacts(path) == {
        "mom": Contact("mom", "whatsapp", "15557654321@s.whatsapp.net"),
        "zoe": Contact("zoe", "discord", "123456789012345678"),
    }


@pytest.mark.parametrize("raw", ["15551234567", "+1 (555) 123-4567", "1.555.123.4567"])
def test_bare_phone_becomes_a_jid(tmp_path, raw):
    path = write(tmp_path, {"me": {"platform": "whatsapp", "id": raw}})
    assert load_contacts(path)["me"].id == "15551234567@s.whatsapp.net"


def test_group_jid_is_kept(tmp_path):
    path = write(tmp_path, {"band": {"platform": "whatsapp", "id": "120363041234567890@g.us"}})
    assert load_contacts(path)["band"].id == "120363041234567890@g.us"


def test_alias_lookup_ignores_case_and_whitespace(tmp_path):
    path = write(tmp_path, {"zoe": {"platform": "discord", "id": "123456789012345678"}})
    assert resolve("  Zoe ", "discord", path).id == "123456789012345678"


def test_unknown_alias_lists_the_known_ones(tmp_path):
    path = write(tmp_path, {"zoe": {"platform": "discord", "id": "123456789012345678"}})
    with pytest.raises(ContactError, match="no contact named 'bob'. Known aliases: zoe"):
        resolve("bob", "discord", path)


def test_wrong_platform_is_refused(tmp_path):
    path = write(tmp_path, {"zoe": {"platform": "discord", "id": "123456789012345678"}})
    with pytest.raises(ContactError, match="'zoe' is a discord contact, not whatsapp"):
        resolve("zoe", "whatsapp", path)


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"platform": "discord", "id": "12345"}, "not a Discord user ID"),
        ({"platform": "discord", "id": "zoe#1234"}, "not a Discord user ID"),
        ({"platform": "whatsapp", "id": "mom"}, "not a WhatsApp JID or phone number"),
        ({"platform": "whatsapp", "id": "1555@evil.example"}, "not a WhatsApp JID"),
        ({"platform": "sms", "id": "15551234567"}, "platform must be one of"),
        ({"platform": "discord"}, 'needs "platform" and "id"'),
    ],
)
def test_bad_entries_are_rejected(tmp_path, entry, message):
    with pytest.raises(ContactError, match=message):
        load_contacts(write(tmp_path, {"x": entry}))


def test_duplicate_alias_differing_only_by_case(tmp_path):
    path = tmp_path / "contacts.json"
    path.write_text(
        '{"Mom": {"platform": "whatsapp", "id": "15551234567"},'
        ' "mom": {"platform": "whatsapp", "id": "15557654321"}}',
        encoding="utf-8",
    )
    with pytest.raises(ContactError, match="appears twice"):
        load_contacts(path)


def test_missing_file_says_what_to_do(tmp_path):
    with pytest.raises(ContactError, match="contacts.example.json"):
        load_contacts(tmp_path / "nope.json")


def test_example_file_is_valid():
    from messenger.paths import ROOT

    contacts = load_contacts(ROOT / "config" / "contacts.example.json")
    assert sorted(contacts) == ["me", "mom", "zoe"]
