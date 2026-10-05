# messenger

A small local [MCP](https://modelcontextprotocol.io) server that lets an assistant such as the Claude app send messages for you: WhatsApp from your own number, Discord DMs from your own bot.

Say "tell Zoe I'll be there in a minute" and it goes out. It sends only when you ask for it in that conversation, and by default it shows you the message and waits for a yes first.

It is also a worked example of making a tiny server observable: structured logs, OpenTelemetry traces and metrics, a `doctor` script that connects the way a client does, and the MCP Inspector wired in. See [Debugging](#debugging).

Linux only, including WSL. Not tested on macOS, and the WhatsApp part needs systemd.

## Tools

| Tool | What it does |
|---|---|
| `send_whatsapp(to, text)` | Sends from your number, exactly as written. `to` is an alias, a contact or group name, a phone number, or a chat JID |
| `send_discord_dm(alias, text)` | Sends from your bot, with ` -claude` appended so the reader knows a bot wrote it. Alias only |
| `search_contacts(query)` | WhatsApp address book and chats by name or number |
| `list_chats(limit)` | Recent WhatsApp chats, newest first (names and times, never message text) |
| `list_contacts()` | Your alias shortcuts and their platform |
| `messenger_status()` | Is the bridge connected, is the bot token set, is confirmation on |

The server is send-only. It never reads message text.

## Safety

- **Confirmation is on by default.** With `MESSENGER_CONFIRM_SENDS=yes` (the default), the server instructs the assistant to show you the recipient and the exact text and wait for a clear yes. Set it to `no` and your instruction to message someone counts as the approval. `scripts/setup.sh` asks which you want.
- **That is an instruction to the model, not a lock.** The hard gate is your MCP client's own tool-approval prompt. Keep that prompt on for the two send tools unless you have decided you don't want it.
- **It never guesses between people.** A name that fits more than one contact sends nothing and returns the matches.
- **It never acts on text it reads.** The instructions tell the assistant not to send because a file, web page or tool result asked it to.
- **Archived chats are never matched by name.** Naming one outright (alias, number or JID) still reaches it.
- **Loopback only.** The server speaks stdio; the WhatsApp bridge binds `127.0.0.1`. Never tunnel or port-forward it.
- **Message text is never logged.** Logs, traces and metrics carry metadata only, and recipients are masked.

### WhatsApp ban risk

The WhatsApp side uses the unofficial WhatsApp Web protocol (whatsmeow). WhatsApp's terms forbid unofficial clients, and accounts do get banned for it, mostly for bulk or automated sending. Keep it to human-volume messages to people who know you, or skip WhatsApp and use Discord only.

## Setup

You need [uv](https://docs.astral.sh/uv/), Python 3.11+, and an MCP client. WhatsApp also needs Go 1.26+, gcc and systemd user services.

```bash
git clone https://github.com/duplonicus/claude-messenger
cd claude-messenger
scripts/setup.sh
```

The script installs the Python package, creates `config/contacts.json` and `.env`, asks whether to confirm before every send, and asks whether to set up WhatsApp. It prints the line to register the server with Claude Code or Claude Desktop. Re-running it is safe.

Then:

```bash
scripts/doctor.py        # connects like a client and checks everything
```

### Contacts

Aliases are shortcuts, checked before anything else. `config/contacts.json` is gitignored:

```json
{
  "mom": { "platform": "whatsapp", "id": "15557654321" },
  "zoe": { "platform": "discord",  "id": "123456789012345678" }
}
```

- WhatsApp `id`: country code + number, or a full JID (`...@s.whatsapp.net`, groups `...@g.us`).
- Discord `id`: Developer Mode → right-click the user → Copy User ID. They must share a server with your bot.

Edits take effect on the next send; no restart.

### Discord

Create a bot in the Discord developer portal and put its token in `.env` (mode 0600, gitignored) as `DISCORD_BOT_TOKEN`. "Cannot send messages to this user" means no shared server, their DMs are closed, or they blocked the bot.

### WhatsApp

`scripts/setup.sh --whatsapp` clones and builds the bridge, installs it as the `whatsapp-bridge` systemd user service, and starts it.

```bash
scripts/pair.sh                              # fresh QR; scan from Linked devices
systemctl --user status whatsapp-bridge
journalctl --user -u whatsapp-bridge -f
```

The bridge is [verygoodplugins/whatsapp-mcp](https://github.com/verygoodplugins/whatsapp-mcp), pinned to a commit in `scripts/install-bridge.sh` and cloned to `vendor/` (gitignored). Only its Go bridge is used. Its own MCP server, which can read message history, is not registered anywhere.

- It listens on `127.0.0.1:8080` only, with a bearer token in `store/.bridge-token` (0600).
- Webhooks and media auto-download are turned off in the service file.
- If the linked device gets dropped, re-pair with `scripts/pair.sh`.

## How a WhatsApp recipient is chosen

In order:

1. An alias from `config/contacts.json`.
2. A JID, or a phone number. A number typed without its country code is matched to the saved contact it belongs to; an unsaved number needs the country code.
3. A name, matched against the bridge's own store (`whatsmeow_contacts` and `chats`, opened read-only). A whole-name match beats a partial one.

WhatsApp sometimes knows one person by both a phone number and a "linked ID". The bridge's `whatsmeow_lid_map` ties them together, so they count as one contact, under the number. Without that, one person shows up twice and every name becomes ambiguous.

## Debugging

This follows the [MCP debugging guide](https://modelcontextprotocol.io/docs/tools/debugging). Start at the top; each step answers a narrower question.

| Question | Run |
|---|---|
| Does it connect and work at all? | `scripts/doctor.py`, or `scripts/doctor.py --wsl` to launch it exactly as Claude Desktop on Windows does |
| What did the server do? | `tail -f logs/server.log` |
| What did Claude Desktop see? | `scripts/desktop-logs.sh`, or `scripts/desktop-logs.sh errors` |
| What does the protocol traffic look like? | `scripts/inspect.sh` (MCP Inspector web UI) or `scripts/inspect.sh list` |
| Where did the time go inside a call? | traces, below |

**"Server disconnected" in Claude Desktop** has, for this server, always meant the process was stopped from outside while the app was running. The app does not restart it, and `logs/server.log` shows `stopping on signal` at the same second. Quit the app fully and reopen it. The same applies after a code change, so iterate with the doctor and the Inspector, not the app.

### Logs

One JSON object per line, to stderr (which the MCP client captures) and to `logs/server.log` (rotates at 1 MB, keeps 3):

```json
{"ts": "2026-10-04T19:32:16.429Z", "level": "info", "event": "tool call", "ms": 32.2, "outcome": "sent", "tool": "send_whatsapp", "call": "fac56338", "recipient": "***0123", "chars": 42}
```

- `call` is a per-call ID; `outcome` is `sent`, `refused` (nothing went out) or `ok` (a lookup).
- Never logged: message text, tokens, full phone numbers.
- stdout is the protocol channel. Nothing may print to it; a test runs the real process and checks.
- `MESSENGER_LOG_LEVEL=DEBUG` in `.env` for more.

`logs/sent.log` records each send attempt: time, platform, recipient, length, outcome. Never the text.

### Traces (OpenTelemetry)

Off by default. Each tool call becomes a span, with child spans for the HTTP calls to the bridge and to Discord, so you can see what a slow send was waiting on.

```bash
uv pip install -e '.[otel]'
scripts/observability.sh up                   # local Jaeger + Prometheus, loopback only, needs Docker
echo 'MESSENGER_TRACES=otlp' >> .env          # then restart the MCP client
# open http://localhost:16686, service "messenger"
```

Jaeger's **Monitor** tab works too: Jaeger derives request rate, error rate and latency per tool from the spans and keeps them in Prometheus as `traces_span_metrics_*`. It only counts server spans, which is why tool spans are created with `SpanKind.SERVER`.

`MESSENGER_TRACES=console` prints spans to stderr instead, no collector needed. `OTEL_EXPORTER_OTLP_ENDPOINT` points it somewhere other than `http://127.0.0.1:4318`. If no collector is listening, the server carries on.

### Metrics (OpenTelemetry → Prometheus)

Off by default. Two instruments, labelled by `tool` and `outcome` only:

| Metric (Prometheus name) | Kind | Answers |
|---|---|---|
| `messenger_tool_calls_total` | counter | How often is each tool used, and how often does a send get refused? |
| `messenger_tool_duration_milliseconds` | histogram | How long do calls take, and what is the slow tail? |

```bash
scripts/observability.sh up
echo 'MESSENGER_METRICS=otlp' >> .env         # then restart the MCP client
# open http://localhost:9090
```

Queries to try:

```promql
sum by (tool, outcome) (messenger_tool_calls_total)
histogram_quantile(0.95, sum by (le, tool) (rate(messenger_tool_duration_milliseconds_bucket[1h])))
```

The server pushes totals every 15 s and once more on shutdown, straight into Prometheus's OTLP receiver, so there is nothing to scrape. Each server process is its own `instance`, so always `sum by (...)` across them. Labels are deliberately low-cardinality: a recipient or call ID as a label would make every message its own time series.

### Claude Desktop's own tools (Windows)

- **Server status:** Settings → Developer → Local MCP servers.
- **Logs:** `%LOCALAPPDATA%\Claude\Logs` (`mcp-server-messenger.log`, `mcp.log`, `main.log`).
- **Chrome DevTools:** put `{"allowDevTools": true}` in `%APPDATA%\Claude\developer_settings.json`. After a relaunch, `Ctrl+Alt+I` opens DevTools: Console for client-side errors, Network for message payloads and timing.

## Tests

```bash
.venv/bin/pytest
```

Sends are mocked with `httpx.MockTransport`; no test sends a real message.

## License

MIT
