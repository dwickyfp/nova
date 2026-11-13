-- Preserve imported presentation definitions. Runtime authors create BI pointers;
-- they have no INSERT/UPDATE grants on these historical payload columns.
ALTER TABLE nova_meta.config_studio_artifacts
    ADD COLUMN chart_spec jsonb CHECK (octet_length(chart_spec::text)<=1048576),
    ADD COLUMN viz_spec jsonb CHECK (octet_length(viz_spec::text)<=1048576);

-- Only fixed SECURITY DEFINER projections can use this owner policy. The caller
-- cannot assume nova_meta_owner or SELECT another owner's artifacts directly.
-- The function below limits results to the granted dashboard's stored members.
CREATE POLICY config_studio_artifacts_shared_projection ON nova_meta.config_studio_artifacts
FOR SELECT TO nova_meta_owner USING (
    session_user='nova_meta_web' AND nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_studio_shares s WHERE s.object_type='dashboard'
            AND s.owner_name=config_studio_artifacts.owner_name
            AND ((s.target_type='user' AND s.target_name=current_setting('nova.principal',true))
                OR (s.target_type='role' AND s.target_name=current_setting('nova.active_role',true)))
    )
);

CREATE FUNCTION nova_meta.shared_dashboard(requested_dashboard text, requested_owner text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE dashboard record; charts jsonb;
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.agent_actor_bound()
       OR current_setting('nova.principal_type',true) IS DISTINCT FROM 'USER' THEN
        RAISE EXCEPTION 'Shared dashboard is unavailable' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM nova_meta.config_studio_shares s
        WHERE s.object_type='dashboard' AND s.object_id=requested_dashboard
            AND s.owner_name=requested_owner
            AND ((s.target_type='user' AND s.target_name=current_setting('nova.principal',true))
                OR (s.target_type='role' AND s.target_name=current_setting('nova.active_role',true)))) THEN
        RETURN NULL;
    END IF;
    SELECT a.artifact_id,a.title,a.viz_spec INTO dashboard
        FROM nova_meta.config_studio_artifacts a
        WHERE a.artifact_id=requested_dashboard AND a.owner_name=requested_owner
            AND a.artifact_type='dashboard' AND a.bi_object_id IS NULL;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT coalesce(jsonb_agg(jsonb_build_object(
        'artifact_id',a.artifact_id,'title',a.title,'artifact_type','chart',
        'sql_text',a.sql_text,'database_name',a.database_name,'schema_name',a.schema_name,
        'chart_spec',a.chart_spec-'data'-'datasets',
        'viz_spec',a.viz_spec) ORDER BY a.artifact_id),'[]'::jsonb)
    INTO charts FROM nova_meta.config_studio_artifacts a
    WHERE a.owner_name=requested_owner AND a.artifact_type IN ('chart','table')
        AND a.artifact_id IN (
            SELECT zone->>'artifact_id' FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(dashboard.viz_spec->'zones')='array'
                    THEN dashboard.viz_spec->'zones' ELSE '[]'::jsonb END) zone
            WHERE zone->>'kind'='sheet'
        );
    RETURN jsonb_build_object('artifact_id',dashboard.artifact_id,'title',dashboard.title,
        'artifact_type','dashboard','viz_spec',dashboard.viz_spec,
        'charts',charts);
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.shared_dashboard(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.shared_dashboard(text,text) TO nova_meta_web;

-- A maintenance scan returns owner names only, for the explicit conversion CLI.
CREATE FUNCTION nova_meta.artifact_conversion_scanner() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT session_user='nova_meta_maintenance' AND nova_meta.actor_context_valid()
        AND current_setting('nova.service_identity',true)=session_user
        AND current_setting('nova.principal_type',true)='SERVICE'
        AND current_setting('nova.principal',true)='nova-artifact-converter'
$body$;
REVOKE ALL ON FUNCTION nova_meta.artifact_conversion_scanner() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.artifact_conversion_scanner() TO nova_meta_maintenance;
CREATE POLICY config_studio_artifacts_conversion_scan ON nova_meta.config_studio_artifacts
    FOR SELECT TO nova_meta_owner USING (nova_meta.artifact_conversion_scanner());
CREATE POLICY config_studio_dashboards_conversion_scan ON nova_meta.config_studio_dashboards
    FOR SELECT TO nova_meta_owner USING (nova_meta.artifact_conversion_scanner());
CREATE FUNCTION nova_meta.legacy_artifact_owners() RETURNS TABLE(owner_name text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF NOT nova_meta.artifact_conversion_scanner() THEN
        RAISE EXCEPTION 'Artifact discovery is unavailable' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT a.owner_name::text FROM nova_meta.config_studio_artifacts a
        WHERE a.bi_object_id IS NULL
        UNION SELECT d.owner_name::text FROM nova_meta.config_studio_dashboards d
        ORDER BY 1;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.legacy_artifact_owners() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.legacy_artifact_owners() TO nova_meta_maintenance;
