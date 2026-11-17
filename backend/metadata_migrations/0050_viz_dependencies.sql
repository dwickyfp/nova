-- Immutable dependency edges protect active drafts and retained publications.
-- Tombstones retain definitions, receipts and audits; application roles cannot purge them.
CREATE TABLE nova_meta.viz_revision_dependencies (
    resource_id uuid NOT NULL,
    revision bigint NOT NULL,
    depends_on uuid NOT NULL,
    depends_revision bigint NOT NULL,
    PRIMARY KEY(resource_id,revision,depends_on,depends_revision),
    FOREIGN KEY(resource_id,revision) REFERENCES nova_meta.viz_revisions(resource_id,revision),
    FOREIGN KEY(depends_on,depends_revision) REFERENCES nova_meta.viz_revisions(resource_id,revision),
    CHECK(resource_id<>depends_on)
);
CREATE INDEX viz_dependency_target ON nova_meta.viz_revision_dependencies(depends_on,depends_revision);
CREATE INDEX viz_publication_target ON nova_meta.viz_publication_references(resource_id);
-- SELECT FOR SHARE also applies UPDATE RLS. Permit locking in the fixed
-- definer projections while refusing every updated row under that identity.
CREATE POLICY viz_resource_lock_projection ON nova_meta.viz_resources FOR UPDATE
    TO nova_meta_owner USING (nova_meta.viz_actor_valid()) WITH CHECK (false);
ALTER TABLE nova_meta.viz_revision_dependencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.viz_revision_dependencies FORCE ROW LEVEL SECURITY;
CREATE POLICY viz_dependency_owner ON nova_meta.viz_revision_dependencies
    TO nova_meta_owner USING (true) WITH CHECK (true);
CREATE POLICY viz_dependency_read ON nova_meta.viz_revision_dependencies FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent
    USING (nova_meta.viz_revision_visible(resource_id,revision));
GRANT SELECT ON nova_meta.viz_revision_dependencies TO nova_meta_web,nova_meta_query,nova_meta_agent;

CREATE FUNCTION nova_meta.viz_dependency_edges(definition jsonb)
RETURNS TABLE(depends_on uuid,depends_revision bigint)
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
    SELECT (value->>'dataset_id')::uuid,(value->>'dataset_revision')::bigint
    FROM jsonb_path_query(definition,
        '$.** ? (exists(@.dataset_id) && exists(@.dataset_revision))') AS v(value)
    UNION
    SELECT (value->>'chart_id')::uuid,(value->>'revision')::bigint
    FROM jsonb_path_query(definition,
        '$.** ? (exists(@.chart_id) && exists(@.revision))') AS v(value)
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_dependency_edges(jsonb) FROM PUBLIC;

INSERT INTO nova_meta.viz_revision_dependencies(resource_id,revision,depends_on,depends_revision)
    SELECT v.resource_id,v.revision,ref.depends_on,ref.depends_revision
    FROM nova_meta.viz_revisions v
    CROSS JOIN LATERAL nova_meta.viz_dependency_edges(v.definition) ref;

CREATE FUNCTION nova_meta.viz_capture_dependencies() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE edge record; n integer:=0; referenced nova_meta.viz_resources; source nova_meta.viz_resources;
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid() THEN
        RAISE EXCEPTION 'Invalid document transaction' USING ERRCODE='42501';
    END IF;
    SELECT * INTO STRICT source FROM nova_meta.viz_resources WHERE resource_id=NEW.resource_id;
    FOR edge IN SELECT * FROM nova_meta.viz_dependency_edges(NEW.definition)
        ORDER BY depends_on,depends_revision LOOP
        n:=n+1;
        IF n>2048 THEN
            RAISE EXCEPTION 'Document dependency limit exceeded' USING ERRCODE='23514';
        END IF;
        -- Share locks serialize reference creation with deletion, including
        -- references that were not visible when the delete request started.
        SELECT * INTO referenced FROM nova_meta.viz_resources
            WHERE resource_id=edge.depends_on FOR SHARE;
        IF NOT FOUND OR referenced.deleted_at IS NOT NULL
           OR referenced.workspace_namespace<>source.workspace_namespace
           OR NOT nova_meta.viz_can(edge.depends_on,'view') THEN
            RAISE EXCEPTION 'Document dependency is unavailable' USING ERRCODE='42501';
        END IF;
        INSERT INTO nova_meta.viz_revision_dependencies
            (resource_id,revision,depends_on,depends_revision)
            VALUES (NEW.resource_id,NEW.revision,edge.depends_on,edge.depends_revision);
    END LOOP;
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_capture_dependencies() FROM PUBLIC;
CREATE TRIGGER viz_revision_dependencies AFTER INSERT ON nova_meta.viz_revisions
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_capture_dependencies();

