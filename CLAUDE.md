# messenger MCP

Local stdio MCP server that sends WhatsApp messages (as the user) and Discord bot DMs. See README.md for setup and operations.

## Rules

- **Confirmation is a setting, on by default.** `MESSENGER_CONFIRM_SENDS` picks between two instruction sets in `messenger/server.py`. Anything that is not a clear "no" keeps it on. Don't hardcode either behaviour, and don't change the default.
- **Never guess between people.** A name that fits more than one contact returns the matches and sends nothing (`directory.Ambiguous`). Keep it.
- **Archived chats are off limits to lookup.** `directory.py` drops them from search, listing and name matching using `whatsmeow_chat_settings.archived`; only an explicit alias, number or JID reaches one.
- **Linked IDs are not people.** A `@lid` JID maps to a phone JID through `whatsmeow_lid_map`; merge them or one person shows up twice and every name becomes ambiguous.
- **The bridge's SQLite store is read-only here.** `messenger/directory.py` opens it with `mode=ro` and only reads names, JIDs and chat times. Don't write to it, and don't expose message text.
- **Loopback only.** The bridge binds 127.0.0.1 and `messenger/whatsapp.py` talks to the literal `127.0.0.1`. Never tunnel, port-forward, or switch the MCP server off stdio.
- **Secrets:** `.env` (bot token) and `vendor/whatsapp-mcp/whatsapp-bridge/store/` (WhatsApp session keys, bridge token) never get committed, printed or logged. Error paths must not stringify an httpx request (it carries the Authorization header).
- **Don't log message text.** `logs/sent.log` and `logs/server.log` are metadata only; recipients go through `telemetry.mask`. Trace attributes follow the same rule.
- **Metric labels stay low-cardinality:** `tool` and `outcome` only. Recipients and call IDs belong on spans and log lines.
- **Nothing writes to stdout but MCP.** That includes exporters: `ConsoleSpanExporter` defaults to stdout and must be given `out=sys.stderr`.
- **Don't kill the server process to "restart" it while Claude Desktop is running.** The app won't relaunch it and shows "Server disconnected". Test changes with `scripts/doctor.py` and `scripts/inspect.sh`.
- **Sending is a live action.** Tests use `httpx.MockTransport`; a real send only happens when the user asks for one.
- **No real names or numbers in the repo.** Test data and examples use invented people and 555 numbers.
- **Bumping the bridge:** change `PIN` in `scripts/install-bridge.sh` on purpose, read the upstream diff first, and re-check that the listener is still 127.0.0.1 and `WEBHOOK_ENABLED=false` still means off.

## Commands

```bash
.venv/bin/pytest
scripts/doctor.py        # connect like a client and check everything (add --wsl for Desktop's path)
scripts/inspect.sh list  # MCP Inspector, CLI mode
scripts/pair.sh          # re-pair WhatsApp
```
