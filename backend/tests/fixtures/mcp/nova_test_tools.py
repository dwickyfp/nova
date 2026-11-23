# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.30.0"]
# ///
"""Deterministic MCP server for end-to-end tests of Nova's MCP connectors.

Run it beside a local Nova backend (the official MCP Python SDK is pinned in
the inline script metadata above, so the backend lock file is unchanged):

    uv run --script backend/tests/fixtures/mcp/nova_test_tools.py --port 8299

It serves Streamable HTTP at ``http://127.0.0.1:8299/mcp``. ``--transport stdio``
serves the same tools over stdio, which Nova stores but never executes.

``--token VALUE`` (or ``NOVA_TEST_MCP_TOKEN``) makes every HTTP request require
``Authorization: Bearer VALUE``; use it to test stored connector headers.

Tools, all deterministic:

* ``fx_convert``         pure function over a fixed rate table (read-only)
* ``get_store_targets``  structured monthly revenue targets per store city (read-only)
* ``fail_on_purpose``    always reports a tool error with a fixed message
* ``slow_report``        sleeps for the requested seconds, then answers (read-only)
* ``record_target_note`` stores a note in process memory (an action, not read-only)
"""

from __future__ import annotations

import argparse
import asyncio
import os
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

#: Units of each currency per one US dollar. Fixed so answers are checkable.
RATES_PER_USD: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "IDR": Decimal("16000"),
    "SGD": Decimal("1.35"),
    "EUR": Decimal("0.92"),
    "JPY": Decimal("150"),
}

#: Monthly revenue targets in IDR per store city, for the NOVA_DEMO orders.
STORE_TARGETS: dict[str, dict[str, int]] = {
    "2025-06": {
        "Jakarta": 25_000_000,
        "Surabaya": 6_000_000,
        "Bandung": 20_000_000,
        "Semarang": 20_000_000,
        "Yogyakarta": 8_000_000,
        "Medan": 15_000_000,
        "Makassar": 10_000_000,
        "Bali": 5_000_000,
        "Malang": 20_000_000,
        "Palembang": 2_000_000,
    },
    "2025-07": {
        "Jakarta": 10_000_000,
        "Yogyakarta": 20_000_000,
    },
}

NOTES: list[dict[str, str]] = []

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def build_server(host: str, port: int) -> FastMCP:
    server = FastMCP(
        "nova-test-tools",
        instructions="Deterministic tools for Nova connector tests.",
        host=host,
        port=port,
        json_response=True,
        stateless_http=True,
    )

    @server.tool(annotations=READ_ONLY)
    def fx_convert(amount: float, from_currency: str, to_currency: str) -> dict[str, Any]:
        """Convert an amount between USD, IDR, SGD, EUR and JPY at Nova's fixed test rates."""
        source, target = from_currency.upper(), to_currency.upper()
        unknown = sorted({source, target} - RATES_PER_USD.keys())
        if unknown:
            raise ValueError(f"Unsupported currency: {', '.join(unknown)}")
        value = Decimal(str(amount)) / RATES_PER_USD[source] * RATES_PER_USD[target]
        rate = RATES_PER_USD[target] / RATES_PER_USD[source]
        return {
            "amount": float(Decimal(str(amount))),
            "from_currency": source,
            "to_currency": target,
            "rate": float(rate.quantize(Decimal("0.000001"), ROUND_HALF_UP)),
            "converted": float(value.quantize(Decimal("0.01"), ROUND_HALF_UP)),
        }

    @server.tool(annotations=READ_ONLY)
    def get_store_targets(month: str) -> dict[str, Any]:
        """Monthly revenue targets in IDR per store city. The month is YYYY-MM."""
        targets = STORE_TARGETS.get(month.strip())
        if targets is None:
            raise ValueError(f"No targets for {month!r}. Known months: 2025-06, 2025-07")
        return {
            "month": month.strip(),
            "currency": "IDR",
            "total_target": sum(targets.values()),
            "stores": [{"city": city, "target": value} for city, value in targets.items()],
        }

    @server.tool()
    def fail_on_purpose(reason: str = "test") -> str:
        """Always fails, so clients can test how they report a tool error."""
        raise RuntimeError(f"Target service is unavailable (requested by test: {reason})")

    @server.tool(annotations=READ_ONLY)
    async def slow_report(seconds: float = 30) -> str:
        """Waits the given number of seconds (at most 120), for timeout and stop tests."""
        await asyncio.sleep(max(0.0, min(float(seconds), 120.0)))
        return f"Slow report finished after {seconds} seconds."

    @server.tool(annotations=ACTION)
    def record_target_note(month: str, note: str) -> dict[str, Any]:
        """Save a note against a month's targets. This changes the target service."""
        NOTES.append({"month": month, "note": note[:500]})
        return {"saved": True, "month": month, "notes_for_month": sum(
            1 for item in NOTES if item["month"] == month
        )}

    return server


def _with_bearer(app: Any, token: str) -> Any:
    """Wrap an ASGI app so every HTTP request needs ``Authorization: Bearer <token>``."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and dict(scope.get("headers") or []).get(
            b"authorization"
        ) != expected:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await app(scope, receive, send)

    return guarded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8299)
    parser.add_argument("--token", default=os.environ.get("NOVA_TEST_MCP_TOKEN", ""))
    args = parser.parse_args()
    server = build_server(args.host, args.port)
    if args.transport == "stdio":
        server.run("stdio")
        return
    import uvicorn

    app = server.streamable_http_app()
    if args.token:
        app = _with_bearer(app, args.token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
