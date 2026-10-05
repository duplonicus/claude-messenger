import json
import sqlite3

import pytest

from messenger import contacts, paths

# (jid, full_name, first_name, push_name) - the phone's address book
ADDRESS_BOOK = [
    ("15550000001@s.whatsapp.net", "Carol Baker", "", "Carol"),
    ("15550000002@s.whatsapp.net", "Zoe", "", "Zoe"),
    ("15550000003@s.whatsapp.net", "Sam Carter", "Sam", "sammy"),
    ("15550000004@s.whatsapp.net", "Sam Okafor", "Sam", ""),
    ("15550000005@s.whatsapp.net", "Samantha", "", ""),
    ("15550000006@s.whatsapp.net", "", "", ""),  # synced with no name at all
    ("15559990007@s.whatsapp.net", "Élodie", "", ""),
    ("15550000010@s.whatsapp.net", "", "", "Dee"),  # push name only
]
# (jid, name, last_message_time) - chats the bridge has seen. As in the real
# store, a 1:1 chat's "name" is usually just its number.
CHATS = [
    ("15550000002@s.whatsapp.net", "15550000002", "2026-10-03 21:00:00"),  # saved as Zoe
    ("120363000000000001@g.us", "Band Chat", "2026-10-03 22:00:00"),
    ("15550000008@s.whatsapp.net", "15550000008", "2026-10-01 09:00:00"),  # nobody knows them
    ("15550000001@s.whatsapp.net", "15550000001", "2026-09-30 12:00:00"),  # saved as Carol Baker
    ("15550000006@s.whatsapp.net", "15550000006", "2026-09-29 08:00:00"),  # in the address book, nameless
    ("15550000009@s.whatsapp.net", "15550000009", "2026-09-28 08:00:00"),  # only an alias names them
    ("15550000010@s.whatsapp.net", "15550000010", "2026-09-27 08:00:00"),  # only a push name
]


# Linked IDs: WhatsApp's second identifier for a person. (lid jid, name, maps to number or None)
LID_CONTACTS = [
    ("99900000000001@lid", "Zoe", "15550000002"),  # the same Zoe as the address book
    ("99900000000002@lid", "Lidia", None),  # no number known for her
]
# Archived chats: (jid as the settings table stores it, chat jid, name)
ARCHIVED = [
    ("15550000011@s.whatsapp.net", "15550000011@s.whatsapp.net", "Archie Number"),
    ("99900000000003@lid", "15550000012@s.whatsapp.net", "Archie Linked"),  # archived under a linked ID
    ("120363000000000002@g.us", "120363000000000002@g.us", "Archived Group"),
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fake bridge store plus alias file, with every path pointed at it."""
    whatsmeow = tmp_path / "whatsapp.db"
    conn = sqlite3.connect(whatsmeow)
    conn.execute(
        "CREATE TABLE whatsmeow_contacts (our_jid TEXT, their_jid TEXT, first_name TEXT,"
        " full_name TEXT, push_name TEXT, business_name TEXT, redacted_phone TEXT)"
    )
    conn.executemany(
        "INSERT INTO whatsmeow_contacts VALUES ('me@s.whatsapp.net', ?, ?, ?, ?, '', '')",
        [(jid, first, full, push) for jid, full, first, push in ADDRESS_BOOK],
    )
    conn.executemany(
        "INSERT INTO whatsmeow_contacts VALUES ('me@s.whatsapp.net', ?, '', ?, ?, '', '')",
        [(lid, name, name) for lid, name, _ in LID_CONTACTS]
        + [(chat, name, "") for _, chat, name in ARCHIVED if not chat.endswith("@g.us")],
    )
    conn.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, pn TEXT UNIQUE NOT NULL)")
    conn.executemany(
        "INSERT INTO whatsmeow_lid_map VALUES (?, ?)",
        [(lid.split("@")[0], pn) for lid, _, pn in LID_CONTACTS if pn] + [("99900000000003", "15550000012")],
    )
    conn.execute(
        "CREATE TABLE whatsmeow_chat_settings (our_jid TEXT, chat_jid TEXT,"
        " muted_until BIGINT NOT NULL DEFAULT 0, pinned BOOLEAN NOT NULL DEFAULT false,"
        " archived BOOLEAN NOT NULL DEFAULT false)"
    )
    conn.executemany(
        "INSERT INTO whatsmeow_chat_settings VALUES ('me@s.whatsapp.net', ?, 0, false, true)",
        [(settings_jid,) for settings_jid, _, _ in ARCHIVED],
    )
    # A chat with settings but not archived must stay visible.
    conn.execute("INSERT INTO whatsmeow_chat_settings VALUES ('me@s.whatsapp.net', '15550000002@s.whatsapp.net', 0, true, false)")
    conn.commit()
    conn.close()

    messages = tmp_path / "messages.db"
    conn = sqlite3.connect(messages)
    conn.execute("CREATE TABLE chats (jid TEXT PRIMARY KEY, name TEXT, last_message_time TIMESTAMP)")
    conn.executemany("INSERT INTO chats VALUES (?, ?, ?)", CHATS)
    # Archived chats are the most recent ones, so a leak would show at the top.
    conn.executemany(
        "INSERT INTO chats VALUES (?, ?, '2026-10-04 09:00:00')",
        [(chat, name if chat.endswith("@g.us") else chat.split("@")[0]) for _, chat, name in ARCHIVED],
    )
    conn.commit()
    conn.close()

    contacts_file = tmp_path / "contacts.json"
    contacts_file.write_text(
        json.dumps(
            {
                "mom": {"platform": "whatsapp", "id": "15550000001"},
                "sam": {"platform": "whatsapp", "id": "15550000004"},
                "plumber": {"platform": "whatsapp", "id": "15550000009"},
                "zoe-discord": {"platform": "discord", "id": "123456789012345678"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(paths, "WHATSMEOW_DB", whatsmeow)
    monkeypatch.setattr(paths, "MESSAGES_DB", messages)
    monkeypatch.setattr(paths, "CONTACTS_FILE", contacts_file)
    monkeypatch.setattr(contacts.load_contacts, "__defaults__", (contacts_file,))
    monkeypatch.setattr(contacts.resolve, "__defaults__", (contacts_file,))
    return tmp_path
