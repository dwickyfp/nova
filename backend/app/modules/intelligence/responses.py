"""Public evidence carries provenance without authentication session material."""

import asyncio

from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.common.responses import SanitizingJSONResponse


def public_evidence(value):
    if isinstance(value, dict):
        return {key: public_evidence(item) for key, item in value.items() if key != "session_id"}
    if isinstance(value, (list, tuple)):
        return [public_evidence(item) for item in value]
    return value


class IntelligenceResponse(SanitizingJSONResponse):
    def render(self, content: object) -> bytes:
        return super().render(public_evidence(content))


class IntelligenceRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request):
            try:
                async with asyncio.timeout(120):
                    return await original(request)
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail="Intelligence cycle time budget exhausted; retry the operation",
                ) from exc

        return bounded
