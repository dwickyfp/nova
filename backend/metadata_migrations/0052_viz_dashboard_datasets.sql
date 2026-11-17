-- Dashboard data resources can pin a dataset before any chart uses it.
CREATE OR REPLACE FUNCTION nova_meta.viz_dependency_edges(definition jsonb)
RETURNS TABLE(depends_on uuid,depends_revision bigint)
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
    SELECT (value->>'dataset_id')::uuid,(value->>'dataset_revision')::bigint
    FROM jsonb_path_query(definition,
        '$.** ? (exists(@.dataset_id) && exists(@.dataset_revision))') AS v(value)
    UNION
    SELECT (value->>'chart_id')::uuid,(value->>'revision')::bigint
    FROM jsonb_path_query(definition,
        '$.** ? (exists(@.chart_id) && exists(@.revision))') AS v(value)
    UNION
    SELECT (value->>'dataset_id')::uuid,(value->>'revision')::bigint
    FROM jsonb_path_query(definition,'$.datasets[*]') AS v(value)
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_dependency_edges(jsonb) FROM PUBLIC;

INSERT INTO nova_meta.viz_revision_dependencies(resource_id,revision,depends_on,depends_revision)
    SELECT v.resource_id,v.revision,ref.depends_on,ref.depends_revision
    FROM nova_meta.viz_revisions v
    CROSS JOIN LATERAL nova_meta.viz_dependency_edges(v.definition) ref
    ON CONFLICT DO NOTHING;

-- Refuse activation when a pre-upgrade missing edge already allowed deletion.
-- Repair the affected live definition explicitly; never rewrite its history.
DO $check_live_references$
BEGIN
    IF EXISTS (
        WITH RECURSIVE required(resource_id,revision) AS (
            (SELECT resource_id,draft_revision FROM nova_meta.viz_resources
                 WHERE deleted_at IS NULL
             UNION
             SELECT p.resource_id,p.revision FROM nova_meta.viz_publications p
                 JOIN nova_meta.viz_resources r ON r.resource_id=p.resource_id
                 WHERE r.deleted_at IS NULL)
            UNION
            SELECT d.depends_on,d.depends_revision
            FROM required r JOIN nova_meta.viz_revision_dependencies d
                ON d.resource_id=r.resource_id AND d.revision=r.revision
        )
        SELECT 1 FROM required r JOIN nova_meta.viz_resources target
            ON target.resource_id=r.resource_id WHERE target.deleted_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Live visualization references a deleted resource' USING ERRCODE='23514';
    END IF;
END
$check_live_references$;
