"""MCP client: connect to a Model Context Protocol server, list and call its tools.

Implements the minimal client Nova needs for its MCP connectors: the MCP
``initialize`` handshake, ``tools/list`` and ``tools/call``.

* **http**: Streamable HTTP, a single POST endpoint that returns JSON (or an SSE
  stream). This is the modern transport, and the only one Nova invokes.
* **sse**: legacy HTTP+SSE. Discovery is attempted with the same POST exchange;
  tools are not invoked.
* **stdio**: a local subprocess. **Not executed by the server**: running an
  arbitrary command from a web request is a remote-code-execution surface. A
  ``stdio`` server is stored and shown, but discovery refuses it with a clear
  message; the operator runs such a server and exposes it over HTTP.

Connectors are registered by an active ACCOUNTADMIN, like AI provider endpoints,
so they may point at loopback or private (intranet) addresses. Link-local
addresses (including the cloud metadata service), multicast, reserved and
unspecified addresses stay blocked by the shared SSRF guard. When Nova runs in a
container, loopback goes to ``AI_PROVIDER_LOOPBACK_HOST``.

Auth headers are stored encrypted and decrypted only here, for the request to
the registered endpoint. Redirects are never followed, so a header is never sent
to another address. No header name or value is logged or put in an error.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.common.crypto import EncryptionError, decrypt
from app.common.ssrf_guard import BlockedEndpointError, guarded_async_client
from app.core.config import settings

logger = logging.getLogger(__name__)

#: The protocol revision Nova speaks. Servers negotiate; this is our proposal.
MCP_PROTOCOL_VERSION = "2025-06-18"

#: One discovery call must not hang a request.
_TIMEOUT_SECONDS = 15.0

#: A tool call may do real work (a report, a lookup); it gets longer to answer
#: than discovery, still bounded. The turn's own budget and Stop still apply.
_CALL_TIMEOUT = httpx.Timeout(60.0, connect=_TIMEOUT_SECONDS)

#: A tools/list response is metadata; a huge body is a misbehaving server.
_MAX_BODY_BYTES = 2_000_000


class McpError(RuntimeError):
    """A discovery attempt failed. The message is safe to surface."""


def _rpc(method: str, params: dict[str, Any] | None = None, *, request_id: int = 1) -> dict:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


#: MCP tool annotation hints Nova records. Anything else a server sends is dropped.
_ANNOTATION_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def _annotations(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    kept: dict[str, Any] = {
        key: raw[key] for key in _ANNOTATION_HINTS if isinstance(raw.get(key), bool)
    }
    if isinstance(raw.get("title"), str) and raw["title"].strip():
        kept["title"] = raw["title"].strip()[:128]
    return kept


def _tool_from_mcp(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one MCP tool descriptor into Nova's tool shape."""
    return {
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "input_schema": raw.get("inputSchema") or raw.get("input_schema") or {},
        "annotations": _annotations(raw.get("annotations")),
    }


def _client(timeout: float | httpx.Timeout) -> httpx.AsyncClient:
    return guarded_async_client(
        timeout=timeout,
        allow_private_network=True,
        loopback_host=settings.AI_PROVIDER_LOOPBACK_HOST,
    )


def _request_headers(server: dict[str, Any]) -> dict[str, str]:
    """Protocol headers plus the connector's decrypted auth headers, if any."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    ciphertext = server.get("auth_headers_ciphertext")
    if not ciphertext:
        return headers
    try:
        stored = json.loads(decrypt(str(ciphertext)) or "{}")
    except (EncryptionError, ValueError):
        raise McpError("The connector's saved headers cannot be read; save them again.") from None
    if not isinstance(stored, dict):
        raise McpError("The connector's saved headers cannot be read; save them again.")
    for name, value in stored.items():
        if isinstance(name, str) and isinstance(value, str):
            headers[name] = value
    return headers


async def _post(client: httpx.AsyncClient, endpoint: str, headers: dict[str, str],
                payload: dict[str, Any]) -> httpx.Response:
    # Never follow a redirect: the auth headers belong to this endpoint only.
    return await client.post(endpoint, headers=headers, json=payload, follow_redirects=False)


async def list_tools(server: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the normalized tools a server exposes.

    Raises :class:`McpError` with an actionable message on any failure.
    """
    transport = (server.get("transport") or "http").lower()
    if transport == "stdio":
        raise McpError(
            "Nova does not run stdio servers. Run the server so it answers over "
            "HTTP, then add that address as an HTTP (Streamable) server."
        )
    if transport in {"http", "sse"}:
        return await _list_tools_http(server)
    raise McpError(f"Unsupported transport {transport!r}.")


