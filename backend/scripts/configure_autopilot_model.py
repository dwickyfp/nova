"""Set the registered DeepSeek LLM using an existing ACCOUNTADMIN API session.

Supply NOVA_ACCESS_TOKEN through the environment. No provider key is read,
copied, or printed; the API validates the encrypted registry credential.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from urllib.parse import urlsplit

import httpx

from app.modules.ai_ml.decision_settings import DecisionSettings
from app.modules.query_autopilot.judge import MODEL_NAME


async def configure(client: httpx.AsyncClient) -> dict:
    async def request(method: str, path: str, **kwargs):
        response = await client.request(method, "/api/v1/" + path, **kwargs)
        if not response.is_success:
            raise ValueError(f"Nova API refused {path} (HTTP {response.status_code})")
        return response.json()

    identity = await request("GET", "auth/me")
    if identity.get("active_role") != "ACCOUNTADMIN":
        raise ValueError("An authenticated session with active ACCOUNTADMIN is required")
    providers = (await request("GET", "ai/providers"))["providers"]
    matches = [p for p in providers if p["name"] == "Kenari" and p.get("is_active", True)]
    if len(matches) != 1 or not matches[0].get("has_api_key"):
        raise ValueError("Exactly one active Kenari provider with a stored credential is required")
    provider = matches[0]
    models = (await request("GET", f"ai/providers/{provider['id']}/models"))["models"]
    selected = [
        m
        for m in models
        if m["name"] == MODEL_NAME and m["type"] == "llm" and m.get("is_active", True)
    ]
    if len(selected) != 1:
        raise ValueError("Exactly one active registered DeepSeek Flash LLM is required")
    model_id = selected[0]["id"]
    previous = DecisionSettings.model_validate(await request("GET", "ai/decision-settings"))
    intended = previous.model_copy(update={"light_model_id": model_id, "heavy_model_id": model_id})
    # These existing endpoints are separate durable writes. A failed second
    # write is reported as partial configuration and can be safely rerun.
    await request("PUT", "ai/default-model", json={"model_id": model_id})
    try:
        await request("PUT", "ai/decision-settings", json=intended.model_dump())
    except Exception:
        raise ValueError(
            "Global default saved; routing update failed. Rerun to reconcile."
        ) from None
    global_value = await request("GET", "ai/default-model")
    routing = DecisionSettings.model_validate(await request("GET", "ai/decision-settings"))
    if global_value != {"model_id": model_id} or routing != intended:
        raise ValueError("Stored configuration differs from the requested model selection")
    return {
        "provider": "Kenari",
        "model": MODEL_NAME,
        "model_id": model_id,
        "global_default": "verified",
        "light_and_heavy": "verified",
        "decision_and_routing_parameters": "preserved",
    }


async def main(url: str):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use a Nova base URL without credentials, query, or fragment")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Use HTTPS, or HTTP on a local loopback address")
    token = os.getenv("NOVA_ACCESS_TOKEN")
    if not token:
        raise ValueError("NOVA_ACCESS_TOKEN must contain an existing administrator session")
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers={"Authorization": "Bearer " + token},
        timeout=30,
        follow_redirects=False,
    ) as client:
        import json

        print(json.dumps(await configure(client), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.url))
    except (ValueError, httpx.HTTPError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else "Nova API connection unavailable")
        raise SystemExit(1) from None
