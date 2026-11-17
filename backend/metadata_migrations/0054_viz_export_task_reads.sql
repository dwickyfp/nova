-- Scheduled dashboard exports (0053) run in the task worker as a business role's
-- scheduled-task account. A run reads the published dashboard, its charts and
-- datasets, resolves the dashboard's shared workspace and adds one workbook file
-- to it. Extend exactly those reads and the workspace entry insert to the task
-- login. Document definitions, grants, publications, shares, file versions and
-- every update stay with the web login; data access remains with the engine and
-- Ranger under the delegated account.

-- Dataviz document reads ----------------------------------------------------------
CREATE OR REPLACE FUNCTION nova_meta.viz_actor_valid() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
       AND session_user IN ('nova_meta_web','nova_meta_query','nova_meta_agent','nova_meta_task')
       AND current_setting('nova.service_identity',true)=session_user
       AND current_setting('nova.principal_type',true)='USER'
       AND nullif(current_setting('nova.session_id',true),'') IS NOT NULL
$body$;

GRANT EXECUTE ON FUNCTION nova_meta.viz_actor_valid(),nova_meta.viz_can(uuid,text),
    nova_meta.viz_revision_visible(uuid,bigint) TO nova_meta_task;
ALTER POLICY viz_resource_read ON nova_meta.viz_resources
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_task;
ALTER POLICY viz_revision_read ON nova_meta.viz_revisions
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_task;
ALTER POLICY viz_publication_read ON nova_meta.viz_publications
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_task;
ALTER POLICY viz_reference_read ON nova_meta.viz_publication_references
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_task;
ALTER POLICY viz_dependency_read ON nova_meta.viz_revision_dependencies
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_task;
GRANT SELECT ON nova_meta.viz_resources,nova_meta.viz_revisions,nova_meta.viz_publications,
    nova_meta.viz_publication_references,nova_meta.viz_revision_dependencies TO nova_meta_task;

-- Semantic View datasets, as the query workload reads them (0051) -------------------
GRANT EXECUTE ON FUNCTION nova_meta.intelligence_actor_bound() TO nova_meta_task;
GRANT SELECT ON nova_meta.config_semantic_views,
    nova_meta.config_semantic_view_versions,nova_meta.config_entities TO nova_meta_task;
CREATE POLICY semantic_views_task_read ON nova_meta.config_semantic_views
    FOR SELECT TO nova_meta_task USING (
        nova_meta.intelligence_actor_bound() AND status='ACTIVE'
        AND (visibility='PUBLIC' OR owner_name=current_setting('nova.principal',true))
    );
CREATE POLICY semantic_versions_task_read ON nova_meta.config_semantic_view_versions
    FOR SELECT TO nova_meta_task USING (
        nova_meta.intelligence_actor_bound() AND status IN ('ACTIVE','DEPRECATED')
        AND EXISTS (SELECT 1 FROM nova_meta.config_semantic_views v WHERE v.id=view_id)
    );
CREATE POLICY entities_task_read ON nova_meta.config_entities
    FOR SELECT TO nova_meta_task USING (nova_meta.intelligence_actor_bound());

-- Workspace resolution and one new entry -------------------------------------------
CREATE OR REPLACE FUNCTION nova_meta.workspace_permission(identity text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE actor text; roles jsonb; workspace_owner text; result text;
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_agent','nova_meta_notebook',
                            'nova_meta_task')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user THEN
        RETURN NULL;
    END IF;
    actor:=current_setting('nova.principal',true);
    roles:=nullif(current_setting('nova.workspace_roles',true),'')::jsonb;
    IF jsonb_typeof(roles) IS DISTINCT FROM 'array'
       OR NOT roles ? current_setting('nova.active_role',true) THEN RETURN NULL; END IF;
    SELECT w.owner INTO workspace_owner FROM nova_meta.config_workspaces w
        WHERE w.id=identity AND w.is_deleted=false;
    IF workspace_owner IS NULL THEN RETURN NULL; END IF;
    IF workspace_owner=actor THEN RETURN 'owner'; END IF;
    SELECT CASE WHEN bool_or(s.permission='edit') THEN 'edit' ELSE 'view' END INTO result
        FROM nova_meta.config_workspace_shares s
        WHERE s.workspace_id=identity AND NOT s.is_deleted AND roles ? s.role_name
        HAVING count(*)>0;
    RETURN result;
