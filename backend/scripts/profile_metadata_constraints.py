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
}


async def profile(host: str, port: int) -> dict:
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "consistent_snapshot": False,
        "scope": "reviewed workspace/preferences/stages/audit candidates only",
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
