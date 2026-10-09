# Architecture

Last checked against the code: 2026-10-09, commit `a523cb5`.

How the messenger server fits together. Setup and day-to-day commands are in the [README](README.md).

## What it is

A local MCP server that lets an assistant (the Claude app, Claude Code) send messages for the user: WhatsApp from the user's own number, and Discord DMs from the user's own bot. It has six tools: two send, four look things up.

The design in one sentence: a short-lived Python process on stdio that turns "who" into exactly one recipient, makes one HTTP call to send, and records only metadata about it.

## System context

```mermaid
flowchart LR
    user["User"]
    client["MCP client<br/>Claude Desktop or Claude Code"]
    server["messenger<br/>MCP server"]
    bridge["WhatsApp bridge<br/>upstream Go program"]
    wa["WhatsApp servers"]
    discord["Discord REST API"]
    obs["Jaeger and Prometheus<br/>optional, local"]

    user -- "tell Zoe I am running late" --> client
    client -- "tool calls, JSON-RPC over stdio" --> server
    server -- "POST /api/send on loopback, bearer token" --> bridge
    bridge -- "WhatsApp Web protocol" --> wa
    server -- "HTTPS, bot token" --> discord
    server -. "OTLP over HTTP on loopback" .-> obs
```

| Outside thing | What crosses the line | Credential |
|---|---|---|
| MCP client | Tool calls in, text results out. The server's instructions are sent once, when the client connects | None. The client starts the server as a child process |
| WhatsApp bridge | Recipient JID and message text | Bearer token the bridge writes to its own `store/.bridge-token` |
| Discord REST API | Recipient user ID and message text | `DISCORD_BOT_TOKEN` from `.env` |
| Jaeger, Prometheus | Spans and two metrics. Never message text | None, loopback only |

The server opens no listening port. The only network listener in the system is the bridge's, on `127.0.0.1:8080`.

## What runs

| Module | Job |
|---|---|
| `scripts/run-server.sh` | What the client actually starts. Runs the project's Python with `-m messenger.server` |
| `server.py` | The six tools and the instructions sent to the client |
| `directory.py` | Who is reachable on WhatsApp. Reads the bridge's SQLite files, read-only |
| `contacts.py` | Loads and checks the alias file |
| `whatsapp.py` | The HTTP call to the bridge |
| `discord.py` | The two HTTPS calls to Discord |
| `telemetry.py` | Logs, traces and metrics |

Those modules are one process. Around it:

| Piece | Lifetime | Notes |
|---|---|---|
| messenger process | Started by the MCP client, stopped by it | No state in memory between calls. Each client connection gets its own process |
| `whatsapp-bridge` | Long-running systemd user service, `Restart=on-failure` after 30 s | Not this repo's code. Cloned to `vendor/` at a pinned commit and built locally |
| Jaeger and Prometheus | Docker Compose, started by hand | Only needed when traces or metrics are switched on |

## Tech stack

