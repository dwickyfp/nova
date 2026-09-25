"""FastAPI request instrumentation with route templates as bounded labels."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.observability.metrics import (
    HTTP_IN_FLIGHT,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    SQL_SOURCE,
)


class HTTPMetricsMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        raw_method = str(scope.get("method", "GET"))
        known_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        method = raw_method if raw_method in known_methods else "OTHER"
        status = 500
        HTTP_IN_FLIGHT.inc()
        source_token = SQL_SOURCE.set("web")

        async def send_with_status(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except asyncio.CancelledError:
            status = 499
            raise
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None)
            route_label = template if isinstance(template, str) else "unmatched"
            HTTP_REQUESTS.labels(method=method, route=route_label, status=str(status)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, route=route_label).observe(
                time.perf_counter() - started
            )
            HTTP_IN_FLIGHT.dec()
            SQL_SOURCE.reset(source_token)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
