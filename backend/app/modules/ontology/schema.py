"""Logical tables of the Supernova Business Ontology.

Every table is append-only: a change is a new revision row, so approved
versions stay immutable and releases can pin exact revisions. The ontology's
tables live in PostgreSQL (revision ``0020_ontology.sql``); the ``NOVA_SYSTEM``
DDL helper below remains only for the business event hub, which Intelligence owns.
"""

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
    # Measured accuracy: gold cases, evaluation runs and their per-case results.
    "eval_cases": "CONFIG_ONTOLOGY_EVAL_CASES",
    "eval_runs": "CONFIG_ONTOLOGY_EVAL_RUNS",
    "eval_results": "CONFIG_ONTOLOGY_EVAL_RESULTS",
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


async def ensure_ontology_schema() -> None:
    """Nothing to create: the migrator installs the ontology's PostgreSQL tables.

    Kept until process startup stops calling it at cutover; it never touches
    ``NOVA_SYSTEM``.
    """
