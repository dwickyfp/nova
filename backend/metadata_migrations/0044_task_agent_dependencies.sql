-- Scheduled learning revalidates the same Agent dependencies as a web turn.
GRANT SELECT ON nova_meta.config_custom_tools TO nova_meta_task;
ALTER POLICY config_custom_tools_read ON nova_meta.config_custom_tools
    TO nova_meta_web,nova_meta_agent,nova_meta_automation,nova_meta_task;

-- A shared Agent may pin a release owned by another user. Only its selected
-- manifest is visible through the caller's existing Agent role grant.
ALTER POLICY config_agent_release_manifests_read ON nova_meta.config_agent_release_manifests
    TO nova_meta_web,nova_meta_agent,nova_meta_automation,nova_meta_task;
