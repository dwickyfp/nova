"""Seed a Nova sales agent bound to an existing published Semantic View.

Create and publish a View from ``nova_sales.ossie.yaml`` first, then run:

    cd backend
    .venv/bin/python -m app.modules.agents.examples.seed_demo root VIEW_ID

Each run creates a new agent. It never creates a second semantic definition.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.database import db
from app.modules.agents.repository import agent_repository
from app.modules.intelligence.semantic_views import semantic_view_service

#: Owner is a Nova/StarRocks user name. Root is the local default; pass another
#: as the first argument to seed under a different account.
DEFAULT_OWNER = "root"


async def seed(owner: str, view_id: str) -> tuple[str, str]:
    """Create an agent bound to a published View. Return View and agent IDs."""
    await db.init_system_pool()
    await agent_repository.ensure_schema()
    view = await semantic_view_service._get(view_id)
    if (
        not view or view.get("status") != "ACTIVE"
        or not view.get("active_version") or view.get("owner_name") != owner
    ):
        await db.close_system_pool()
        raise ValueError("Use a published Semantic View owned by this user")

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
                "customer questions from the Semantic View. State any filter or "
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
            "semantic_view_ids": [view_id],
            "visibility": "private",
        },
    )
    await db.close_system_pool()
    return view_id, agent["agent_id"]


def main() -> None:
    owner = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OWNER
    if len(sys.argv) < 3:
        raise SystemExit("Usage: seed_demo USERNAME PUBLISHED_VIEW_ID")
    view_id, agent_id = asyncio.run(seed(owner, sys.argv[2]))
    print(f"semantic_view_id: {view_id}")
    print(f"agent_id:          {agent_id}")
    print("Open Nova Studio and pick 'Revenue Analyst'.")


if __name__ == "__main__":
    main()
