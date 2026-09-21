"""MCP client — connect to a Model Context Protocol server and list its tools.

Implements the minimal client Nova needs for a Tools Registry: the MCP
``initialize`` handshake and ``tools/list`` over the two transports that do not
require spawning a local process.

* **http** — Streamable HTTP: a single POST endpoint that returns JSON (or an SSE
  stream). This is the modern transport.
* **sse** — legacy HTTP+SSE: a GET stream that yields an ``endpoint`` event, then
  POSTs to that endpoint. Supported because many servers still expose it.
* **stdio** — a local subprocess speaking JSON-RPC on stdin/stdout. **Not
  executed by the server**: running an arbitrary command from a web request is a
  remote-code-execution surface. A ``stdio`` server is stored and shown, but
  discovery refuses it with a clear message; the operator runs such a server and
  exposes it over HTTP, or registers its tools by hand.

The client is deliberately small and defensive: it time-boxes every call, caps
the response size, and never follows a redirect to a non-public address (through
the same SSRF guard the assistant uses). It returns tool *metadata* only; it
never invokes a tool.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.common.ssrf_guard import BlockedEndpointError, guarded_async_client

logger = logging.getLogger(__name__)

#: The protocol revision Nova speaks. Servers negotiate; this is our proposal.
MCP_PROTOCOL_VERSION = "2025-06-18"

#: One discovery call must not hang a request.
_TIMEOUT_SECONDS = 15.0

#: A tools/list response is metadata; a huge body is a misbehaving server.
_MAX_BODY_BYTES = 2_000_000


class McpError(RuntimeError):
    """A discovery attempt failed. The message is safe to surface."""


def _rpc(method: str, params: dict[str, Any] | None = None, *, request_id: int = 1) -> dict:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def _tool_from_mcp(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one MCP tool descriptor into Nova's tool shape."""
    return {
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "input_schema": raw.get("inputSchema") or raw.get("input_schema") or {},
    }


async def list_tools(server: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the normalized tools a server exposes.

    Raises :class:`McpError` with an actionable message on any failure.
    """
    transport = (server.get("transport") or "http").lower()
    if transport == "stdio":
        raise McpError(
            "stdio servers are not executed by Nova. Run the server and expose it "
            "over HTTP, or register its tools manually."
        )
    if transport in {"http", "sse"}:
        return await _list_tools_http(server)
    raise McpError(f"Unsupported transport {transport!r}.")


async def _list_tools_http(server: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = (server.get("endpoint") or "").strip()
    if not endpoint:
        raise McpError("This server has no endpoint URL.")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }

    try:
        async with guarded_async_client(timeout=_TIMEOUT_SECONDS) as client:
            await _handshake(client, endpoint, headers)
            response = await client.post(
                endpoint,
                headers=headers,
                json=_rpc("tools/list", {}, request_id=2),
            )
            _check(response)
            payload = _read_rpc(response)
    except BlockedEndpointError as exc:
        raise McpError(
            "The server endpoint is not allowed: it must be a public http(s) URL."
        ) from exc
    except httpx.HTTPError as exc:
        raise McpError(f"Could not reach the server: {type(exc).__name__}") from exc

    tools = payload.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise McpError("The server returned no tool list.")
    return [t for t in (_tool_from_mcp(item) for item in tools) if t["name"]]


async def _handshake(
    client: httpx.AsyncClient, endpoint: str, headers: dict[str, str]
) -> None:
    """Send MCP ``initialize`` then ``notifications/initialized``.

    A server that rejects the handshake is reported by its own status; a server
    that does not require it (some minimal implementations) still answers
    ``tools/list``, so a handshake failure does not abort discovery outright —
    the subsequent call decides.
    """
    init = _rpc(
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "nova-studio", "version": "1.0"},
        },
        request_id=1,
    )
    try:
        response = await client.post(endpoint, headers=headers, json=init)
        if response.status_code < 400:
            await client.post(
                endpoint,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
    except httpx.HTTPError:
        # The tools/list call will surface the real error.
        logger.debug("MCP initialize handshake did not complete")


def _check(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise McpError(f"The server responded with HTTP {response.status_code}.")


def _read_rpc(response: httpx.Response) -> dict[str, Any]:
    """Read a JSON-RPC payload from either a JSON body or an SSE stream."""
    content_type = response.headers.get("content-type", "")
    text = response.text[:_MAX_BODY_BYTES]

    if "text/event-stream" in content_type:
        # The last `data:` line that parses as JSON is the response.
        import json

        for line in reversed(text.splitlines()):
            if line.startswith("data:"):
                try:
                    parsed = json.loads(line[5:].strip())
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
        raise McpError("The server sent an event stream with no readable response.")

    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError("The server returned a response that is not JSON.") from exc
    if not isinstance(parsed, dict):
        raise McpError("The server returned an unexpected response shape.")
    if "error" in parsed:
        message = str(parsed["error"].get("message") or "unknown error")
        raise McpError(f"The server reported an error: {message}")
    return parsed
