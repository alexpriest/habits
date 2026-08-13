#!/usr/bin/env python3
"""A minimal MCP Streamable-HTTP client — just enough to call one tool.

Alex already runs MCP servers for his fitness data on Railway, and those servers
already hold the OAuth credentials and already handle token refresh. Talking to
them is strictly better than copying a refresh token onto this machine and
reimplementing the refresh dance: one place to break, one place to fix.

Scope is deliberately tiny — initialize, then tools/call. No sampling, no
resources, no notifications, no session resumption.

⚠️ NOT every MCP server speaks this transport. `strava-mcp` does (POST /mcp,
Streamable HTTP). `oura-mcp` is on the older HTTP+SSE transport (/sse + /message)
and is NOT supported here — checked 2026-08-13, /mcp returns 404 on it. Oura is
read through its REST API with a personal access token instead, which is less
code than implementing a second transport for one caller.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT = 45


class McpError(RuntimeError):
    pass


def _post(base: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base.rstrip('/')}/mcp",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # Streamable HTTP may answer as JSON or as a single SSE event.
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise McpError(f"{base} HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise McpError(f"{base} {type(e).__name__}") from e

    for line in body.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise McpError(f"{base} returned non-JSON: {body[:120]!r}") from e


def call_tool(base: str, token: str, name: str, arguments: dict) -> str:
    """Initialize, call one tool, return its concatenated text content."""
    _post(base, token, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "habits", "version": "1"},
        },
    })
    result = _post(base, token, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    if "error" in result:
        raise McpError(f"{name}: {result['error'].get('message', 'unknown error')}")
    content = (result.get("result") or {}).get("content") or []
    return "".join(c.get("text", "") for c in content)
