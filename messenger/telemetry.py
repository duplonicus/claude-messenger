"""Logging, tracing and metrics for the server.

stdout is the MCP channel, so nothing here may ever write to it. Logs go to
stderr (the MCP client captures that; Claude Desktop writes it to
mcp-server-messenger.log) and to logs/server.log. Traces and metrics are
OpenTelemetry, each off unless its setting says otherwise.

Settings (real environment first, then the project's .env):
  MESSENGER_LOG_LEVEL          DEBUG | INFO (default) | WARNING
  MESSENGER_TRACES             off (default) | otlp | console
  OTEL_EXPORTER_OTLP_ENDPOINT  where otlp traces go (default http://127.0.0.1:4318)
  MESSENGER_METRICS            off (default) | otlp | console
  OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
                               full URL metrics are POSTed to (default: the
                               local Prometheus OTLP receiver, see below)

Nothing logged or traced here carries message text, tokens, or a full phone
number: see `mask`.
"""

import functools
import json
import logging
import os
import re
import signal
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler

from . import paths

SERVICE = "messenger"
DEFAULT_OTLP = "http://127.0.0.1:4318"
# Prometheus takes OTLP directly when started with --web.enable-otlp-receiver.
DEFAULT_METRICS = "http://127.0.0.1:9090/api/v1/otlp/v1/metrics"
METRICS_EVERY_MS = 15_000

log = logging.getLogger("messenger")
_tracer = None  # set by setup() when tracing is on
_server_kind = None  # SpanKind.SERVER, imported with the rest of tracing
_provider = None
_meters = None  # MeterProvider, when metrics are on
_calls = None  # counter: tool calls by tool and outcome
_duration = None  # histogram: tool call duration in ms


def _resource():
    from opentelemetry.sdk.resources import Resource

    # Clients may run several copies of this server at once (Claude Desktop
    # starts two). The instance id keeps their series apart.
    return Resource.create({"service.name": SERVICE, "service.instance.id": f"{os.uname().nodename}-{os.getpid()}"})


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name) or paths.load_env().get(name) or default


def mask(recipient: str) -> str:
    """Keep enough of a recipient to recognise it, not enough to dial it."""
    user, at, server = recipient.strip().partition("@")
    # A number however it was typed: digits plus the usual punctuation only.
    if re.fullmatch(r"[\d\s().+-]+", user) and len(re.sub(r"\D", "", user)) >= 7:
        return f"***{re.sub(r'[^0-9]', '', user)[-4:]}{at}{server}"
    return recipient.strip()  # an alias or a name the user typed


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, event, then context."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "ctx", {}))
        if record.exc_info:
            entry["stack"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def event(message: str, level: int = logging.INFO, exc_info=None, **ctx) -> None:
    log.log(level, message, extra={"ctx": ctx}, exc_info=exc_info)


def _setup_logging() -> None:
    level = getattr(logging, setting("MESSENGER_LOG_LEVEL", "INFO").upper(), logging.INFO)
    log.setLevel(level)
    log.propagate = False
    log.handlers.clear()

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(JsonFormatter())
    log.addHandler(stderr)

    try:
        paths.SERVER_LOG.parent.mkdir(mode=0o700, exist_ok=True)
        to_file = RotatingFileHandler(paths.SERVER_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        to_file.setFormatter(JsonFormatter())
        log.addHandler(to_file)
    except OSError as exc:  # a read-only checkout must not stop the server
        event("file logging unavailable", logging.WARNING, error=str(exc))

    # httpx logs every request URL at INFO; the trace has that, with timing.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _setup_tracing() -> str:
    """Turn tracing on if asked. Returns the mode actually in effect."""
    global _tracer, _provider, _server_kind
    mode = setting("MESSENGER_TRACES", "off").lower()
    if mode not in ("otlp", "console"):
        return "off"
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )
    except ImportError:
        event("tracing requested but opentelemetry is not installed", logging.WARNING,
              fix="uv pip install -e '.[otel]'")
        return "off"

    provider = TracerProvider(resource=_resource())
    if mode == "console":
        # ConsoleSpanExporter defaults to stdout, which would corrupt MCP.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stderr)))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = setting("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_OTLP).rstrip("/")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
        # With no collector listening, the exporter retries noisily. One line is enough.
        logging.getLogger("opentelemetry").setLevel(logging.ERROR)
    trace.set_tracer_provider(provider)
    _provider = provider
    _tracer = trace.get_tracer(SERVICE)
    # A tool call is this server handling a client's request. Jaeger's Monitor
    # tab (and most RED dashboards) only count SERVER spans; the default kind,
    # INTERNAL, leaves it empty.
    _server_kind = trace.SpanKind.SERVER

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()  # child spans for bridge and Discord calls
    except ImportError:
        pass
    return mode


def _setup_metrics() -> str:
    """Turn metrics on if asked. Returns the mode actually in effect."""
    global _meters, _calls, _duration
    mode = setting("MESSENGER_METRICS", "off").lower()
    if mode not in ("otlp", "console"):
        return "off"
    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    except ImportError:
        event("metrics requested but opentelemetry is not installed", logging.WARNING,
              fix="uv pip install -e '.[otel]'")
        return "off"

    if mode == "console":
        exporter = ConsoleMetricExporter(out=sys.stderr)  # default is stdout: see tracing
    else:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        exporter = OTLPMetricExporter(endpoint=setting("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", DEFAULT_METRICS))
        logging.getLogger("opentelemetry").setLevel(logging.ERROR)
    # Metrics are pushed on a timer, not per call: totals so far, every 15 s.
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=METRICS_EVERY_MS)
    _meters = MeterProvider(resource=_resource(), metric_readers=[reader])
    metrics.set_meter_provider(_meters)
    meter = metrics.get_meter(SERVICE)
    _calls = meter.create_counter(
        "messenger.tool.calls", unit="{call}", description="Tool calls, by tool and outcome"
    )
    _duration = meter.create_histogram(
        "messenger.tool.duration", unit="ms", description="Time a tool call took"
    )
    return mode


