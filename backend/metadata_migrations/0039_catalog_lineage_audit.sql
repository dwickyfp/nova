-- Maintenance needs successful statement text for lineage, without broad audit
-- access or the web administrator login. The owner policy is reachable only
-- inside this fixed projection with the catalog refresher's bound identity.
CREATE POLICY audit_catalog_lineage_projection ON nova_meta.audit_log
FOR SELECT TO nova_meta_owner USING (
    session_user='nova_meta_maintenance' AND nova_meta.catalog_actor_bound()
    AND current_setting('nova.principal_type',true)='SERVICE'
    AND current_setting('nova.principal',true)='catalog-refresher'
    AND current_setting('nova.active_role',true)='SECURITYADMIN'
    AND event_type='query' AND status='SUCCESS'
);

CREATE FUNCTION nova_meta.catalog_lineage_statements(since_time timestamptz, row_limit integer)
RETURNS TABLE(query_id text,event_time timestamptz,database_name text,sql_text text)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user<>'nova_meta_maintenance' OR NOT nova_meta.catalog_actor_bound()
       OR current_setting('nova.principal_type',true) IS DISTINCT FROM 'SERVICE'
       OR current_setting('nova.principal',true) IS DISTINCT FROM 'catalog-refresher'
       OR current_setting('nova.active_role',true) IS DISTINCT FROM 'SECURITYADMIN' THEN
        RAISE EXCEPTION 'Lineage audit projection is unavailable' USING ERRCODE='42501';
    END IF;
    IF since_time IS NULL OR row_limit IS NULL OR row_limit<1 OR row_limit>5000 THEN
        RAISE EXCEPTION 'Invalid lineage audit bounds' USING ERRCODE='22023';
    END IF;
    RETURN QUERY SELECT a.query_id::text,a.event_time,a.database_name::text,a.sql_text
        FROM nova_meta.audit_log a
        WHERE a.event_type='query' AND a.status='SUCCESS' AND a.event_time>=since_time
            AND a.sql_text IS NOT NULL
            AND (strpos(upper(a.sql_text),'INSERT')>0 OR strpos(upper(a.sql_text),'CREATE')>0)
        ORDER BY a.event_time,a.record_id LIMIT row_limit;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.catalog_lineage_statements(timestamptz,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.catalog_lineage_statements(timestamptz,integer)
    TO nova_meta_maintenance;
