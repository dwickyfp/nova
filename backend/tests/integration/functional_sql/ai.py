from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

from .contracts import Case, FunctionSignature
from .runtime import NovaAPI

MODEL = "deepseek-v4-1-flash"
MAX_OUTPUT_TOKENS = 1024


async def verify_redaction(api: NovaAPI) -> None:
    name = "nova_sql_canary_" + uuid4().hex[:12]
    connection = await api.target.connect()
    created = False
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                f"CREATE GLOBAL FUNCTION {name}(txt STRING) RETURNS "
                'CONCAT(txt, \'{"api_key":"synthetic-redaction-canary"}\')'
            )
            created = True
            await cursor.execute("SHOW FULL GLOBAL FUNCTIONS")
            rows = await cursor.fetchall()
            matches = [row for row in rows if str(row[0]).startswith(name + "(")]
            assert len(matches) == 1, "Redaction fixture was not registered"
            assert "synthetic-redaction-canary" not in str(matches), "UDF metadata secret leak"
            assert '"api_key":"***"' in str(matches[0][4]), "Missing redaction evidence"
    finally:
        if created:
            await api.sql(f"DROP GLOBAL FUNCTION {name}(STRING)", confirm=True)
        connection.close()


class AIBudget:
    def __init__(self, path: Path, ceiling: float = 10):
        self.path = path
        self.ceiling = min(ceiling, 10)
        self.reserved = 0.0

    async def reserve(self, calls: int) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            models = await client.get("https://kenari.id/v1/models")
            models.raise_for_status()
            model = next(item for item in models.json()["data"] if item["id"] == MODEL)
            response = await client.get("https://kenari.id/api/public/pricing")
            response.raise_for_status()
        pricing = model["pricing"]
        assert pricing["unit"] == "micro_idr_per_1m_tokens" and pricing["currency"] == "IDR"
        exchange = float(response.json()["usd_idr_rate"])
        assert exchange > 0
        # Include all prompt bytes and wrapper instructions, with room for tokenizer overhead.
        per_call = (4096 * pricing["input"] + MAX_OUTPUT_TOKENS * pricing["output"]) / 1e12
        per_call = per_call / exchange * 2
        reservation = calls * per_call
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as ledger:
            fcntl.flock(ledger, fcntl.LOCK_EX)
            ledger.seek(0)
            state = json.loads(ledger.read() or '{"reserved_usd":0,"reservations":[]}')
            assert state["reserved_usd"] + reservation <= self.ceiling, "AI budget exhausted"
            state["reserved_usd"] += reservation
            state["reservations"].append({"time": time.time(), "calls": calls, "usd": reservation})
            ledger.seek(0)
            ledger.truncate()
            ledger.write(json.dumps(state, indent=2))
        self.reserved = reservation
        return {
            "model": MODEL,
            "pricing": pricing,
            "usd_idr_rate": exchange,
            "ai_spend_upper_bound_usd": reservation,
            "ai_cumulative_upper_bound_usd": state["reserved_usd"],
            "max_output_tokens_per_request": MAX_OUTPUT_TOKENS,
            "ai_cost_kind": "conservative reservation; SQL does not expose usage tokens",
        }


async def configure_aliases(api: NovaAPI) -> None:
    providers = (await api.request("GET", "/api/v1/ai/providers"))["providers"]
    assert all("api_key" not in provider for provider in providers), "Provider secret exposed"
    provider = next(item for item in providers if item["name"] == "Kenari")
    models = (await api.request("GET", f"/api/v1/ai/providers/{provider['id']}/models"))["models"]
    model = next(item for item in models if item["name"] == MODEL and item["is_active"])
    aliases = (await api.request("GET", "/api/v1/ai/aliases"))["aliases"]
    for kind in (
        "complete",
        "sentiment",
        "classify",
        "summarize",
        "extract",
        "translate",
        "filter",
    ):
        payload = {
            "alias_name": "nova_" + kind,
            "function_type": kind,
            "provider_id": provider["id"],
            "model_id": model["id"],
            "is_default": True,
            "is_active": True,
            "default_params": {
                "max_tokens": MAX_OUTPUT_TOKENS,
                "temperature": 0,
                "timeout_ms": 20000,
            },
        }
        existing = next(
            (item for item in aliases if item["function_type"] == kind and item["is_default"]), None
        )
        if existing:
            await api.request("PUT", "/api/v1/ai/aliases/" + existing["id"], json=payload)
        else:
            payload.pop("is_active")
            await api.request("POST", "/api/v1/ai/aliases", json=payload)
    status = await api.request("GET", "/api/v1/ai/aliases/udf-status")
    assert status["registered"] == 7 and status["failed"] == 0, "AI UDF registration failed"


def ai_cases(functions: list[FunctionSignature], *, ready: bool) -> list[Case]:
    signature = tuple(item.key for item in functions if item.name == "ai_query")
    prerequisite = None if ready else "AI aliases, redaction proof and cost reservation required"
    items = [
        Case(
            "ai.complete",
            "ai",
            "SELECT AI_COMPLETE('What is 6 times 3? Reply with the number only.')",
            "18",
            "label",
            signatures=signature,
        ),
        Case(
            "ai.sentiment",
            "ai",
            "SELECT AI_SENTIMENT('I love this wonderful product.')",
            "positive",
            "sentiment",
        ),
        Case(
            "ai.classify", "ai", "SELECT AI_CLASSIFY('banana','fruit,transport')", "fruit", "label"
        ),
        Case(
            "ai.summarize",
            "ai",
            "SELECT AI_SUMMARIZE('Jakarta is a large city in Indonesia. "
            "Jakarta has museums and parks.')",
            "Jakarta",
            "contains",
        ),
        Case(
            "ai.extract",
            "ai",
            "SELECT AI_EXTRACT('The name is Ada and the count is 3.', "
            '\'{"name":"string","count":"integer"}\')',
            {"name": "Ada", "count": 3},
            "ai_json",
        ),
        Case(
            "ai.translate",
            "ai",
            "SELECT AI_TRANSLATE('Selamat pagi','English')",
            "morning",
            "contains",
        ),
        Case(
            "ai.filter",
            "ai",
            "SELECT AI_FILTER('The apple is red.','mentions a fruit')",
            "true",
            "label",
        ),
    ]
    from dataclasses import replace

    return [replace(item, prerequisite=prerequisite) for item in items] + [
        Case(
            "ai.query_invalid_config",
            "ai",
            "SELECT ai_query('hello',parse_json('{}'))",
            error_code=1064,
            error_contains="model",
        ),
    ]
