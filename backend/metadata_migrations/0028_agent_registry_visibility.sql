-- A retained role grant does not publish a private agent or its resources.
ALTER POLICY config_agents_read ON nova_meta.config_agents USING (
    nova_meta.agent_is_principal(owner_name) OR (
        visibility='shared' AND nova_meta.agent_actor_bound() AND EXISTS (
            SELECT 1 FROM nova_meta.config_agent_roles r
            WHERE r.agent_id=config_agents.agent_id AND r.owner_name=config_agents.owner_name
                AND r.role_name=current_setting('nova.active_role',true)
        )
    )
);

ALTER POLICY config_custom_tools_read ON nova_meta.config_custom_tools USING (
    nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agents a WHERE a.owner_name=config_custom_tools.owner_name
            AND (coalesce(a.default_tools,'[]'::jsonb) ? config_custom_tools.name
                OR coalesce(a.default_tools,'[]'::jsonb) ? ('custom:'||config_custom_tools.name))
    ))
);

-- Platform connectors are administered under the human caller's identity.
-- Reading registrations never borrows the __nova__ service identity.
CREATE FUNCTION nova_meta.agent_platform_admin() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.agent_actor_bound()
        AND current_setting('nova.principal_type',true)='USER'
        AND current_setting('nova.active_role',true)='ACCOUNTADMIN'
$body$;
REVOKE ALL ON FUNCTION nova_meta.agent_platform_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.agent_platform_admin()
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

DO $policies$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_mcp_servers','config_tools'] LOOP
        EXECUTE format(
            'CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (owner_name=''__nova__'' AND nova_meta.agent_actor_bound())',
            tbl||'_platform_read',tbl);
        EXECUTE format(
            'CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_web WITH CHECK (owner_name=''__nova__'' AND nova_meta.agent_platform_admin())',
            tbl||'_platform_insert',tbl);
        EXECUTE format(
            'CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO nova_meta_web USING (owner_name=''__nova__'' AND nova_meta.agent_platform_admin()) WITH CHECK (owner_name=''__nova__'' AND nova_meta.agent_platform_admin())',
            tbl||'_platform_update',tbl);
        EXECUTE format(
            'CREATE POLICY %I ON nova_meta.%I FOR DELETE TO nova_meta_web USING (owner_name=''__nova__'' AND nova_meta.agent_platform_admin())',
            tbl||'_platform_delete',tbl);
    END LOOP;
END
$policies$;
