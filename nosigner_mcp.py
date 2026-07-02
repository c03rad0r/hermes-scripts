#!/usr/bin/env python3
"""
nosigner_mcp — MCP server wrapping the NIP-46 signer.

Provides MCP tools to interact with the nosigner daemon:
- sign_event: Sign a Nostr event
- get_pubkey: Get the signer's public key
- bunker_status: Check the signer's connection status
- bunker_url: Generate a bunker:// URL for the signer

Usage:
    python3 nosigner_mcp.py [--daemon-path /path/to/nosigner.py]

The MCP server communicates with the nosigner daemon via:
1. File-based IPC if the daemon is running
2. Direct invocation for one-shot operations
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Try MCP SDK
try:
    from mcp.server import Server as McpServer
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, CallToolResult
except ImportError:
    print("MCP SDK not installed. Install with: uv pip install mcp", file=sys.stderr)
    sys.exit(1)

HOME = Path.home()
SCRIPT_DIR = HOME / ".hermes" / "scripts"
DAEMON_SCRIPT = SCRIPT_DIR / "nosigner.py"
STATE_DIR = HOME / ".hermes" / "state" / "bunker"
STATE_DB = STATE_DIR / "state.db"


def _run_daemon_cmd(args: list[str], timeout: int = 30) -> tuple[str, str, int]:
    """Run a one-shot nosigner command and return (stdout, stderr, exit_code)."""
    cmd = [sys.executable, str(DAEMON_SCRIPT)] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as e:
        return e.stdout or "", e.stderr or "", -1
    except FileNotFoundError:
        return "", f"nosigner.py not found at {DAEMON_SCRIPT}", 1


def _read_state() -> dict:
    """Read bunker state from SQLite."""
    import sqlite3

    if not STATE_DB.exists():
        return {"authorized_keys": [], "config": {}}

    try:
        conn = sqlite3.connect(str(STATE_DB))
        conn.row_factory = sqlite3.Row
        keys = [dict(row) for row in conn.execute("SELECT * FROM authorized_keys").fetchall()]
        config = dict(conn.execute("SELECT key, value FROM bunker_config").fetchall())
        conn.close()
        return {"authorized_keys": keys, "config": config}
    except Exception as e:
        return {"error": str(e)}


server = McpServer("nosigner-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="sign_event",
            description="Sign a Nostr event using the configured signer key. Returns the signed event JSON with id, pubkey, sig.",
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {"type": "integer", "description": "Event kind (e.g., 1 for text note, 0 for profile)", "default": 1},
                    "content": {"type": "string", "description": "Event content string"},
                    "tags": {"type": "array", "items": {"type": "array"}, "description": "Event tags as [[key, value], ...]", "default": []},
                    "sec_key": {"type": "string", "description": "Optional nsec or hex private key. Uses .blossom-key if omitted."},
                },
                "required": ["content"],
            },
        ),
        Tool(
            name="get_pubkey",
            description="Get the signer's public key from the configured private key.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sec_key": {"type": "string", "description": "Optional nsec or hex private key. Uses .blossom-key if omitted."},
                },
            },
        ),
        Tool(
            name="bunker_status",
            description="Check the bunker daemon status: running state, authorized keys, configured relays.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="generate_bunker_url",
            description="Generate a bunker:// URL for use with Amber or other NIP-46 clients.",
            inputSchema={
                "type": "object",
                "properties": {
                    "secret": {"type": "string", "description": "Connection secret for the bunker URL"},
                    "relays": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relay URLs",
                        "default": ["wss://nostr.oxtr.dev", "wss://relay.nsec.app", "wss://relay.primal.net"],
                    },
                    "sec_key": {"type": "string", "description": "Optional nsec or hex private key. Uses .blossom-key if omitted."},
                },
                "required": ["secret"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "sign_event":
        kind = arguments.get("kind", 1)
        content = arguments.get("content", "")
        tags = arguments.get("tags", [])
        sec_key = arguments.get("sec_key", "")

        # Use the nosigner script to sign
        args = ["--sec", sec_key or "$(cat ~/.hermes/profiles/manager/.blossom-key)"]
        # Since we can't easily sign via CLI, construct a test event directly
        from nosigner import make_event, decode_nsec, privkey_to_pubkey

        try:
            if sec_key.startswith("nsec1"):
                priv = decode_nsec(sec_key)
            elif sec_key and len(sec_key) == 64:
                priv = bytes.fromhex(sec_key)
            else:
                # Read blossom key
                key_path = HOME / ".hermes" / "profiles" / "manager" / ".blossom-key"
                if key_path.exists():
                    key_data = key_path.read_text().strip()
                    if key_data.startswith("nsec1"):
                        priv = decode_nsec(key_data)
                    else:
                        priv = bytes.fromhex(key_data)
                else:
                    return [TextContent(type="text", text="No private key found. Provide --sec_key or ensure .blossom-key exists.")]

            import time
            event = make_event(priv, kind, content, tags, int(time.time()))
            return [TextContent(type="text", text=json.dumps(event, indent=2))]
        except Exception as e:
            return [TextContent(type="text", text=f"Error signing event: {e}")]

    elif name == "get_pubkey":
        sec_key = arguments.get("sec_key", "")
        from nosigner import decode_nsec, privkey_to_pubkey

        try:
            if sec_key.startswith("nsec1"):
                priv = decode_nsec(sec_key)
            elif sec_key and len(sec_key) == 64:
                priv = bytes.fromhex(sec_key)
            else:
                key_path = HOME / ".hermes" / "profiles" / "manager" / ".blossom-key"
                if key_path.exists():
                    key_data = key_path.read_text().strip()
                    if key_data.startswith("nsec1"):
                        priv = decode_nsec(key_data)
                    else:
                        priv = bytes.fromhex(key_data)
                else:
                    return [TextContent(type="text", text="No private key found.")]

            pub_hex = privkey_to_pubkey(priv).hex()
            return [TextContent(type="text", text=f"Public key: {pub_hex}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    elif name == "bunker_status":
        state = _read_state()
        return [TextContent(type="text", text=json.dumps(state, indent=2))]

    elif name == "generate_bunker_url":
        secret = arguments.get("secret", "")
        relays = arguments.get("relays", ["wss://nostr.oxtr.dev", "wss://relay.nsec.app", "wss://relay.primal.net"])
        sec_key = arguments.get("sec_key", "")

        from urllib.parse import urlencode
        from nosigner import decode_nsec, privkey_to_pubkey

        try:
            if sec_key.startswith("nsec1"):
                priv = decode_nsec(sec_key)
            elif sec_key and len(sec_key) == 64:
                priv = bytes.fromhex(sec_key)
            else:
                key_path = HOME / ".hermes" / "profiles" / "manager" / ".blossom-key"
                if key_path.exists():
                    key_data = key_path.read_text().strip()
                    if key_data.startswith("nsec1"):
                        priv = decode_nsec(key_data)
                    else:
                        priv = bytes.fromhex(key_data)
                else:
                    return [TextContent(type="text", text="No private key found.")]

            pub_hex = privkey_to_pubkey(priv).hex()
            params = {}
            for r in relays:
                params.setdefault("relay", []).append(r)
            params["secret"] = secret
            qs = urlencode(params, doseq=True)
            url = f"bunker://{pub_hex}?{qs}"
            return [TextContent(type="text", text=f"Bunker URL:\n{url}\n\nPaste this into Amber → Bunker connection.")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


def main() -> None:
    parser = argparse.ArgumentParser(description="nosigner MCP server")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    args = parser.parse_args()

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    import asyncio
    asyncio.run(run())


if __name__ == "__main__":
    main()