| Layer | What | Note |
|---|---|---|
| Language | Python 3.11+ | Package `messenger`, six modules |
| MCP | `mcp` 1.x, `FastMCP` | Pinned below 2. stdio transport |
| HTTP | `httpx` | Sync client, a new one per call |
| WhatsApp | [verygoodplugins/whatsapp-mcp](https://github.com/verygoodplugins/whatsapp-mcp) Go bridge | Pinned at `8954045` (v0.7.0). Only the bridge is used, not its MCP server |
| Lookup | `sqlite3` from the standard library | Opens the bridge's two databases with `mode=ro` |
| Process manager | systemd user service | For the bridge only |
| Logs | `logging` with a JSON formatter | stderr plus a rotating file |
| Traces and metrics | OpenTelemetry SDK, OTLP over HTTP | Optional extra `[otel]`; off unless a setting turns it on |
| Trace and metric backends | Jaeger 2.x, Prometheus | `observability/docker-compose.yml`, all ports on 127.0.0.1 |
| Tests | pytest | 142 passing on 2026-10-09. Sends are mocked with `httpx.MockTransport` |
| Packaging | `pyproject.toml`, hatchling, uv | |
| Secret guard | `.githooks/pre-commit` | Blocks commits containing token-shaped strings |

## Send flows

### WhatsApp

1. The client calls `send_whatsapp(to, text)`.
2. `server.py` refuses if the text is empty.
3. `directory.py` reads the bridge's SQLite store and turns `to` into one JID and a display name. If it cannot settle on exactly one, it stops here and nothing is sent.
4. `whatsapp.py` sends `POST /api/send` to the bridge on loopback, with the JID, the text and the bearer token.
5. The bridge sends the message to WhatsApp as the linked account and answers `200` with `success: true`.
6. `server.py` appends one line to `logs/sent.log`.
7. The client gets back `Sent to NAME (JID) on WhatsApp.`

The text goes out exactly as given. No signature is added, because the recipient sees it as the user writing.

### Discord

1. The client calls `send_discord_dm(alias, text)`.
2. `server.py` refuses if the text is empty.
3. `contacts.py` looks the alias up and checks it is a Discord alias. That gives a user ID.
4. `discord.py` appends the ` -claude` signature and refuses if the result is over 2000 characters.
5. First request: `POST /users/@me/channels` with the user ID. Discord answers with the DM channel's ID.
6. Second request: `POST /channels/ID/messages` with the text. Discord answers with the message ID.
7. `server.py` appends one line to `logs/sent.log`.
8. The client gets back `Sent to ALIAS on Discord.`

Discord sends are alias-only. The message comes from the bot account, so ` -claude` is appended to every one.

### What the caller sees when it fails

A tool never raises for an expected failure. It returns a string starting with `Not sent:` or `Error:`, and the assistant relays it.

| Case | Result |
|---|---|
| Empty or whitespace-only text | `Error: empty message` |
| Name fits more than one WhatsApp contact | `Not sent:` plus the list of matches with their JIDs. Nothing goes out |
| Nothing matches the recipient | `Not sent: no WhatsApp contact or chat matches ...` |
| Bridge not running, or waiting for a QR scan | `Not sent: the WhatsApp bridge is not answering ...` with the `systemctl` command to check |
| Bridge has never started (no token file) | `Not sent: no bridge token yet ...` |
| Bridge returns 401 | `Not sent: the bridge rejected its own token (401); restart the bridge` |
| Any other bridge error | `Not sent: WhatsApp send failed (STATUS): DETAIL` |
| Discord alias unknown, or it is a WhatsApp alias | `Not sent:` naming the problem and the known aliases |
| No bot token | `Not sent: DISCORD_BOT_TOKEN is not set in .env` |
| Signed message over 2000 characters | `Not sent:` with the length and the limit. No request is made |
| Discord code 50007 (cannot DM this user) | `Not sent:` explaining the three causes: no shared server, DMs off, bot blocked |
| Discord code 10013, 401, 429 | `Not sent:` with a plain explanation (unknown user, bad token, retry-after seconds) |
| Network error reaching Discord | `Not sent: could not reach Discord: EXCEPTION_NAME`. The exception itself is never printed, because the request carries the token |

Timeouts: 30 s for a WhatsApp send, 5 s for the bridge health check, 15 s for Discord. There are no retries.

## How a WhatsApp recipient is chosen

This is the core logic. `directory.resolve_recipient` turns whatever the user said into exactly one JID, or refuses.

```mermaid
flowchart TD
    start["What the user said"] --> alias{"Is it a WhatsApp alias?"}
    alias -- yes --> send["Send to that one JID"]
    alias -- no --> num{"Is it a JID or<br/>a phone number?"}
    num -- yes --> send
    num -- no --> name["Search names"]
    name --> count{"How many people fit?"}
    count -- one --> send
    count -- none --> refuse["Send nothing:<br/>no match"]
    count -- "more than one" --> ask["Send nothing:<br/>return the matches"]
```

The chart is the short version. The rules in full, tried in this order:

1. **Alias.** If it is a WhatsApp alias in `config/contacts.json`, use that alias's JID.
2. **JID.** If it is a full JID (it has an `@`), use it as typed.
3. **Phone number.** If it is digits:
   - a number that exactly matches a known contact is used;
   - otherwise, saved numbers that END with those digits are looked up. One hit is used, more than one is refused as ambiguous. This is what lets a number typed without its country code find the saved contact it belongs to;
   - otherwise, 10 or more digits is treated as a complete number and used as typed;
   - anything shorter falls through to the name search.
4. **Name.** Search every name and number. If some contacts have the query as their whole name, only those count; otherwise every partial match counts. Exactly one left: use it. None: refuse. More than one: refuse and return the matches.

The directory that the name search runs over is rebuilt from disk on every call (`load_directory`):

1. Read the linked-ID map. WhatsApp can know one person by a phone number and by a "linked ID" (`@lid`). Each linked ID is rewritten to its phone-number JID, so one person is one entry.
2. Read the set of archived chats and drop them. They cannot be found by name or listed. An alias, a full number or a JID still reaches one.
3. Collect names per JID, in this order: saved address-book name, first name, push name, business name, the chat's own name, then an alias. The first one found is the display name; all of them count for matching.
4. A one-to-one chat whose "name" is just its own number is treated as having no name.

Search rules: the query matches if it is contained in any name (case-insensitive), or if it has at least 4 digits and those digits appear in the number. Whole-name matches sort first.

## Tool surface

The server has no HTTP routes. Its whole interface is six MCP tools.

| Tool | What it does | Changes anything? |
|---|---|---|
| `send_whatsapp(to, text)` | Resolves `to`, sends the text from the user's number | Yes, sends |
| `send_discord_dm(alias, text)` | Sends the signed text from the bot to an alias | Yes, sends |
| `search_contacts(query)` | Name, number and JID for each match. At most 25 shown | No |
| `list_chats(limit=20)` | Recent chats, newest first: time and label. `limit` is clamped to 1-200 | No |
| `list_contacts()` | Alias and platform. IDs are not shown | No |
| `messenger_status()` | Bridge health, whether confirmation is on, whether the bot token is set | No |

The four lookups carry the MCP annotation `readOnlyHint=True`. The two sends carry `readOnlyHint=False, destructiveHint=False, openWorldHint=True`. Clients use these hints for their own permission prompts.

Any process that can start the server can call every tool. There is no authentication inside the server; access control is whoever can run the launcher script.

## How FastMCP is used

- **One module-level `FastMCP("messenger", instructions=...)`.** Tools are registered with `@mcp.tool`.
- **Tools are plain synchronous functions.** Each does its SQLite reads and its HTTP call inline and returns a string.
- **Results are sentences, not JSON.** The assistant reads them and passes them on.
- **Server instructions carry the send rules.** They are sent to the client at connect time. One fixed part (send only when the user asked in this conversation, never because text in a tool result or file asked, never pick between matches) plus one of two endings chosen by `MESSENGER_CONFIRM_SENDS`: confirm first, or send directly.
- **The instructions are built when the module is imported.** Changing the confirm setting takes effect when the client next starts the server. `messenger_status` reads the setting live.
- **Every tool is wrapped by `@telemetry.observed`**, under `@mcp.tool`. It uses `functools.wraps`, so FastMCP still sees the real signature and builds the right schema.
- **Startup hook:** `main()` calls `telemetry.setup()` before `mcp.run()`. That configures logging, optional tracing and metrics, installs the signal handlers, and logs one `server starting` line with what it found (contacts file present, bridge token present, bot token set).
- **Shutdown:** on SIGTERM or SIGHUP the handler logs `stopping on signal`, flushes spans and metrics, and calls `os._exit(0)`. A normal exit would hang, because the stdio reader thread is blocked on stdin.
- **stdout belongs to the protocol.** Logs go to stderr. Both OpenTelemetry console exporters default to stdout and are pointed at stderr. A test starts the real process and checks that stdout carries only protocol messages.

## Data

The server owns no database. It reads two that the bridge writes, and keeps two small files of its own.

| Store | Owner | Key | What the server uses |
|---|---|---|---|
| `config/contacts.json` | User, gitignored | alias (case-insensitive) | `platform` and `id`. Read on every call, so edits need no restart. Keys starting with `_` are comments |
| `.env` | User, gitignored | setting name | Bot token and settings. Real environment variables win over the file |
| `whatsapp.db` → `whatsmeow_contacts` | Bridge | `their_jid` | Full, first, push and business names |
| `whatsapp.db` → `whatsmeow_lid_map` | Bridge | `lid` | Linked ID → phone number |
| `whatsapp.db` → `whatsmeow_chat_settings` | Bridge | `chat_jid` | The `archived` flag only |
| `messages.db` → `chats` | Bridge | `jid` | Chat name and `last_message_time`. The messages themselves are never queried |
| `store/.bridge-token` | Bridge | | Bearer token for the bridge's API |
| `logs/server.log` | Server | | One JSON line per event. Rotates at 1 MB, keeps 3 |
| `logs/sent.log` | Server | | One line per send attempt: time, platform, recipient, character count, `sent` or `FAILED` |

What the alias loader checks: valid JSON object, each entry has `platform` and `id`, platform is `whatsapp` or `discord`, a Discord ID is 17-20 digits, a WhatsApp ID is a JID or a 7-15 digit number, and no alias appears twice once case is ignored.

Settings:

| Setting | Default | Effect |
|---|---|---|
| `MESSENGER_CONFIRM_SENDS` | `yes` | Only `no`, `false`, `0` or `off` turn confirmation off. Anything else, including a typo, keeps it on |
| `DISCORD_BOT_TOKEN` | unset | Discord sends are refused without it |
| `MESSENGER_LOG_LEVEL` | `INFO` | |
| `MESSENGER_TRACES` | `off` | `otlp` or `console` |
| `MESSENGER_METRICS` | `off` | `otlp` or `console` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://127.0.0.1:4318` | Where traces go |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Prometheus's OTLP receiver on `127.0.0.1:9090` | Where metrics go |
| `MESSENGER_HOME` | the repo root | Where every path above is resolved from |

## Observability

One decorator, `telemetry.observed`, produces all three signals for every tool call: a log line always, a span if traces are on, and two metrics if metrics are on. Jaeger also derives its own per-tool metrics from the spans and stores them in Prometheus.

| Signal | What is recorded | What is never recorded |
|---|---|---|
| Log line | Tool, 8-character call ID, recipient with a phone number masked to its last 4 digits, text length, duration, outcome | Message text, tokens |
| Span | The same fields, plus child spans for each HTTP call to the bridge or Discord | Message text |
| Metrics | `messenger.tool.calls` (counter) and `messenger.tool.duration` (histogram, ms) | Recipient and call ID. Labels are `tool` and `outcome` only |

Outcome is read from the tool's reply: `sent` if it starts with `Sent to`, `refused` if it starts with `Not sent:` or `Error:`, `ok` for a lookup, `crashed` if the function raised.

Details that matter:

- Tool spans are created with `SpanKind.SERVER`. Jaeger's Monitor tab only counts server spans.
- Metrics are pushed, not scraped. Prometheus runs with `--web.enable-otlp-receiver` and has no scrape config.
- Each process reports as its own instance (host name plus process ID), so queries sum across instances.
- If the OpenTelemetry packages are not installed, the server logs one warning and runs with the signal off. If no collector is listening, the server carries on.
- Jaeger keeps traces in memory (up to 100,000). Prometheus keeps 30 days in a Docker volume.

Three scripts cover the rest: `scripts/doctor.py` connects the way a client does and checks startup time, the tool list, bridge health, the token and the contacts file without sending anything; `scripts/inspect.sh` opens the MCP Inspector; `scripts/desktop-logs.sh` tails Claude Desktop's own log for this server.

## Install and run

There is no deploy step and no CI. The server runs from a checkout.

1. `git clone`, then `scripts/setup.sh`.
2. It creates the virtual environment, installs the package and turns on the pre-commit hook.
3. It creates `config/contacts.json` and `.env` if they are missing.
4. It asks "confirm before every send?" and writes the answer to `.env` as `MESSENGER_CONFIRM_SENDS`.
5. It asks "set up WhatsApp?" (default no). On yes:
   - `install-bridge.sh` clones the bridge at the pinned commit and builds it;
   - the systemd unit template is filled in, and `whatsapp-bridge` is enabled and started;
   - `scripts/pair.sh` shows the QR code to scan from the phone.
6. It prints the line that registers `scripts/run-server.sh` with the MCP client.
7. `scripts/doctor.py` checks the result.

- **Launch:** the client runs `scripts/run-server.sh`, which execs the project's own Python with `-m messenger.server`. On Windows, Claude Desktop reaches it through `wsl.exe`.
- **Updating:** pull, then restart the MCP client. The client does not restart a server that was stopped from outside.
- **Bridge build:** `GOFLAGS=-mod=readonly`, `GOTOOLCHAIN=local`, cgo on. It builds what `go.sum` pins and downloads nothing extra.
- **Bridge service settings:** webhooks off, media auto-download off, `UMask=0077`, output to the journal.
- **Rollback:** check out the previous commit and restart the client. For the bridge, set `PIN` back and re-run `install-bridge.sh`.

## Key decisions and their cost

| Decision | Why | Cost |
|---|---|---|
| stdio transport, no listening port | The server stays reachable only by a process that starts it | One process per client connection. No shared state, and metrics come from several instances |
| Use an upstream Go bridge for WhatsApp, pinned to a commit | The bridge holds the WhatsApp session keys, so its version only changes on purpose | Bumping means reading the upstream diff and re-checking the loopback bind and the webhook switch |
| Use only the bridge, not the upstream MCP server | The upstream MCP server can read message history. This one is send-only | Lookup had to be written here, against the bridge's SQLite schema |
| Read the bridge's SQLite files directly, read-only | Names, numbers and chat times come straight from the store the bridge already keeps | Coupled to upstream's table and column names. A schema change breaks lookup |
| Never pick between people | A name that fits two contacts returns both and sends nothing | An extra turn whenever a name is shared |
| Merge linked IDs into the phone-number contact | Without it one person appears twice and every name is ambiguous | Depends on the bridge's `whatsmeow_lid_map` being filled in |
| Archived chats are invisible to name lookup | An archived chat is never chosen by a name match | An archived contact must be named by alias, number or JID |
| Confirmation is a setting, default on, and fails toward asking | A sent message cannot be recalled | It is an instruction to the model, not a lock. The hard gate is the client's own tool-approval prompt |
| Discord is alias-only, and from a bot | The bot can only DM a user ID, and a signature tells the reader a bot wrote it | No name search on Discord. The recipient must share a server with the bot |
| Metadata-only logs, masked recipients | Message text and tokens never reach a log, a span or a metric | A send cannot be reconstructed from the logs |
| Metric labels are `tool` and `outcome` only | A recipient or call ID as a label would make every message its own time series | Per-recipient questions are answered from spans and logs, not metrics |
| Telemetry is an optional install, off by default | The server runs with two dependencies | Two code paths: with and without OpenTelemetry |
| `127.0.0.1` written as a literal, not `localhost` | Nothing in the WhatsApp path resolves a name | The bridge port is fixed in code at 8080 |
| Rebuild the directory from SQLite on every call | No cache to go stale; a new contact is findable at once | A few full-table reads per call |

## Known limits

- **Send-only.** It cannot read replies or message history.
- **Confirmation is advice to the model.** The server cannot tell whether the user was asked. Only the client's permission prompt can block a call.
- **No rate limit and no allowlist in the server.** Any recipient the tools can resolve can be messaged, as often as the tools are called.
- **WhatsApp runs on an unofficial client.** WhatsApp's terms forbid it and accounts can be banned.
- **`logs/sent.log` is not masked.** A successful WhatsApp send writes the full JID there. `logs/server.log`, spans and metrics use the masked form.
- **Masking covers numbers only.** A recipient typed as a name or alias is logged as typed.
- **No retries.** A failed send is reported and left to the user.
- **Discord messages over 2000 characters are refused, not split.** WhatsApp length is not checked here.
- **Linux only**, including WSL. The WhatsApp side needs systemd user services.
- **No CI.** Tests run locally.
- **Lookup depends on upstream's schema** at the pinned commit.
- **Outcome is inferred from the reply's first words.** Changing a reply's wording changes how it is counted.

## If it had to grow (not built)

None of this exists.

- **Reading replies.** A tool that reads recent messages from one contact would be the first step toward short back-and-forth exchanges. It would end the "never reads message text" property, so it would need its own limits: one contact at a time and a cap on exchanges.
- **A hard send gate in the server.** A recipient allowlist or a per-hour send cap would make the safety rules enforceable instead of instructed.
- **More than one user or machine.** stdio and a local bridge assume one person on one computer. A shared service would need an HTTP transport, real authentication and a bridge per account.
- **A cached directory.** With a much larger address book, rebuilding the directory on each call would be replaced by a cache keyed on the database files' modification times.
