"""Additive StarRocks metadata for the Supernova Business Ontology.

Every table is append-only: a change is a new revision row, so approved
versions stay immutable and releases can pin exact revisions.
"""

from app.core.database import db

ONTOLOGY_TABLES = {
    "domains": "CONFIG_ONTOLOGY_DOMAINS",
    "concepts": "CONFIG_ONTOLOGY_CONCEPTS",
    "relations": "CONFIG_ONTOLOGY_RELATIONS",
    "representations": "CONFIG_ONTOLOGY_REPRESENTATIONS",
    "proposals": "CONFIG_ONTOLOGY_PROPOSALS",
    "releases": "CONFIG_ONTOLOGY_RELEASES",
    "aliases": "CONFIG_ONTOLOGY_ALIASES",
    # The derived layer the compiler writes from published Semantic Views.
    "derived": "CONFIG_ONTOLOGY_DERIVED",
    "compilations": "CONFIG_ONTOLOGY_COMPILATIONS",
}

def revision_table_ddl(name: str) -> str:
    """An append-only revision table for shared, deployment-wide records."""
    return f"""CREATE TABLE IF NOT EXISTS NOVA_SYSTEM.{name} (
    id VARCHAR(64) NOT NULL,
    revision BIGINT NOT NULL,
    operation_id VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    active_role VARCHAR(128) NOT NULL,
    security_context_version BIGINT NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
) PRIMARY KEY(id, revision, operation_id)
DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1", "enable_persistent_index"="true")"""


ONTOLOGY_DDL = tuple(revision_table_ddl(name) for name in ONTOLOGY_TABLES.values())


async def ensure_ontology_schema() -> None:
    for ddl in ONTOLOGY_DDL:
        await db.execute_system(ddl)
