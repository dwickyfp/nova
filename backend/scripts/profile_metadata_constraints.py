"""Read-only counts for reviewed migration constraints; never export row values."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import asyncmy

from app.core.config import settings

CHECKS = {
    "workspace_live_path_duplicates": """
        SELECT COUNT(*) FROM (
            SELECT user_name, parent_path, name FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
            WHERE is_deleted = false GROUP BY user_name, parent_path, name HAVING COUNT(*) > 1
        ) duplicates
    """,
    "workspace_version_duplicates": """
        SELECT COUNT(*) FROM (
            SELECT entry_id, user_name, version FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
            GROUP BY entry_id, user_name, version HAVING COUNT(*) > 1
        ) duplicates
    """,
    "workspace_duplicate_versions_different_payload": """
        SELECT COUNT(*) FROM (
            SELECT entry_id, user_name, version FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS
            GROUP BY entry_id, user_name, version HAVING COUNT(*) > 1
            AND (COUNT(DISTINCT object_key) > 1 OR COUNT(DISTINCT etag) > 1
                 OR COUNT(DISTINCT size_bytes) > 1)
        ) conflicts
    """,
    "workspace_orphan_versions": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS v
        LEFT JOIN NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES e ON e.id = v.entry_id
        WHERE e.id IS NULL
    """,
    "workspace_version_namespace_mismatch": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS v
        JOIN NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES e ON e.id = v.entry_id
        WHERE v.user_name <> e.user_name
    """,
    "workspace_version_nonpositive": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS WHERE version <= 0
    """,
    "workspace_orphan_shares": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES s
        LEFT JOIN NOVA_SYSTEM.CONFIG_WORKSPACES w ON w.id = s.workspace_id
        WHERE w.id IS NULL
    """,
    "workspace_invalid_share_permission": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_SHARES
        WHERE permission NOT IN ('view', 'edit')
    """,
    "workspace_orphan_shared_namespace": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES e
        LEFT JOIN NOVA_SYSTEM.CONFIG_WORKSPACES w ON e.user_name = CONCAT('~ws/', w.id)
        WHERE e.user_name LIKE '~ws/%' AND w.id IS NULL
    """,
    "workspace_invalid_entry_type": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES
        WHERE entry_type NOT IN ('file', 'folder')
    """,
    "workspace_negative_entry_size": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES WHERE size_bytes < 0
    """,
    "workspace_negative_version_size": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_FILE_VERSIONS WHERE size_bytes < 0
    """,
    "workspace_null_deleted_flag": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACES WHERE is_deleted IS NULL
    """,
    "workspace_entries_null_deleted_flag": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACE_ENTRIES WHERE is_deleted IS NULL
    """,
    "workspace_empty_owner": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_WORKSPACES WHERE owner = ''
    """,
    "preferences_empty_identity": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES
        WHERE user_name = '' OR pref_key = ''
    """,
    "preferences_null_value": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_USER_PREFERENCES WHERE pref_value IS NULL
    """,
    "stages_duplicate_qualified_name": """
        SELECT COUNT(*) FROM (
            SELECT database_name, schema_name, name FROM NOVA_SYSTEM.CONFIG_STAGES
            GROUP BY database_name, schema_name, name HAVING COUNT(*) > 1
        ) duplicates
    """,
    "audit_duplicate_log_id": """
        SELECT COUNT(*) FROM (
            SELECT log_id FROM NOVA_SYSTEM.AUDIT_LOG GROUP BY log_id HAVING COUNT(*) > 1
        ) duplicates
    """,
    "audit_null_query_id": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.AUDIT_LOG WHERE query_id IS NULL
    """,
    "ai_models_orphan_provider": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_AI_MODELS m
        LEFT JOIN NOVA_SYSTEM.CONFIG_AI_PROVIDERS p ON p.id = m.provider_id
        WHERE p.id IS NULL
    """,
    "ai_aliases_orphan_provider": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES a
        LEFT JOIN NOVA_SYSTEM.CONFIG_AI_PROVIDERS p ON p.id = a.provider_id
        WHERE p.id IS NULL
    """,
    "ai_aliases_orphan_model": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES a
        LEFT JOIN NOVA_SYSTEM.CONFIG_AI_MODELS m ON m.id = a.model_id
        WHERE a.model_id IS NOT NULL AND m.id IS NULL
    """,
    "ai_aliases_provider_model_mismatch": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES a
        JOIN NOVA_SYSTEM.CONFIG_AI_MODELS m ON m.id = a.model_id
        WHERE a.provider_id <> m.provider_id
    """,
    "ai_aliases_multiple_active_defaults": """
        SELECT COUNT(*) FROM (
            SELECT function_type FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES
            WHERE is_default = true AND is_active = true
            GROUP BY function_type HAVING COUNT(*) > 1
        ) duplicates
    """,
    "ai_aliases_duplicate_name": """
        SELECT COUNT(*) FROM (
            SELECT alias_name FROM NOVA_SYSTEM.CONFIG_MODEL_ALIASES
            GROUP BY alias_name HAVING COUNT(*) > 1
        ) duplicates
    """,
    "assistant_messages_orphan_thread": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m
        LEFT JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t ON t.thread_id = m.thread_id
        WHERE t.thread_id IS NULL
    """,
    "assistant_messages_owner_mismatch": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES m
        JOIN NOVA_SYSTEM.CONFIG_ASSISTANT_THREADS t ON t.thread_id = m.thread_id
        WHERE m.user_name <> t.user_name
    """,
    "assistant_message_sequence_duplicates": """
        SELECT COUNT(*) FROM (
            SELECT thread_id, user_name, seq FROM NOVA_SYSTEM.CONFIG_ASSISTANT_MESSAGES
            GROUP BY thread_id, user_name, seq HAVING COUNT(*) > 1
        ) duplicates
    """,
    "agent_events_orphan_run": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS e
        LEFT JOIN NOVA_SYSTEM.CONFIG_AGENT_RUNS r ON r.run_id = e.run_id
        WHERE r.run_id IS NULL
    """,
    "agent_events_above_high_water": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_AGENT_RUN_EVENTS e
        JOIN NOVA_SYSTEM.CONFIG_AGENT_RUNS r ON r.run_id = e.run_id
        WHERE e.`sequence` > r.last_sequence
    """,
    "ml_version_orphan_model": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.ML_MODEL_VERSIONS v
        LEFT JOIN NOVA_SYSTEM.ML_MODELS m ON m.model_id = v.model_id
        WHERE m.model_id IS NULL
    """,
    "ml_alias_orphan_version": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.ML_MODEL_ALIASES a
        LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v
          ON v.model_id = a.model_id AND v.version = a.version
        WHERE v.model_id IS NULL
    """,
    "ml_current_version_missing_or_not_ready": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.ML_MODELS m
        LEFT JOIN NOVA_SYSTEM.ML_MODEL_VERSIONS v
          ON v.model_id = m.model_id AND v.version = m.current_version
        WHERE m.current_version > 0 AND (v.model_id IS NULL OR v.status <> 'READY')
    """,
    "semantic_active_version_missing_or_inactive": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS s
        LEFT JOIN NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS v
          ON v.view_id = s.id AND v.version = s.active_version
        WHERE s.active_version IS NOT NULL AND (v.view_id IS NULL OR v.status <> 'ACTIVE')
    """,
    "semantic_orphan_version": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_SEMANTIC_VIEW_VERSIONS v
        LEFT JOIN NOVA_SYSTEM.CONFIG_SEMANTIC_VIEWS s ON s.id = v.view_id
        WHERE s.id IS NULL
    """,
    "feature_view_active_version_missing": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_FEATURE_VIEWS f
        LEFT JOIN NOVA_SYSTEM.CONFIG_FEATURE_VIEW_VERSIONS v
          ON v.view_name = f.name AND v.version = f.active_version
        WHERE f.active_version IS NOT NULL AND v.view_name IS NULL
    """,
    "feature_group_active_version_missing": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_FEATURE_GROUPS f
        LEFT JOIN NOVA_SYSTEM.CONFIG_FEATURE_GROUP_VERSIONS v
          ON v.group_name = f.name AND v.version = f.active_version
        WHERE f.active_version IS NOT NULL AND v.group_name IS NULL
    """,
    "task_node_orphan_graph_run": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_TASK_RUNS n
        LEFT JOIN NOVA_SYSTEM.CONFIG_TASK_GRAPH_RUNS g ON g.id = n.graph_run_id
        WHERE g.id IS NULL
    """,
    "task_node_attempt_duplicates": """
        SELECT COUNT(*) FROM (
            SELECT graph_run_id, task_id, attempt FROM NOVA_SYSTEM.CONFIG_TASK_RUNS
            GROUP BY graph_run_id, task_id, attempt HAVING COUNT(*) > 1
        ) duplicates
    """,
    "task_duplicate_qualified_name": """
        SELECT COUNT(*) FROM (
            SELECT database_name, schema_name, name FROM NOVA_SYSTEM.CONFIG_TASKS
            GROUP BY database_name, schema_name, name HAVING COUNT(*) > 1
        ) duplicates
    """,
    "notebook_execution_orphan_runtime": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS e
        LEFT JOIN NOVA_SYSTEM.CONFIG_NOTEBOOK_RUNTIMES r ON r.id = e.runtime_id
        WHERE e.runtime_id IS NOT NULL AND r.id IS NULL
    """,
    "notebook_execution_runtime_scope_mismatch": """
        SELECT COUNT(*) FROM NOVA_SYSTEM.CONFIG_NOTEBOOK_EXECUTIONS e
        JOIN NOVA_SYSTEM.CONFIG_NOTEBOOK_RUNTIMES r ON r.id = e.runtime_id
        WHERE e.notebook_id <> r.notebook_id OR e.user_name <> r.user_name
    """,
    "dataviz_duplicate_idempotency_scope": """
        SELECT COUNT(*) FROM (
            SELECT owner_name, idempotency_scope FROM NOVA_SYSTEM.CONFIG_DATAVIZ_PUBLICATIONS
            GROUP BY owner_name, idempotency_scope HAVING COUNT(*) > 1
        ) duplicates
    """,
}


async def profile(host: str, port: int) -> dict:
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "consistent_snapshot": False,
        "scope": (
            "reviewed relational uniqueness, reference and scope candidates; not all constraints"
        ),
        "checks": {},
    }
    connection = await asyncmy.connect(
        host=host,
        port=port,
        user=settings.STARROCKS_ROOT_USER,
        password=settings.STARROCKS_ROOT_PASSWORD,
        connect_timeout=5,
        read_timeout=30,
    )
    try:
        async with connection.cursor() as cursor:
            for name, query in CHECKS.items():
                await cursor.execute(query)
                row = await cursor.fetchone()
                report["checks"][name] = {
                    "count": int(row[0]),
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                }
    finally:
        connection.close()
    report["finished_at"] = datetime.now(UTC).isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Existing inspected engine host")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(asyncio.run(profile(args.host, args.port)), indent=2) + "\n")


if __name__ == "__main__":
    main()
