import json

import httpx
import pytest

from messenger import whatsapp

JID = "15551234567@s.whatsapp.net"


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / ".bridge-token"
    path.write_text("abc123\n", encoding="utf-8")
    return path


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bridge_url_is_loopback_literal():
    assert whatsapp.BRIDGE_URL == "http://127.0.0.1:8080/api"


def test_send_posts_to_loopback_with_bearer_and_no_signature(token_file):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "message": "sent"})

    whatsapp.send(JID, "be there in a minute", client_for(handler), token_file)

    assert seen == {
        "url": "http://127.0.0.1:8080/api/send",
        "auth": "Bearer abc123",
        "body": {"recipient": JID, "message": "be there in a minute"},
    }


def test_bridge_failure_passes_its_reason_through(token_file):
    handler = lambda request: httpx.Response(500, json={"success": False, "message": "Not connected to WhatsApp"})
    with pytest.raises(whatsapp.WhatsAppError, match=r"\(500\): Not connected to WhatsApp"):
        whatsapp.send(JID, "hi", client_for(handler), token_file)


def test_200_without_success_is_still_a_failure(token_file):
    handler = lambda request: httpx.Response(200, json={"success": False, "message": "nope"})
    with pytest.raises(whatsapp.WhatsAppError, match="nope"):
        whatsapp.send(JID, "hi", client_for(handler), token_file)


def test_bridge_down_says_where_to_look(token_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(whatsapp.WhatsAppError, match="waiting for a QR scan. Check: systemctl --user status whatsapp-bridge"):
        whatsapp.send(JID, "hi", client_for(handler), token_file)


def test_no_token_file_never_calls_the_bridge(tmp_path):
    def handler(request):
        raise AssertionError("no request should be made without a token")

    with pytest.raises(whatsapp.WhatsAppError, match="no bridge token yet"):
        whatsapp.send(JID, "hi", client_for(handler), tmp_path / "missing")


@pytest.mark.parametrize(
    "status, expected",
    [
        (200, "connected"),
        (503, "bridge is up but not connected to WhatsApp (needs a QR scan, or still starting)"),
        (401, "bridge answered 401"),
    ],
)
def test_health(token_file, status, expected):
    handler = lambda request: httpx.Response(status, json={})
    assert whatsapp.health(client_for(handler), token_file) == expected
