"""Seed the Nova sales agent demo (Phase 12).

Creates a semantic model from the bundled Ossie example and an agent bound to
it, for the built-in ``NOVA_DEMO`` / ``NOVA_CATALOG`` sample databases (see
``docker/init-nova.sql``). Run against a Nova engine with those databases loaded:

    cd backend
    .venv/bin/python -m app.modules.agents.examples.seed_demo

It is safe to run more than once: each run creates a fresh model and agent, so
re-running does not overwrite a hand-edited one. Delete the extras from Agent
Studio afterwards if you only want one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.core.database import db
from app.modules.agents.repository import agent_repository
from app.modules.agents.semantic.ossie import parse_ossie

#: The bundled semantic model, shipped alongside this script.
OSSIE_PATH = Path(__file__).resolve().parent / "nova_sales.ossie.yaml"

#: Owner is a Nova/StarRocks user name. Root is the local default; pass another
#: as the first argument to seed under a different account.
DEFAULT_OWNER = "root"


async def seed(owner: str = DEFAULT_OWNER) -> tuple[str, str]:
    """Create the semantic model and agent. Returns ``(model_id, agent_id)``."""
    await db.init_system_pool()
    await agent_repository.ensure_schema()

    parsed = parse_ossie(OSSIE_PATH.read_text(encoding="utf-8"))

    model = await agent_repository.create_semantic_model(
        owner_name=owner,
        fields={
            "name": parsed.model["name"],
            "description": parsed.model.get("description", ""),
            "database_name": "NOVA_DEMO",
            "schema_name": None,
            "ossie_version": parsed.version,
            "definition": parsed.as_dict(),
            "source_file_id": None,
        },
    )

    agent = await agent_repository.create_agent(
        owner_name=owner,
        fields={
            "name": "Revenue Analyst",
            "description": (
                "Answers sales, order, customer, and product questions from the "
                "Nova e-commerce sample data."
            ),
            "database_name": "NOVA_DEMO",
            "schema_name": None,
            "instructions_response": (
                "You are an e-commerce sales analyst. Answer revenue, order, and "
                "customer questions from the semantic model. State any filter or "
                "time range you applied. Amounts are in IDR."
            ),
            "instructions_orchestration": (
                "For any question involving numbers, call semantic_query first, "
                "then summarise the rows it returns. Call data_to_chart when a "
                "visual answers better than a table."
            ),
            "response_style": "concise",
            "sample_questions": [
                "What was total revenue?",
                "Show revenue by product category",
                "What are the top 3 customers by revenue?",
                "Show the monthly revenue trend",
            ],
            "budget_seconds": 90,
            "tool_not_accessible": "accept",
            "default_tools": ["load_skill", "semantic_query", "data_to_chart"],
            "default_skills": [],
            "policy": "auto_read_only",
            "semantic_model_id": model["semantic_model_id"],
            "visibility": "private",
        },
    )
    await db.close_system_pool()
    return model["semantic_model_id"], agent["agent_id"]


def main() -> None:
    owner = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OWNER
    model_id, agent_id = asyncio.run(seed(owner))
    print(f"semantic_model_id: {model_id}")
    print(f"agent_id:          {agent_id}")
    print("Open Nova Studio and pick 'Revenue Analyst'.")


if __name__ == "__main__":
    main()