CREATE FUNCTION nova_meta.viz_reference_target_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE target nova_meta.viz_resources;
BEGIN
    SELECT * INTO target FROM nova_meta.viz_resources
        WHERE resource_id=NEW.resource_id FOR SHARE;
    IF NOT FOUND OR target.deleted_at IS NOT NULL
       OR NOT nova_meta.viz_can(NEW.resource_id,'view') THEN
        RAISE EXCEPTION 'Published dependency is unavailable' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_reference_target_guard() FROM PUBLIC;
CREATE TRIGGER viz_reference_target BEFORE INSERT ON nova_meta.viz_publication_references
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_reference_target_guard();

CREATE FUNCTION nova_meta.viz_delete_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF NEW.deleted_at IS NOT DISTINCT FROM OLD.deleted_at THEN RETURN NEW; END IF;
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid()
       OR OLD.owner_name<>current_setting('nova.principal',true)
       OR OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NULL THEN
        RAISE EXCEPTION 'Only the document owner may delete it' USING ERRCODE='42501';
    END IF;
    IF EXISTS (
        WITH RECURSIVE ancestors(resource_id,revision) AS (
            SELECT resource_id,revision FROM nova_meta.viz_revision_dependencies
                WHERE depends_on=OLD.resource_id
            UNION
            SELECT d.resource_id,d.revision FROM nova_meta.viz_revision_dependencies d
                JOIN ancestors a ON d.depends_on=a.resource_id AND d.depends_revision=a.revision
        )
        SELECT 1 FROM ancestors a
        JOIN nova_meta.viz_resources parent ON parent.resource_id=a.resource_id
        WHERE parent.deleted_at IS NULL
        AND (parent.draft_revision=a.revision OR EXISTS (
            SELECT 1 FROM nova_meta.viz_publications p
            WHERE p.resource_id=parent.resource_id AND p.revision=a.revision))
    ) OR EXISTS (
        SELECT 1 FROM nova_meta.viz_publication_references ref
        JOIN nova_meta.viz_publications p ON p.publication_id=ref.publication_id
        JOIN nova_meta.viz_resources parent ON parent.resource_id=p.resource_id
        WHERE ref.resource_id=OLD.resource_id AND parent.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION 'A retained document depends on this resource' USING ERRCODE='23514';
    END IF;
    NEW.deleted_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_delete_guard() FROM PUBLIC;
CREATE TRIGGER viz_resource_delete BEFORE UPDATE OF deleted_at ON nova_meta.viz_resources
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_delete_guard();

CREATE FUNCTION nova_meta.viz_deleted_receipt_visible(identity uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
    SELECT nova_meta.viz_actor_valid() AND session_user='nova_meta_web' AND EXISTS (
        SELECT 1 FROM nova_meta.viz_operations o
        JOIN nova_meta.viz_resources r ON r.resource_id=o.resource_id
        WHERE o.operation_id=identity AND o.kind='delete' AND r.deleted_at IS NOT NULL
          AND r.owner_name=current_setting('nova.principal',true)
          AND nova_meta.workspace_namespace_permission(r.workspace_namespace) IS NOT NULL
          AND o.actor_name=current_setting('nova.principal',true)
          AND o.actor_role=current_setting('nova.active_role',true)
          AND o.session_id=current_setting('nova.session_id',true)
          AND o.security_context_version=current_setting('nova.security_context_version',true)::bigint)
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_deleted_receipt_visible(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.viz_deleted_receipt_visible(uuid) TO nova_meta_web;
ALTER POLICY viz_operation_read ON nova_meta.viz_operations USING (
    nova_meta.viz_can(resource_id,'edit') OR
    (nova_meta.viz_can(resource_id,'view') AND actor_name=current_setting('nova.principal',true)
     AND actor_role=current_setting('nova.active_role',true)
     AND session_id=current_setting('nova.session_id',true)
     AND security_context_version=current_setting('nova.security_context_version',true)::bigint)
    OR nova_meta.viz_deleted_receipt_visible(operation_id));
