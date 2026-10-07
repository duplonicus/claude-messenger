"""Logging and tracing: useful, private, and never on stdout."""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from messenger import telemetry
from messenger.paths import ROOT

SECRET_TEXT = "a very private sentence nobody should log"


@pytest.mark.parametrize(
    "recipient, masked",
    [
        ("15195550123", "***0123"),
        ("+1 (519) 555-0123", "***0123"),
        ("15195550123@s.whatsapp.net", "***0123@s.whatsapp.net"),
        ("120363000000000001@g.us", "***0001@g.us"),
        ("mom", "mom"),
        ("Carol Baker", "Carol Baker"),
        ("zoe-discord", "zoe-discord"),
    ],
)
def test_mask(recipient, masked):
    assert telemetry.mask(recipient) == masked


@pytest.fixture
def captured(monkeypatch):
    """Collect the JSON the logger would write."""
    lines = []

    class Collect(logging.Handler):
        def emit(self, record):
            lines.append(json.loads(telemetry.JsonFormatter().format(record)))

    monkeypatch.setattr(telemetry.log, "handlers", [Collect()])
    monkeypatch.setattr(telemetry.log, "propagate", False)
    telemetry.log.setLevel(logging.INFO)
    return lines


def test_tool_call_is_logged_with_shape_but_not_content(captured):
    @telemetry.observed
    def send_whatsapp(to: str, text: str) -> str:
        return "Sent to mom (15195550123@s.whatsapp.net) on WhatsApp."

    send_whatsapp("15195550123", SECRET_TEXT)

    (entry,) = captured
    assert set(entry) == {"ts", "level", "event", "tool", "call", "recipient", "chars", "ms", "outcome"}
    assert entry["event"] == "tool call"
    assert entry["tool"] == "send_whatsapp"
    assert entry["recipient"] == "***0123"
    assert entry["chars"] == len(SECRET_TEXT)
    assert entry["outcome"] == "sent"
    assert entry["ms"] >= 0
    assert len(entry["call"]) == 8
    assert SECRET_TEXT not in json.dumps(entry) and "15195550123" not in json.dumps(entry)


@pytest.mark.parametrize(
    "result, outcome",
    [
        ("Sent to zoe on Discord.", "sent"),
        ("Not sent: no WhatsApp contact or chat matches 'bob'", "refused"),
        ("Error: empty message", "refused"),
        ("mom: whatsapp", "ok"),
    ],
)
def test_outcome_is_read_from_the_reply(captured, result, outcome):
    @telemetry.observed
    def tool() -> str:
        return result

    tool()
    assert captured[0]["outcome"] == outcome


def test_crash_is_logged_with_a_stack_and_still_raised(captured):
    @telemetry.observed
    def send_whatsapp(to: str, text: str) -> str:
        raise RuntimeError("bridge exploded")

    with pytest.raises(RuntimeError, match="bridge exploded"):
        send_whatsapp("mom", SECRET_TEXT)

    (entry,) = captured
    assert entry["level"] == "error" and entry["event"] == "tool crashed"
    assert entry["error"] == "RuntimeError"
    assert "RuntimeError: bridge exploded" in entry["stack"]
    assert SECRET_TEXT not in json.dumps(entry)


def test_observed_keeps_the_signature_fastmcp_reads():
    import inspect

    @telemetry.observed
    def send_whatsapp(to: str, text: str) -> str:
        """doc"""
        return ""

    assert list(inspect.signature(send_whatsapp).parameters) == ["to", "text"]
    assert send_whatsapp.__name__ == "send_whatsapp" and send_whatsapp.__doc__ == "doc"


# --- the real process, over real stdio -------------------------------------

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
CALLS = [
    INIT,
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_contacts", "arguments": {}}},
]


def start_server(home, **env):
    (home / "config").mkdir(exist_ok=True)
    (home / "config" / "contacts.json").write_text('{"mom": {"platform": "whatsapp", "id": "15195550123"}}')
    return subprocess.Popen(
        [sys.executable, "-m", "messenger.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
        env={**os.environ, "MESSENGER_HOME": str(home), "MESSENGER_TRACES": "off", "MESSENGER_METRICS": "off", **env},
    )


def talk(proc, wait_for_id=2, timeout=20):
    """Send the calls, read stdout until the reply with `wait_for_id` arrives."""
    for message in CALLS:
        proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    lines, deadline = [], time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line)
        if json.loads(line).get("id") == wait_for_id:
            return lines
    raise AssertionError(f"no reply to id {wait_for_id}; stderr: {proc.stderr.read()[-800:]}")


def stop(proc):
    proc.send_signal(signal.SIGTERM)
    try:
        # Against a dead collector the final flush alone takes about 15 s, so 15 was a coin toss.
        out, err = proc.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    return out, err


