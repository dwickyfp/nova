"""FastAPI request instrumentation with route templates as bounded labels."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi.routing import RouteContext, iter_route_contexts
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response
from starlette.routing import Match

from app.observability.metrics import (
    HTTP_IN_FLIGHT,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    SQL_SOURCE,
)


class HTTPMetricsMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._route_owner: Any = None
        self._route_contexts: dict[int, list[RouteContext]] = {}

    def _route_template(self, scope: dict) -> str:
        owner = scope.get("app")
        if owner is not self._route_owner:
            contexts: dict[int, list[RouteContext]] = {}
            for context in iter_route_contexts(getattr(owner, "routes", [])):
                contexts.setdefault(id(context.original_route), []).append(context)
            self._route_contexts = contexts
            self._route_owner = owner
        route = scope.get("route")
        # Included routers retain their original route in the request scope.
        # Match its effective context so prefixes and aliases stay distinct.
        for context in self._route_contexts.get(id(route), []):
            match, _ = context.matches(scope)
            if match in {Match.FULL, Match.PARTIAL} and isinstance(context.path, str):
                return context.path
        return "unmatched"

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
            route_label = self._route_template(scope)
            HTTP_REQUESTS.labels(method=method, route=route_label, status=str(status)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, route=route_label).observe(
                time.perf_counter() - started
            )
            HTTP_IN_FLIGHT.dec()
            SQL_SOURCE.reset(source_token)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
