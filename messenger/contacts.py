"""The alias file: nickname -> platform + ID.

Aliases are shortcuts and are checked first. Discord sends are alias-only;
WhatsApp sends also accept names, numbers and JIDs (see directory.py).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import CONTACTS_FILE

PLATFORMS = ("whatsapp", "discord")

# Discord snowflakes are 17-20 digits today.
_DISCORD_ID = re.compile(r"\d{17,20}")
# A person (@s.whatsapp.net / @lid) or a group (@g.us).
_WHATSAPP_JID = re.compile(r"[0-9-]+@(s\.whatsapp\.net|g\.us|lid)")
_PHONE = re.compile(r"\+?\d{7,15}")


class ContactError(Exception):
    """A problem the user can fix: unknown alias, wrong platform, bad file."""


@dataclass(frozen=True)
class Contact:
    alias: str
    platform: str
    id: str


def _key(alias: str) -> str:
    return alias.strip().lower()


def whatsapp_jid(raw: str) -> str | None:
    """A JID as-is, a bare phone number as that person's JID, anything else None."""
    raw = raw.strip()
    if _WHATSAPP_JID.fullmatch(raw):
        return raw
    digits = re.sub(r"[\s().-]", "", raw)
    if _PHONE.fullmatch(digits):
        return f"{digits.lstrip('+')}@s.whatsapp.net"
    return None


def _normalize_id(alias: str, platform: str, raw: str) -> str:
    raw = str(raw).strip()
    if platform == "discord":
        if not _DISCORD_ID.fullmatch(raw):
            raise ContactError(
                f"contact '{alias}': '{raw}' is not a Discord user ID (17-20 digits)"
            )
        return raw
    jid = whatsapp_jid(raw)
    if jid:
        return jid
    raise ContactError(
        f"contact '{alias}': '{raw}' is not a WhatsApp JID or phone number "
        "(country code + number, digits only)"
    )


def load_contacts(path: Path = CONTACTS_FILE) -> dict[str, Contact]:
    if not path.exists():
        raise ContactError(
            f"no contacts file at {path} - copy config/contacts.example.json"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContactError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContactError(f"{path} must be a JSON object of alias -> contact")

    contacts: dict[str, Contact] = {}
    for alias, entry in raw.items():
        if alias.startswith("_"):  # "_comment" and friends
            continue
        if not isinstance(entry, dict) or "platform" not in entry or "id" not in entry:
            raise ContactError(f"contact '{alias}' needs \"platform\" and \"id\"")
        platform = str(entry["platform"]).strip().lower()
        if platform not in PLATFORMS:
            raise ContactError(
                f"contact '{alias}': platform must be one of {', '.join(PLATFORMS)}"
            )
        key = _key(alias)
        if key in contacts:
            raise ContactError(f"alias '{key}' appears twice (aliases ignore case)")
        contacts[key] = Contact(key, platform, _normalize_id(alias, platform, entry["id"]))
    return contacts


def resolve(alias: str, platform: str, path: Path = CONTACTS_FILE) -> Contact:
    """Look up an alias and insist it belongs to the platform being sent on."""
    contacts = load_contacts(path)
    contact = contacts.get(_key(alias))
    if contact is None:
        known = ", ".join(sorted(contacts)) or "(none)"
        raise ContactError(f"no contact named '{alias}'. Known aliases: {known}")
    if contact.platform != platform:
        raise ContactError(
            f"'{contact.alias}' is a {contact.platform} contact, not {platform}"
        )
    return contact