async def _list_tools_http(server: dict[str, Any]) -> list[dict[str, Any]]:
    endpoint = (server.get("endpoint") or "").strip()
    if not endpoint:
        raise McpError("This server has no endpoint URL.")
    headers = _request_headers(server)
    try:
        async with _client(_TIMEOUT_SECONDS) as client:
            await _handshake(client, endpoint, headers)
            response = await _post(client, endpoint, headers, _rpc("tools/list", {}, request_id=2))
            _check(response)
            payload = _read_rpc(response)
    except BlockedEndpointError as exc:
        raise McpError(_blocked_message(exc)) from exc
    except httpx.TimeoutException as exc:
        raise McpError("The server did not answer within 15 seconds.") from exc
    except httpx.HTTPError as exc:
        raise McpError(f"Could not reach the server: {type(exc).__name__}") from exc

    tools = payload.get("result", {}).get("tools")
    if not isinstance(tools, list):
        raise McpError("The server returned no tool list.")
    normalized = (_tool_from_mcp(item) for item in tools if isinstance(item, dict))
    return [tool for tool in normalized if tool["name"]]


def _blocked_message(exc: BlockedEndpointError) -> str:
    if exc.retryable:
        return "Could not resolve the server's host name."
    return (
        "The server endpoint is not allowed. Use an http(s) URL on a public, private "
        "or loopback address; link-local and cloud metadata addresses are blocked."
    )


async def _handshake(
    client: httpx.AsyncClient, endpoint: str, headers: dict[str, str]
) -> None:
    """Send MCP ``initialize`` then ``notifications/initialized``.

    A server that rejects the handshake is reported by its own status; a server
    that does not require it (some minimal implementations) still answers
    ``tools/list``, so a handshake failure does not abort discovery outright:
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
        response = await _post(client, endpoint, headers, init)
        if response.status_code < 300:
            session_id = response.headers.get("mcp-session-id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            await _post(
                client, endpoint, headers,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
    except httpx.HTTPError:
        # The tools/list call will surface the real error.
        logger.debug("MCP initialize handshake did not complete")


async def call_tool(
    server: dict[str, Any], tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call one HTTP MCP tool with the same network and response bounds as discovery."""
    transport = str(server.get("transport") or "http").lower()
    if transport != "http":
        raise McpError("Only Streamable HTTP MCP tools can be invoked by Nova.")
    endpoint = str(server.get("endpoint") or "").strip()
    if not endpoint:
        raise McpError("This server has no endpoint URL.")
    headers = _request_headers(server)
    try:
        async with _client(_CALL_TIMEOUT) as client:
            await _handshake(client, endpoint, headers)
            response = await _post(
                client, endpoint, headers,
                _rpc("tools/call", {"name": tool_name, "arguments": arguments}, request_id=3),
            )
            _check(response)
            payload = _read_rpc(response)
    except BlockedEndpointError as exc:
        raise McpError(_blocked_message(exc)) from exc
    except httpx.TimeoutException as exc:
        raise McpError("The server did not answer within 60 seconds.") from exc
    except httpx.HTTPError as exc:
        raise McpError(f"Could not reach the server: {type(exc).__name__}") from exc
    result = payload.get("result")
    if not isinstance(result, dict):
        raise McpError("The server returned no tool result.")
    return result


def _check(response: httpx.Response) -> None:
    if 300 <= response.status_code < 400:
        raise McpError(
            "The server answered with a redirect. Register the final endpoint URL; "
            "Nova does not follow redirects for connectors."
        )
    if response.status_code in {401, 403}:
        raise McpError(
            f"The server refused the request (HTTP {response.status_code}). "
            "Check the connector's auth headers."
        )
    if response.status_code >= 400:
        raise McpError(f"The server responded with HTTP {response.status_code}.")


def _read_rpc(response: httpx.Response) -> dict[str, Any]:
    """Read a JSON-RPC payload from either a JSON body or an SSE stream."""
    content_type = response.headers.get("content-type", "")
    text = response.text[:_MAX_BODY_BYTES]

    parsed: Any = None
    if "text/event-stream" in content_type:
        # The last `data:` line that parses as JSON is the response.
        for line in reversed(text.splitlines()):
            if line.startswith("data:"):
                try:
                    candidate = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
        if parsed is None:
            raise McpError("The server sent an event stream with no readable response.")
    else:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError("The server returned a response that is not JSON.") from exc
    if not isinstance(parsed, dict):
        raise McpError("The server returned an unexpected response shape.")
    if "error" in parsed:
        error = parsed["error"] if isinstance(parsed["error"], dict) else {}
        message = " ".join(str(error.get("message") or "unknown error").split())[:300]
        raise McpError(f"The server reported an error: {message}")
    return parsed
