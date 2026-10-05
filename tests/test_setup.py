"""scripts/setup.sh and the confirmation setting it writes."""

import shutil
import subprocess
from pathlib import Path

import pytest

from messenger import server

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path):
    """A throwaway copy of the files setup.sh touches."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    shutil.copy(REPO / "scripts" / "setup.sh", tmp_path / "scripts" / "setup.sh")
    shutil.copy(REPO / "config" / "contacts.example.json", tmp_path / "config" / "contacts.example.json")
    return tmp_path


def run_setup(checkout, *args, stdin=""):
    return subprocess.run(
        [str(checkout / "scripts" / "setup.sh"), "--no-install", "--no-whatsapp", *args],
        input=stdin, capture_output=True, text=True, cwd="/",
    )


def env_of(checkout):
    return dict(line.split("=", 1) for line in (checkout / ".env").read_text().splitlines())


@pytest.mark.parametrize("typed, expected", [("", "yes"), ("y\n", "yes"), ("n\n", "no"), ("No\n", "no"), ("maybe\n", "yes")])
def test_setup_asks_and_defaults_to_confirming(checkout, typed, expected):
    result = run_setup(checkout, stdin=typed)
    assert result.returncode == 0, result.stderr
    assert env_of(checkout) == {"MESSENGER_CONFIRM_SENDS": expected}


def test_setup_flag_skips_the_question_and_env_is_private(checkout):
    assert run_setup(checkout, "--confirm", "no").returncode == 0
    assert env_of(checkout) == {"MESSENGER_CONFIRM_SENDS": "no"}
    assert (checkout / ".env").stat().st_mode & 0o777 == 0o600


def test_setup_rerun_changes_only_its_own_line(checkout):
    (checkout / ".env").write_text("DISCORD_BOT_TOKEN=keepme\nMESSENGER_CONFIRM_SENDS=no\n")
    (checkout / "config" / "contacts.json").write_text('{"mine": {"platform": "discord", "id": "1"}}')
    assert run_setup(checkout, "--confirm", "yes").returncode == 0
    assert env_of(checkout) == {"DISCORD_BOT_TOKEN": "keepme", "MESSENGER_CONFIRM_SENDS": "yes"}
    assert "mine" in (checkout / "config" / "contacts.json").read_text(), "an existing contacts file is never overwritten"


def test_setup_rejects_a_bad_confirm_value(checkout):
    assert run_setup(checkout, "--confirm", "sometimes").returncode == 2
    assert not (checkout / ".env").exists()


@pytest.mark.parametrize("value, confirm", [
    (None, True), ("", True), ("yes", True), ("YES", True), ("on", True), ("typo", True),
    ("no", False), ("No", False), ("false", False), ("0", False), ("off", False),
])
def test_confirmation_is_on_unless_clearly_turned_off(monkeypatch, tmp_path, value, confirm):
    monkeypatch.setattr("messenger.paths.ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr("messenger.paths.load_env", lambda path=None: {})
    if value is None:
        monkeypatch.delenv("MESSENGER_CONFIRM_SENDS", raising=False)
    else:
        monkeypatch.setenv("MESSENGER_CONFIRM_SENDS", value)
    assert server.confirm_sends() is confirm
    text = server.instructions()
    # Both modes keep the rules that never change.
    assert "Never send on your own initiative" in text and "never pick for them" in text
    assert ("wait for a clear yes" in text) is confirm
    assert ("call the send tool straight away" in text) is (not confirm)
    assert f"Confirm before sending: {'on' if confirm else 'off'}" in server.messenger_status()
