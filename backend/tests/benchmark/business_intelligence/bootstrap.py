"""Create the starter view and specialists through authenticated production APIs."""

from __future__ import annotations

import json

from app.modules.agents.semantic.serialize import to_ossie_document
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.dataset import DATABASE
from tests.benchmark.business_intelligence.model import starter_definition

SPECIALISTS = {
    "Finance": "Canonical financial definitions, refunds, gross profit and decision economics.",
    "Marketing": "Governed conversion, campaign experiments, spend and incremental gross profit.",
    "Operations": (
        "Inventory, fulfillment, checkout and service evidence aligned by time and scope."
    ),
    "Executive": "Combine authorized evidence, alternatives, policy, uncertainty and outcomes.",
}


async def bootstrap(client: StudioClient, *, namespace: str, database: str = DATABASE) -> dict:
    """A separate namespace prevents an experiment from reusing learned state."""
    definition = starter_definition(database=database)
    identity = await client.request("GET", "auth/me")
    if identity["username"] == "root" or identity["active_role"] == "ACCOUNTADMIN":
        raise ValueError("Use an explicitly provisioned, read-only benchmark data identity")
    view = await client.request(
        "POST",
        "semantic-views",
        {
            "name": "nova_business_360",
            "database": database,
            "schema_name": namespace,
            "definition": json.dumps(to_ossie_document(definition)),
        },
    )
    view_id = view["id"]
    validation = await client.request("POST", f"semantic-views/{view_id}/versions/1/validate")
    if not validation["valid"]:
        raise RuntimeError("Starter Semantic View failed production validation")
    await client.request("POST", f"semantic-views/{view_id}/versions/1/publish", {})
    agents = {}
    for domain, description in SPECIALISTS.items():
        agent = await client.request(
            "POST",
            "agents",
            {
                "name": f"{namespace} {domain}",
                "description": description,
                "database_name": database,
                "semantic_view_ids": [view_id],
                "default_tools": [
                    "semantic_query",
                    "context_graph",
                    "decision_lab",
                    "diagnose_change",
                ],
                "policy": "auto_read_only",
                "budget_profile": "analyst",
                "instructions_response": (
                    "Use published calculations. Distinguish arithmetic contributions, "
                    "association, hypotheses and causal evidence. Report missing "
                    "evidence. Currency is IDR."
                ),
            },
        )
        if agent["semantic_view_ids"] != [view_id] or agent["owner_name"] != identity["username"]:
            raise RuntimeError(
                "Benchmark specialist binding differs from the requested authorized scope"
            )
        await client.request(
            "POST",
            f"agents/{agent['agent_id']}/access",
            {
                "role_name": identity["active_role"],
                "grant_type": "USAGE",
            },
        )
        checked = await client.request(
            "POST",
            f"agents/{agent['agent_id']}/access/verify",
            {
                "role_name": identity["active_role"],
            },
        )
        if not checked["all_granted"]:
            raise RuntimeError(
                "Benchmark specialist resources could not be verified for its identity"
            )
        agents[domain] = agent["agent_id"]
    return {
        "view_id": view_id,
        "agents": agents,
        "principal": identity["username"],
        "active_role": identity["active_role"],
        "namespace": namespace,
    }
