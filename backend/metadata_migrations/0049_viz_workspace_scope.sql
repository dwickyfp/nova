-- Personal workspaces are virtual in the existing Workspace owner. Keep the
-- named-workspace foreign key while resolving personal membership by owner.
ALTER TABLE nova_meta.viz_resources
    DROP CONSTRAINT viz_resources_workspace_id_fkey,
    ADD COLUMN shared_workspace_id varchar(64) GENERATED ALWAYS AS (
        CASE WHEN workspace_id='personal' THEN NULL ELSE workspace_id END
    ) STORED REFERENCES nova_meta.config_workspaces(id),
    ADD COLUMN workspace_namespace text GENERATED ALWAYS AS (
        CASE WHEN workspace_id='personal' THEN owner_name ELSE '~ws/'||workspace_id END
    ) STORED;

CREATE INDEX viz_resources_personal ON nova_meta.viz_resources(owner_name,kind,updated_at DESC)
    WHERE workspace_id='personal' AND deleted_at IS NULL;

CREATE OR REPLACE FUNCTION nova_meta.viz_can(identity uuid, action text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE resource nova_meta.viz_resources; actions text[];
BEGIN
    IF NOT nova_meta.viz_actor_valid() OR action NOT IN
        ('view','interact','comment','edit','publish','manage','export') THEN RETURN false; END IF;
    SELECT * INTO resource FROM nova_meta.viz_resources WHERE resource_id=identity;
    IF NOT FOUND OR resource.deleted_at IS NOT NULL
       OR nova_meta.workspace_namespace_permission(resource.workspace_namespace) IS NULL
    THEN RETURN false; END IF;
    IF resource.owner_name=current_setting('nova.principal',true) THEN RETURN true; END IF;
    SELECT coalesce(array_agg(permission),'{}'::text[]) INTO actions
        FROM nova_meta.viz_acl_grants WHERE resource_id=identity
        AND (expires_at IS NULL OR expires_at>statement_timestamp())
        AND ((subject_type='user' AND subject_id=current_setting('nova.principal',true))
            OR (subject_type='role' AND subject_id=current_setting('nova.active_role',true)));
    IF 'manage'=ANY(actions) AND action<>'export' THEN RETURN true; END IF;
    IF action='publish' THEN RETURN 'publish'=ANY(actions) AND 'edit'=ANY(actions); END IF;
    IF action=ANY(actions) THEN RETURN true; END IF;
    IF 'edit'=ANY(actions) AND action IN ('view','interact','comment') THEN RETURN true; END IF;
    RETURN action='view' AND actions && ARRAY['interact','comment'];
END
$body$;

ALTER POLICY viz_resource_read ON nova_meta.viz_resources USING (
    (nova_meta.viz_actor_valid() AND owner_name=current_setting('nova.principal',true)
     AND deleted_at IS NULL
     AND nova_meta.workspace_namespace_permission(workspace_namespace) IS NOT NULL)
    OR nova_meta.viz_can(resource_id,'view'));

ALTER POLICY viz_resource_create ON nova_meta.viz_resources WITH CHECK (
    nova_meta.viz_actor_valid() AND owner_name=current_setting('nova.principal',true)
    AND nova_meta.workspace_namespace_permission(workspace_namespace) IN ('owner','edit'));
