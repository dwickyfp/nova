"""Cross-row invariants required before imported control metadata can be activated."""

from __future__ import annotations

CHECKS = {
    "workspace_version_owner": (
        "SELECT count(*) AS n FROM nova_meta.config_workspace_file_versions v "
        "LEFT JOIN nova_meta.config_workspace_entries e ON e.id=v.entry_id "
        "WHERE e.id IS NULL OR v.user_name IS DISTINCT FROM e.user_name"
    ),
    "workspace_shared_namespace": (
        "SELECT count(*) AS n FROM nova_meta.config_workspace_entries e "
        "WHERE e.user_name LIKE '~ws/%' AND NOT EXISTS "
        "(SELECT 1 FROM nova_meta.config_workspaces w WHERE '~ws/'||w.id=e.user_name)"
    ),
    "model_current_version": (
        "SELECT count(*) AS n FROM nova_meta.ml_models m WHERE m.current_version>0 "
        "AND NOT EXISTS (SELECT 1 FROM nova_meta.ml_model_versions v "
        "WHERE v.model_id=m.model_id AND v.version=m.current_version)"
    ),
    "model_alias_target": (
        "SELECT count(*) AS n FROM nova_meta.ml_model_aliases a "
        "WHERE NOT EXISTS (SELECT 1 FROM nova_meta.ml_model_versions v "
        "WHERE v.model_id=a.model_id AND v.version=a.version)"
    ),
    "skill_document_chunks": (
        "SELECT count(*) AS n FROM nova_meta.config_agent_skills s "
        "WHERE s.body LIKE 'nova-skill-body:v1:%' AND ("
        "s.body !~ '^nova-skill-body:v1:[0-9a-f-]{36}:[0-9]+$' OR NOT EXISTS ("
        "SELECT 1 FROM nova_meta.config_skill_body_chunks c "
        "WHERE c.skill_id=s.skill_id AND c.owner_name=s.owner_name "
        "AND c.revision=split_part(s.body,':',3) GROUP BY c.skill_id "
        "HAVING count(*)::text=split_part(s.body,':',4) AND min(c.part_index)=0 "
        "AND max(c.part_index)=count(*)-1))"
    ),
}


async def verify_semantics(connection) -> dict:
    results = {}
    for name, query in CHECKS.items():
        cursor = await connection.execute(query)
        row = await cursor.fetchone()
        results[name] = int(row["n"])
    return {"status": "VERIFIED" if not any(results.values()) else "CONFLICT", "checks": results}