END
$body$;

CREATE OR REPLACE FUNCTION nova_meta.workspace_namespace_permission(namespace text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE roles jsonb;
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_query','nova_meta_agent','nova_meta_notebook',
                            'nova_meta_task')
       OR nova_meta.actor_context_valid() IS DISTINCT FROM true
       OR current_setting('nova.service_identity',true) IS DISTINCT FROM session_user
       OR namespace IS NULL THEN
        RETURN NULL;
    END IF;
    roles:=nullif(current_setting('nova.workspace_roles',true),'')::jsonb;
    IF jsonb_typeof(roles) IS DISTINCT FROM 'array'
       OR NOT roles ? current_setting('nova.active_role',true) THEN RETURN NULL; END IF;
    IF namespace=current_setting('nova.principal',true) AND position('/' IN namespace)=0 THEN
        RETURN 'owner';
    END IF;
    IF left(namespace,4)='~ws/' THEN
        RETURN nova_meta.workspace_permission(substr(namespace,5));
    END IF;
    RETURN NULL;
END
$body$;

-- Only new entries: the task login never updates, renames or deletes files.
CREATE OR REPLACE FUNCTION nova_meta.workspace_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Workspace import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF (current_user='nova_meta_web' AND TG_OP IN ('INSERT','UPDATE'))
       OR (current_user='nova_meta_task' AND TG_OP='INSERT'
           AND TG_TABLE_NAME='config_workspace_entries') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Workspace mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;

-- The new entry is audited like any other; the task login may only record an insert.
CREATE OR REPLACE FUNCTION nova_meta.audit_workspace_file_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE action text;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF NOT ((session_user='nova_meta_web' AND TG_OP IN ('INSERT','UPDATE'))
            OR (session_user='nova_meta_task' AND TG_OP='INSERT'
                AND TG_TABLE_NAME='config_workspace_entries'))
       OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME NOT IN
            ('config_workspace_entries','config_workspace_file_versions') THEN
        RAISE EXCEPTION 'Invalid workspace audit source' USING ERRCODE='42501';
    END IF;
    action:=CASE WHEN NEW.is_deleted THEN 'DELETE' WHEN TG_OP='INSERT' THEN 'CREATE'
        ELSE 'UPDATE' END;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,NEW.id,action,'SUCCESS','ALLOW',
        TG_TABLE_NAME,jsonb_build_object('id',NEW.id),NEW.metadata_revision);
    RETURN NULL;
END
$body$;

GRANT EXECUTE ON FUNCTION nova_meta.workspace_permission(text),
    nova_meta.workspace_namespace_permission(text) TO nova_meta_task;
ALTER POLICY workspace_read ON nova_meta.config_workspaces
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook,nova_meta_task;
ALTER POLICY workspace_share_read ON nova_meta.config_workspace_shares
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook,nova_meta_task;
ALTER POLICY workspace_entry_read ON nova_meta.config_workspace_entries
    TO nova_meta_web,nova_meta_query,nova_meta_agent,nova_meta_notebook,nova_meta_task;
ALTER POLICY workspace_entry_insert ON nova_meta.config_workspace_entries
    TO nova_meta_web,nova_meta_task;
GRANT SELECT ON nova_meta.config_workspaces,nova_meta.config_workspace_shares,
    nova_meta.config_workspace_entries TO nova_meta_task;
GRANT INSERT(entry_type,etag,id,name,object_key,parent_path,size_bytes,user_name)
    ON nova_meta.config_workspace_entries TO nova_meta_task;
