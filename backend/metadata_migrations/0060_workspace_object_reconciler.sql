-- Workspace object reconciliation (NDV-STORE-009). Every file save writes a new
-- object under <base>/<namespace>/.objects/ before its metadata commits. A save
-- that fails is cleaned up at once, but a crash between the write and the commit,
-- a failed cleanup or an unknown commit outcome leaves an object that nothing may
-- reference. The maintenance worker, as the SERVICE principal
-- nova-workspace-objects, asks which of a batch of such keys a live entry or
-- version still references, and removes the others only after a retention far
-- longer than any write can stay uncommitted.
--
-- The reconciler reads object keys and nothing else: no names, paths, owners or
-- contents, and it changes no metadata.
CREATE FUNCTION nova_meta.workspace_object_reconciler() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
       AND session_user='nova_meta_maintenance'
       AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
       AND current_setting('nova.principal_type',true)='SERVICE'
       AND current_setting('nova.principal',true)='nova-workspace-objects'
$body$;
REVOKE ALL ON FUNCTION nova_meta.workspace_object_reconciler() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.workspace_object_reconciler() TO nova_meta_maintenance;

GRANT SELECT (object_key, is_deleted)
    ON nova_meta.config_workspace_entries TO nova_meta_maintenance;
GRANT SELECT (object_key, is_deleted, state)
    ON nova_meta.config_workspace_file_versions TO nova_meta_maintenance;

CREATE POLICY workspace_entry_reconciler_read ON nova_meta.config_workspace_entries
    FOR SELECT TO nova_meta_maintenance USING (nova_meta.workspace_object_reconciler());
CREATE POLICY workspace_version_reconciler_read ON nova_meta.config_workspace_file_versions
    FOR SELECT TO nova_meta_maintenance USING (nova_meta.workspace_object_reconciler());

-- The reconciler looks keys up in batches.
CREATE INDEX config_workspace_entries_object_key
    ON nova_meta.config_workspace_entries (object_key) WHERE object_key IS NOT NULL;
CREATE INDEX config_workspace_file_versions_object_key
    ON nova_meta.config_workspace_file_versions (object_key);
