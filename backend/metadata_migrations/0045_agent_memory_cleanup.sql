-- The owner-scoped memory cleanup function executes as nova_meta_owner while
-- retaining the web login and bound caller. It must use the same freeze gate.
CREATE OR REPLACE FUNCTION nova_meta.agent_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Agent import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF (current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
            AND TG_OP IN ('INSERT','UPDATE','DELETE'))
        OR (current_user='nova_meta_owner' AND session_user='nova_meta_web'
            AND TG_TABLE_NAME='config_agent_memories' AND TG_OP='DELETE'
            AND nova_meta.agent_actor_bound()) THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Agent metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
