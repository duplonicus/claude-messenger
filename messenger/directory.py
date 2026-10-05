"""Who can be reached on WhatsApp, read straight from the bridge's SQLite store.

Written by the bridge and only read here:
  whatsapp.db  whatsmeow_contacts       the phone's synced address book
  whatsapp.db  whatsmeow_lid_map        linked ID -> phone number for the same person
  whatsapp.db  whatsmeow_chat_settings  which chats are archived
  messages.db  chats                    every chat the bridge has seen, groups included

Archived chats are left out of everything here: they can't be found by name
or listed. Naming one outright (alias, number or JID) still reaches it.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .contacts import ContactError, load_contacts, whatsapp_jid


@dataclass(frozen=True)
class Entry:
    jid: str
    name: str  # best display name, "" if none is known
    names: tuple[str, ...]  # every name this JID goes by, for matching
    last_message_time: str | None = None

    @property
    def is_group(self) -> bool:
        return self.jid.endswith("@g.us")

    def label(self) -> str:
        if self.is_group:
            kind = "group"
        elif self.jid.endswith("@lid"):
            kind = "no number known"  # a linked ID is not a phone number
        else:
            kind = "+" + self.jid.split("@")[0]
        # No name known: the number stands in for it rather than a placeholder.
        return " - ".join(part for part in (self.name, kind, self.jid) if part)


# Shortest thing accepted as a complete number (country code included) when it
# isn't a saved contact. Anything shorter is searched for instead of dialled.
MIN_FULL_NUMBER = 10


class Ambiguous(ContactError):
    """More than one person fits; the caller shows the matches and asks."""

    def __init__(self, query: str, matches: list[Entry]):
        self.matches = matches
        lines = "\n".join(f"  {m.label()}" for m in matches)
        super().__init__(
            f"'{query}' matches {len(matches)} WhatsApp contacts. "
            f"Ask which one, then send to its JID:\n{lines}"
        )


def _rows(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _lid_map() -> dict[str, str]:
    """Linked-ID user -> phone-number user, for people WhatsApp knows by both."""
    return {
        str(lid).split("@")[0]: str(pn).split("@")[0]
        for lid, pn in _rows(paths.WHATSMEOW_DB, "SELECT lid, pn FROM whatsmeow_lid_map")
    }


def _canonical(jid: str, lids: dict[str, str]) -> str:
    """One JID per person: a linked ID becomes its phone-number JID when known."""
    user, _, server = jid.partition("@")
    if server == "lid" and user in lids:
        return f"{lids[user]}@s.whatsapp.net"
    return jid


def _archived(lids: dict[str, str]) -> set[str]:
    rows = _rows(paths.WHATSMEOW_DB, "SELECT chat_jid FROM whatsmeow_chat_settings WHERE archived")
    return {_canonical(jid, lids) for (jid,) in rows}


def list_chats(limit: int = 20) -> list[Entry]:
    """Most recently active chats first, named the way search names them."""
    rows = _rows(
        paths.MESSAGES_DB,
        "SELECT jid, last_message_time FROM chats ORDER BY last_message_time DESC",
    )
    # The chats table often holds just the number as the "name"; the directory
    # knows the address-book name, push name and alias for the same JID.
    # load_directory has already dropped archived chats and merged linked IDs.
    known = {e.jid: e for e in load_directory()}
    lids = _lid_map()
    chats, seen = [], set()
    for jid, when in rows:
        jid = _canonical(jid, lids)
        if jid in known and jid not in seen:
            seen.add(jid)
            chats.append(Entry(jid, known[jid].name, known[jid].names, when))
    return chats[: max(1, min(limit, 200))]


def load_directory() -> list[Entry]:
    """Address book, chats and aliases merged, one entry per person.

    A person WhatsApp knows by both a linked ID and a number is one entry,
    under the number. Archived chats are left out.

    Name order: saved address-book name, first name, push name, business
    name, the chat's own name, then an alias.
    """
    lids = _lid_map()
    archived = _archived(lids)
    names: dict[str, list[str]] = {}
    # Address-book names first so the saved name wins as the display name.
    for jid, full, first, push, business in _rows(
        paths.WHATSMEOW_DB,
        "SELECT their_jid, full_name, first_name, push_name, business_name FROM whatsmeow_contacts",
    ):
        jid = _canonical(jid, lids)
        if jid in archived:
            continue
        known = names.setdefault(jid, [])
        for name in (full, first, push, business):
            if name and name not in known:
                known.append(name)
    for jid, name in _rows(paths.MESSAGES_DB, "SELECT jid, name FROM chats"):
        jid = _canonical(jid, lids)
        if jid in archived:
            continue
        # A chat with no saved name is "named" after its own number; not a name.
        known = names.setdefault(jid, [])
        if name and name != jid.split("@")[0] and name not in known:
            known.append(name)
    # Aliases last: a name of last resort for a number nothing else names.
    try:
        aliases = load_contacts(paths.CONTACTS_FILE).values()
    except ContactError:
        aliases = ()
    for contact in aliases:
        if contact.platform == "whatsapp" and contact.id not in archived:
            known = names.setdefault(contact.id, [])
            if contact.alias not in known:
                known.append(contact.alias)
    return [Entry(jid, ns[0] if ns else "", tuple(ns)) for jid, ns in names.items()]


def search(query: str, entries: list[Entry] | None = None) -> list[Entry]:
    """Entries whose name or number contains the query. Exact names sort first."""
    entries = load_directory() if entries is None else entries
    q = query.strip().casefold()
    if not q:
        return []
    digits = "".join(ch for ch in q if ch.isdigit())
    hits = [
        e
        for e in entries
        if any(q in n.casefold() for n in e.names)
        or (len(digits) >= 4 and digits in e.jid.split("@")[0])
    ]
    return sorted(hits, key=lambda e: (not _exact(e, q), e.name.casefold(), e.jid))


def _exact(entry: Entry, q: str) -> bool:
    return any(n.casefold() == q for n in entry.names)


def resolve_recipient(to: str, entries: list[Entry] | None = None) -> tuple[str, str]:
    """Turn what the user said into (jid, display name).

    Order: alias, then JID or phone number, then a name. A name that fits more
    than one contact raises Ambiguous rather than picking.
    """
    wanted = to.strip()
    if not wanted:
        raise ContactError("no recipient given")

    try:
        alias = load_contacts(paths.CONTACTS_FILE).get(wanted.lower())
    except ContactError:
        alias = None  # a missing or broken alias file shouldn't block a direct send
    if alias and alias.platform == "whatsapp":
        return alias.id, alias.alias

    entries = load_directory() if entries is None else entries

    jid = whatsapp_jid(wanted)
    if jid:
        known = next((e for e in entries if e.jid == jid), None)
        if known or "@" in wanted:
            return jid, (known.name if known and known.name else "+" + jid.split("@")[0])
        # A number typed without its country code still finds the saved
        # contact it belongs to, instead of going to a different number.
        number = jid.split("@")[0]
        tails = [e for e in entries if not e.is_group and e.jid.split("@")[0].endswith(number)]
        if len(tails) > 1:
            raise Ambiguous(to, tails)
        if tails:
            return tails[0].jid, tails[0].name or "+" + tails[0].jid.split("@")[0]
        if len(number) >= MIN_FULL_NUMBER:
            return jid, "+" + number
        # Too short to be a full number: fall through and treat it as a search.

    matches = search(wanted, entries)
    if not matches:
        raise ContactError(
            f"no WhatsApp contact or chat matches '{to}'. "
            "Try search_contacts, or give a phone number with country code."
        )
    # Someone whose whole name is the query beats people who merely contain it.
    exact = [m for m in matches if _exact(m, wanted.casefold())]
    pool = exact or matches
    if len(pool) > 1:
        raise Ambiguous(to, pool)
    return pool[0].jid, pool[0].name
