"""Platform-owned skill author configuration; never persisted as a user agent."""

from datetime import datetime

SKILL_AUTHOR_ID = "nova-skill-author"


def skill_author_config(owner_name: str) -> dict:
    return {
        "agent_id": SKILL_AUTHOR_ID,
        "owner_name": owner_name,
        "name": "Nova Studio",
        "description": "Helps you write reusable skills.",
        "default_tools": [],
        "visibility": "private",
        "budget_seconds": 60,
        "budget_tokens": 16000,
        "created_at": datetime(2026, 9, 23),
        "updated_at": datetime(2026, 9, 23),
    }


def is_legacy_skill_author(agent: dict) -> bool:
    return (
        agent.get("name") == "Nova Studio"
        and agent.get("description") == "Helps you write reusable skills."
        and agent.get("default_tools") == ["load_skill"]
        and agent.get("visibility") == "private"
        and agent.get("budget_seconds") == 60
        and agent.get("budget_tokens") == 16000
        and agent.get("created_at") == agent.get("updated_at")
        and not any(
            agent.get(key)
            for key in (
                "database_name",
                "schema_name",
                "model_provider_id",
                "model_name",
                "instructions_response",
                "instructions_orchestration",
                "compiled_instructions",
                "semantic_model_id",
                "semantic_model_ids",
                "default_skills",
                "discoverable_skills",
                "sample_questions",
                "avatar",
                "color",
                "response_style",
            )
        )
    )
