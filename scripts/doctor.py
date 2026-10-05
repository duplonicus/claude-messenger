#!/usr/bin/env python3
"""Connect to the messenger server the way a client does and report what breaks.

    scripts/doctor.py            launch it directly (how Claude Code does)
    scripts/doctor.py --wsl      launch it through wsl.exe (how Claude Desktop does)

Read-only: it lists tools and calls messenger_status and list_contacts. It
never sends a message. Exit code is 0 when every check passes.
"""

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client
except ModuleNotFoundError:
    # Started with the system Python: run again with the project's own.
    venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists() or Path(sys.prefix).resolve() == (ROOT / ".venv").resolve():
        sys.exit("The mcp package is missing. Run scripts/setup.sh first.")
    os.execv(str(venv_python), [str(venv_python), *sys.argv])

RUN = ROOT / "scripts" / "run-server.sh"


def desktop_config() -> Path:
    """Claude Desktop's config as seen from WSL. CLAUDE_DESKTOP_CONFIG overrides the search."""
    if os.environ.get("CLAUDE_DESKTOP_CONFIG"):
        return Path(os.environ["CLAUDE_DESKTOP_CONFIG"])
    found = sorted(glob.glob("/mnt/c/Users/*/AppData/Roaming/Claude/claude_desktop_config.json"))
    if not found:
        raise FileNotFoundError("no claude_desktop_config.json under /mnt/c/Users/*; set CLAUDE_DESKTOP_CONFIG")
    return Path(found[0])


EXPECTED_TOOLS = {
    "list_contacts", "search_contacts", "list_chats",
    "send_whatsapp", "send_discord_dm", "messenger_status",
}

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  -  ' + detail if detail else ''}")
    return ok


def launch_params(via_wsl: bool) -> StdioServerParameters:
    if not via_wsl:
        # The SDK starts servers with a minimal environment; pass this server's own settings through.
        own = {k: v for k, v in os.environ.items() if k.startswith(("MESSENGER_", "OTEL_"))}
        return StdioServerParameters(command=str(RUN), args=[], env={**get_default_environment(), **own})
    # Use the exact command Desktop has, so a broken config entry shows up here.
    entry = json.loads(desktop_config().read_text(encoding="utf-8"))["mcpServers"]["messenger"]
    return StdioServerParameters(command="/mnt/c/Windows/System32/" + entry["command"], args=entry["args"])


async def probe(via_wsl: bool) -> None:
    started = time.perf_counter()
    async with stdio_client(launch_params(via_wsl), errlog=sys.stderr) as (read, write):
        async with ClientSession(read, write) as session:
            init = await asyncio.wait_for(session.initialize(), timeout=30)
            ms = round((time.perf_counter() - started) * 1000)
            check(True, "initialize", f"{init.serverInfo.name}, protocol {init.protocolVersion}, {ms} ms")
            check(ms < 5000, "starts in under 5 s", f"{ms} ms")

            tools = {t.name for t in (await session.list_tools()).tools}
            check(tools == EXPECTED_TOOLS, "tool list", f"{len(tools)} tools"
                  + (f"; missing {sorted(EXPECTED_TOOLS - tools)}, extra {sorted(tools - EXPECTED_TOOLS)}"
                     if tools != EXPECTED_TOOLS else ""))

            t0 = time.perf_counter()
            status = (await session.call_tool("messenger_status", {})).content[0].text
            check("WhatsApp: connected" in status, "WhatsApp bridge", status.splitlines()[0])
            check("token: set" in status, "Discord bot token", status.splitlines()[-1])
            confirm = next((line for line in status.splitlines() if line.startswith("Confirm")), "")
            check(bool(confirm), "confirmation setting", confirm)
            check(True, "tool round trip", f"{round((time.perf_counter() - t0) * 1000)} ms")

            contacts = (await session.call_tool("list_contacts", {})).content[0].text
            check(not contacts.startswith("Error"), "contacts file", f"{len(contacts.splitlines())} aliases")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wsl", action="store_true", help="launch through wsl.exe using Claude Desktop's config entry")
    args = parser.parse_args()

    print(f"messenger doctor ({'via wsl.exe, as Claude Desktop' if args.wsl else 'direct, as Claude Code'})")
    if args.wsl:
        try:
            entry = json.loads(desktop_config().read_text(encoding="utf-8"))["mcpServers"]["messenger"]
            check(True, "desktop config entry", f"{entry['command']} {' '.join(entry['args'])}")
        except (OSError, KeyError, ValueError) as exc:
            check(False, "desktop config entry", f"{type(exc).__name__}: {exc}")
            return 1
    check(RUN.exists() and RUN.stat().st_mode & 0o111 != 0, "launcher is executable", str(RUN))

    try:
        asyncio.run(probe(args.wsl))
    except BaseException as exc:  # ExceptionGroup from anyio, timeouts, a dead process
        inner = exc.exceptions[0] if hasattr(exc, "exceptions") else exc
        check(False, "connection", f"{type(inner).__name__}: {inner}")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{'all checks passed' if not failed else 'FAILED: ' + ', '.join(failed)}")
    if failed:
        print("server log: logs/server.log   desktop's view: scripts/desktop-logs.sh")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