@pytest.mark.parametrize("traces, metrics", [("off", "off"), ("console", "off"), ("off", "console"), ("console", "console")])
def test_stdout_carries_only_protocol_messages(tmp_path, traces, metrics):
    """The console exporters are the dangerous ones: both default to stdout."""
    proc = start_server(tmp_path, MESSENGER_TRACES=traces, MESSENGER_METRICS=metrics)
    lines = talk(proc)
    rest, err = stop(proc)

    for line in lines + [l for l in rest.splitlines() if l.strip()]:
        assert json.loads(line)["jsonrpc"] == "2.0"
    assert json.loads(lines[-1])["result"]["content"][0]["text"] == "mom: whatsapp"

    logged = [json.loads(l) for l in err.splitlines() if l.startswith('{"ts"')]
    events = [e["event"] for e in logged]
    assert events[0] == "server starting" and logged[0]["traces"] == traces and logged[0]["metrics"] == metrics
    assert "tool call" in events
    assert events[-1] == "stopping on signal" and logged[-1]["signal"] == "SIGTERM"
    if traces == "console":
        assert '"name": "tool list_contacts"' in err  # the span, on stderr
        assert '"kind": "SpanKind.SERVER"' in err  # what Jaeger's Monitor tab counts
    if metrics == "console":
        assert '"name": "messenger.tool.calls"' in err  # the final export, on stderr


def test_log_file_gets_the_same_lines(tmp_path):
    proc = start_server(tmp_path)
    talk(proc)
    stop(proc)
    entries = [json.loads(l) for l in (tmp_path / "logs" / "server.log").read_text().splitlines()]
    assert [e["event"] for e in entries] == ["server starting", "tool call", "stopping on signal"]
    assert entries[0]["metrics"] == "off" and entries[0]["traces"] == "off"
    assert entries[1]["tool"] == "list_contacts" and entries[1]["outcome"] == "ok"


def test_otlp_traces_reach_a_collector(tmp_path):
    """End to end: a tool call becomes a span POSTed to /v1/traces."""
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, self.headers.get("Content-Type"), body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    try:
        proc = start_server(
            tmp_path,
            MESSENGER_TRACES="otlp",
            OTEL_EXPORTER_OTLP_ENDPOINT=f"http://127.0.0.1:{collector.server_port}",
        )
        talk(proc)
        stop(proc)  # SIGTERM -> clean exit -> the batch processor flushes
    finally:
        collector.shutdown()

    assert [path for path, _, _ in received] == ["/v1/traces"]
    _, content_type, body = received[0]
    assert content_type == "application/x-protobuf"
    assert b"tool list_contacts" in body  # span name
    assert b"messenger" in body  # service.name
    assert b"messenger.outcome" in body


def test_otlp_metrics_reach_a_collector_with_safe_labels(tmp_path):
    """Counts and timings are pushed on shutdown; labels are tool and outcome only."""
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    try:
        proc = start_server(
            tmp_path,
            MESSENGER_METRICS="otlp",
            OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=f"http://127.0.0.1:{collector.server_port}/api/v1/otlp/v1/metrics",
        )
        talk(proc)
        stop(proc)  # well inside the 15 s interval, so this is the shutdown flush
    finally:
        collector.shutdown()

    assert [path for path, _ in received] == ["/api/v1/otlp/v1/metrics"]
    body = received[0][1]
    for expected in (b"messenger.tool.calls", b"messenger.tool.duration", b"list_contacts", b"outcome", b"service.instance.id"):
        assert expected in body
    # Per-call values would make every call its own time series.
    assert b"recipient" not in body and b"messenger.call" not in body


def test_measure_uses_only_tool_and_outcome(monkeypatch):
    seen = []

    class Fake:
        def add(self, n, labels):
            seen.append(("calls", n, labels))

        def record(self, ms, labels):
            seen.append(("duration", ms, labels))

    monkeypatch.setattr(telemetry, "_calls", Fake())
    monkeypatch.setattr(telemetry, "_duration", Fake())

    @telemetry.observed
    def send_whatsapp(to: str, text: str) -> str:
        return "Not sent: no WhatsApp contact or chat matches 'bob'"

    send_whatsapp("15195550123", SECRET_TEXT)
    labels = {"tool": "send_whatsapp", "outcome": "refused"}
    assert [(kind, lab) for kind, _, lab in seen] == [("calls", labels), ("duration", labels)]
    assert seen[0][1] == 1 and seen[1][1] >= 0


def test_dead_collector_does_not_break_the_server(tmp_path):
    proc = start_server(tmp_path, MESSENGER_TRACES="otlp", OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:9",
                        MESSENGER_METRICS="otlp", OTEL_EXPORTER_OTLP_METRICS_ENDPOINT="http://127.0.0.1:9/x")
    lines = talk(proc)
    stop(proc)
    assert json.loads(lines[-1])["result"]["content"][0]["text"] == "mom: whatsapp"
