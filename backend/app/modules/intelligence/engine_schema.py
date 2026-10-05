"""Additive StarRocks metadata for governed intelligence records."""

from app.core.database import db

ENGINE_TABLES = {
    "nodes": "CONFIG_CONTEXT_NODES",
    "edges": "CONFIG_CONTEXT_EDGES",
    "monitors": "CONFIG_INTELLIGENCE_MONITORS",
    "observations": "CONFIG_INTELLIGENCE_OBSERVATIONS",
    "news": "CONFIG_INTELLIGENCE_NEWS",
    "investigations": "CONFIG_INTELLIGENCE_INVESTIGATIONS",
    "decisions": "CONFIG_INTELLIGENCE_DECISIONS",
    "events": "CONFIG_DECISION_EVENTS",
    "outcomes": "CONFIG_DECISION_OUTCOMES",
    "actions": "CONFIG_INTELLIGENCE_ACTIONS",
    "action_events": "CONFIG_INTELLIGENCE_ACTION_EVENTS",
    "comparisons": "CONFIG_INTELLIGENCE_COMPARISONS",
    "editions": "CONFIG_INTELLIGENCE_EDITIONS",
    "stories": "CONFIG_INTELLIGENCE_STORIES",
}

ENGINE_DDL = tuple(
    f"""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.{name} (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    principal VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")"""
    for name in ENGINE_TABLES.values()
)


async def ensure_engine_schema() -> None:
    from app.modules.access_control.business_policy import BUSINESS_POLICY_DDL
    from app.modules.intelligence.newsroom_feedback import FEEDBACK_DDL

    for ddl in (*ENGINE_DDL, FEEDBACK_DDL):
        await db.execute_system(ddl)
    await db.execute_system(BUSINESS_POLICY_DDL)
