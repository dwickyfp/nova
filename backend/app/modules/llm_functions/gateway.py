"""Internal LLM gateway for the ``AI_*`` SQL functions.

StarRocks' ``ai_query()`` sends an OpenAI-style chat request to the endpoint in
the function body, with the body's ``api_key`` as a bearer token. Pointing that
at Nova instead of the provider keeps the provider's key out of the engine: the
function body carries only a token that names one alias, and this gateway adds
the real key. It is also the only place the token usage of ``AI_*`` is visible.

The engine does not forward who ran the SQL, so usage is recorded per function
and alias, not per user.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.common.ssrf_guard import BlockedEndpointError, guarded_async_client
from app.core.config import settings
from app.core.database import db
from app.modules.ai_ml.service import ai_service
from app.observability.metrics import LLM_FUNCTION_REQUESTS, LLM_FUNCTION_TOKENS

logger = logging.getLogger(__name__)
router = APIRouter()

GATEWAY_PATH = "/api/v1/internal/llm/chat/completions"
TOKEN_PREFIX = "nvf1"
#: Request fields a function body may set. ``model`` is not among them: the
#: alias decides the model, so a token cannot be used to reach another one.
FORWARDED_PARAMETERS = ("temperature", "max_tokens", "top_p")
_RESOLUTION_TTL_SECONDS = 30.0

USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.USAGE_LLM_FUNCTIONS (
  usage_id          BIGINT NOT NULL AUTO_INCREMENT,
  recorded_at       DATETIME NOT NULL,
  function_name     VARCHAR(64) NOT NULL,
  alias_name        VARCHAR(128),
  model_name        VARCHAR(256),
  status            VARCHAR(32) NOT NULL,
  request_count     BIGINT NOT NULL,
  prompt_tokens     BIGINT,
  completion_tokens BIGINT,
  total_tokens      BIGINT,
  duration_ms       BIGINT
) DUPLICATE KEY(usage_id, recorded_at)
DISTRIBUTED BY HASH(usage_id) BUCKETS 1
PROPERTIES("replication_num"="1")
"""


def alias_token(alias_id: str) -> str:
    """The bearer token a function body presents for ``alias_id``.

    It is an HMAC over the alias id, so it needs no storage, cannot be turned
    into a token for another alias, and stops working when the alias is deleted
    or ``SECRET_KEY`` is rotated. It is worthless against the provider.
    """
    return f"{TOKEN_PREFIX}.{alias_id}.{_signature(alias_id)}"


def alias_from_token(token: str) -> str | None:
    prefix, _, rest = token.partition(".")
    alias_id, _, signature = rest.rpartition(".")
    if prefix != TOKEN_PREFIX or not alias_id or not signature:
        return None
    return alias_id if hmac.compare_digest(signature, _signature(alias_id)) else None


def _signature(alias_id: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, b"nova-llm-function:" + alias_id.encode(), hashlib.sha256).hexdigest()


def gateway_endpoint() -> str:
    return settings.LLM_GATEWAY_URL.rstrip("/") + GATEWAY_PATH


def provider_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"


@dataclass(frozen=True)
class Resolution:
    function_name: str
    alias_name: str
    model_name: str
    endpoint: str
    api_key: str


