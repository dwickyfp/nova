-- Current provider invocation owners use one restricted credential projection.
-- This does not grant their USER actors, SQL clients or public APIs secret reads.
CREATE FUNCTION nova_meta.ai_runtime_actor() RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid()
       AND session_user IN ('nova_meta_web','nova_meta_agent','nova_meta_query',
           'nova_meta_proxy','nova_meta_task','nova_meta_ingestion','nova_meta_ml',
           'nova_meta_automation','nova_meta_maintenance')
       AND current_setting('nova.principal_type', true) = 'SERVICE'
       AND current_setting('nova.principal', true) = 'nova-ai'
       AND current_setting('nova.active_role', true) = 'NOVA_WORKER'
$body$;
REVOKE ALL ON FUNCTION nova_meta.ai_runtime_actor() FROM PUBLIC;

CREATE POLICY ai_runtime_provider_projection ON nova_meta.config_ai_providers
    FOR SELECT TO nova_meta_owner USING (
        nova_meta.ai_runtime_actor() AND is_active AND NOT is_deleted
    );
CREATE POLICY ai_runtime_credential_projection ON nova_meta.config_ai_provider_credentials
    FOR SELECT TO nova_meta_owner USING (nova_meta.ai_runtime_actor());

CREATE FUNCTION nova_meta.ai_runtime_credential(provider_identity text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $body$
DECLARE value text;
BEGIN
    IF nova_meta.ai_runtime_actor() IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Provider runtime identity is not authorized' USING ERRCODE = '42501';
    END IF;
    SELECT c.ciphertext INTO value
    FROM nova_meta.config_ai_providers p
    JOIN nova_meta.config_ai_provider_credentials c
        ON c.provider_id=p.id AND c.metadata_revision=p.metadata_revision
    WHERE p.id=provider_identity AND p.is_active AND NOT p.is_deleted;
    RETURN value;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.ai_runtime_credential(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.ai_runtime_credential(text)
    TO nova_meta_web, nova_meta_agent, nova_meta_query, nova_meta_proxy,
       nova_meta_task, nova_meta_ingestion, nova_meta_ml, nova_meta_automation,
       nova_meta_maintenance;