def _measure(tool: str, outcome: str, ms: float) -> None:
    # Low-cardinality labels only: never a recipient or a call id here, or
    # every message would become its own time series.
    if _calls:
        labels = {"tool": tool, "outcome": outcome}
        _calls.add(1, labels)
        _duration.record(ms, labels)


def _on_signal(signum, _frame) -> None:
    # A client that stops the server sends SIGTERM; say so, so a "server
    # disconnected" in the client's log has a matching line here.
    event("stopping on signal", signal=signal.Signals(signum).name, pid=os.getpid())
    if _provider:
        _provider.shutdown()  # flush spans still in the batch
    if _meters:
        _meters.shutdown()  # one last export of the totals
    logging.shutdown()
    # Not SystemExit: the stdio reader thread is blocked on stdin and would
    # keep the process alive until the client closes the pipe.
    os._exit(0)


def setup() -> None:
    """Call once, before the server starts reading stdin."""
    _setup_logging()
    traces = _setup_tracing()
    metrics = _setup_metrics()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    event(
        "server starting",
        pid=os.getpid(),
        python=sys.version.split()[0],
        cwd=os.getcwd(),
        root=str(paths.ROOT),
        traces=traces,
        metrics=metrics,
        contacts_file=paths.CONTACTS_FILE.exists(),
        bridge_token=paths.BRIDGE_TOKEN_FILE.exists(),
        bot_token=bool(setting("DISCORD_BOT_TOKEN")),
    )


def _outcome(result) -> str:
    text = str(result)
    if text.startswith("Sent to"):
        return "sent"
    if text.startswith(("Not sent:", "Error:")):
        return "refused"
    return "ok"


def observed(fn):
    """Log, trace and count one tool call: who it was for, how long, how it ended.

    Wraps the plain function, so FastMCP still sees the original signature.
    Only argument *shapes* are recorded: text length, masked recipient.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        call = uuid.uuid4().hex[:8]
        named = dict(zip(fn.__code__.co_varnames, args)) | kwargs
        ctx = {"tool": fn.__name__, "call": call}
        if "to" in named or "alias" in named:
            ctx["recipient"] = mask(str(named.get("to", named.get("alias", ""))))
        if "text" in named:
            ctx["chars"] = len(str(named["text"]))
        if "query" in named:
            ctx["query_chars"] = len(str(named["query"]))

        span_cm = _tracer.start_as_current_span(f"tool {fn.__name__}", kind=_server_kind) if _tracer else None
        span = span_cm.__enter__() if span_cm else None
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            ms = round((time.perf_counter() - started) * 1000, 1)
            event("tool crashed", logging.ERROR, exc_info=True, ms=ms, error=type(exc).__name__, **ctx)
            _measure(fn.__name__, "crashed", ms)
            if span:
                span.record_exception(exc)
                span.set_attribute("messenger.outcome", "crashed")
                span_cm.__exit__(type(exc), exc, exc.__traceback__)
            raise
        ms = round((time.perf_counter() - started) * 1000, 1)
        outcome = _outcome(result)
        event("tool call", ms=ms, outcome=outcome, **ctx)
        _measure(fn.__name__, outcome, ms)
        if span:
            for key, value in ctx.items():
                span.set_attribute(f"messenger.{key}", value)
            span.set_attribute("messenger.outcome", outcome)
            span_cm.__exit__(None, None, None)
        return result

    return wrapper