class GatewayRefusal(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class UsageMeter:
    """Token usage summed in memory and written in batches.

    One ``AI_*`` call per row would otherwise mean one metadata write per row.
    """

    def __init__(self) -> None:
        self._totals: dict[tuple[str, str, str, str], list[int]] = {}

    def record(
        self, resolved: Resolution, status: str, usage: dict[str, Any] | None, duration_ms: int
    ) -> None:
        usage = usage if isinstance(usage, dict) else {}
        prompt, completion = _tokens(usage.get("prompt_tokens")), _tokens(
            usage.get("completion_tokens")
        )
        total = _tokens(usage.get("total_tokens")) or prompt + completion
        key = (resolved.function_name, resolved.alias_name, resolved.model_name, status)
        totals = self._totals.setdefault(key, [0, 0, 0, 0, 0])
        for index, value in enumerate((1, prompt, completion, total, duration_ms)):
            totals[index] += value
        LLM_FUNCTION_REQUESTS.labels(function=resolved.function_name, status=status).inc()
        for kind, value in (("prompt", prompt), ("completion", completion)):
            if value:
                LLM_FUNCTION_TOKENS.labels(function=resolved.function_name, kind=kind).inc(value)

    async def flush(self) -> int:
        """Write and clear the pending totals. Returns the rows written."""
        pending, self._totals = self._totals, {}
        if not pending:
            return 0
        now = datetime.now(UTC).replace(tzinfo=None)
        rows = [(now, *key, *totals) for key, totals in pending.items()]
        try:
            async with db.system_conn() as conn, conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO NOVA_SYSTEM.USAGE_LLM_FUNCTIONS (recorded_at, function_name, "
                    "alias_name, model_name, status, request_count, prompt_tokens, "
                    "completion_tokens, total_tokens, duration_ms) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
        except Exception:
            # Keep the totals for the next flush rather than lose the usage.
            for key, totals in pending.items():
                merged = self._totals.setdefault(key, [0, 0, 0, 0, 0])
                for index, value in enumerate(totals):
                    merged[index] += value
            raise
        return len(rows)

    async def run(self, stop: asyncio.Event) -> None:
        """Flush on an interval until ``stop`` is set, then once more."""
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.LLM_USAGE_FLUSH_SECONDS)
            try:
                await self.flush()
            except Exception as exc:
                logger.warning("Could not record AI function usage: %s", type(exc).__name__)


def _tokens(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


class LLMGateway:
    def __init__(self) -> None:
        self.meter = UsageMeter()
        self._resolved: dict[str, tuple[float, Resolution]] = {}
        self._slots: asyncio.Semaphore | None = None

    async def ensure_schema(self) -> None:
        await db.execute_system(USAGE_SCHEMA)

    def forget(self, alias_id: str | None = None) -> None:
        """Drop cached alias resolutions after an alias or provider changes."""
        if alias_id is None:
            self._resolved.clear()
        else:
            self._resolved.pop(alias_id, None)

    async def resolve(self, alias_id: str) -> Resolution:
        cached = self._resolved.get(alias_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        from app.modules.llm_functions.service import UDF_TEMPLATES, llm_function_service

        alias = await llm_function_service.get_alias(alias_id)
        if not alias or not alias.get("is_active", True):
            raise GatewayRefusal(401, "Unknown AI function token")
        template = UDF_TEMPLATES.get(alias["function_type"])
        provider = await ai_service.get_provider(alias["provider_id"])
        if not template or not provider or not provider.get("is_active", True):
            raise GatewayRefusal(503, "AI function provider is unavailable")
        model = await ai_service.get_model(alias["model_id"]) if alias.get("model_id") else None
        if provider.get("type") == "decision" or (model and model.get("type") == "decision"):
            raise GatewayRefusal(503, "AI function provider is unavailable")
        api_key = await ai_service.get_provider_api_key(alias["provider_id"])
        if not api_key or not provider.get("endpoint"):
            raise GatewayRefusal(503, "AI function provider is unavailable")
        resolved = Resolution(
            function_name=template["function_name"],
            alias_name=alias["alias_name"],
            model_name=alias.get("model_name") or (model["name"] if model else "gpt-4o-mini"),
            endpoint=provider_endpoint(provider["endpoint"]),
            api_key=api_key,
        )
        self._resolved[alias_id] = (time.monotonic() + _RESOLUTION_TTL_SECONDS, resolved)
        return resolved

    async def complete(self, token: str, body: Any) -> tuple[int, dict]:
        alias_id = alias_from_token(token)
        if alias_id is None:
            raise GatewayRefusal(401, "Unknown AI function token")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            raise GatewayRefusal(400, "Expected a chat completion request")
        resolved = await self.resolve(alias_id)
        payload = {"model": resolved.model_name, "messages": body["messages"]}
        payload.update({name: body[name] for name in FORWARDED_PARAMETERS if name in body})
        if self._slots is None:
            self._slots = asyncio.Semaphore(settings.LLM_GATEWAY_MAX_CONCURRENCY)
        started = time.monotonic()
        status, content = "error", None
        try:
            timeout = settings.LLM_GATEWAY_TIMEOUT_SECONDS
            async with self._slots, guarded_async_client(timeout=timeout) as client:
                response = await client.post(
                    resolved.endpoint,
                    headers={"Authorization": f"Bearer {resolved.api_key}"},
                    json=payload,
                )
            if response.status_code != 200:
                raise GatewayRefusal(
                    response.status_code if response.status_code in {408, 429} else 502,
                    f"AI provider answered {response.status_code}",
                )
            content = response.json()
            if not isinstance(content, dict):
                raise ValueError("unexpected provider response")
            status = "success"
            return 200, content
        except GatewayRefusal:
            raise
        except (BlockedEndpointError, httpx.HTTPError, ValueError) as exc:
            # The provider's message can echo the request or the key; only the
            # kind of failure leaves this process.
            logger.warning("AI function provider request failed: %s", type(exc).__name__)
            raise GatewayRefusal(502, "AI provider request failed") from exc
        finally:
            self.meter.record(
                resolved,
                status,
                content.get("usage") if content else None,
                int((time.monotonic() - started) * 1000),
            )


llm_gateway = LLMGateway()


@router.post("/chat/completions", include_in_schema=False)
async def chat_completions(request: Request) -> JSONResponse:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer":
            raise GatewayRefusal(401, "Unknown AI function token")
        try:
            body = await request.json()
        except ValueError:
            raise GatewayRefusal(400, "Expected a chat completion request") from None
        status_code, content = await llm_gateway.complete(token.strip(), body)
    except GatewayRefusal as refusal:
        return JSONResponse(
            status_code=refusal.status_code,
            content={"error": {"message": refusal.message, "type": "nova_gateway_error"}},
        )
    return JSONResponse(status_code=status_code, content=content)
