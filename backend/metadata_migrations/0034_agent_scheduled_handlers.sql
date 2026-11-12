-- Scheduled learning and Quality handlers retain the verified task owner and role.


CREATE OR REPLACE FUNCTION nova_meta.agent_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT session_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
        AND nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;

CREATE OR REPLACE FUNCTION nova_meta.agent_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Agent import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Agent metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;

CREATE OR REPLACE FUNCTION nova_meta.agent_record_head(tbl text, record_id text,
    OUT head bigint, OUT foreign_scope boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation','nova_meta_task')
       OR NOT nova_meta.agent_actor_bound()
       OR tbl NOT IN ('config_agent_quality_cases','config_agent_quality_runs',
           'config_agent_quality_monitoring','config_agent_improvement_proposals',
           'config_agent_harness_lessons') THEN
        RAISE EXCEPTION 'Invalid revision head request' USING ERRCODE='42501';
    END IF;
    EXECUTE format('SELECT max(revision), bool_or(principal IS DISTINCT FROM $2 OR active_role IS DISTINCT FROM $3) FROM nova_meta.%I WHERE id=$1', tbl)
        INTO head, foreign_scope
        USING record_id, current_setting('nova.principal',true), current_setting('nova.active_role',true);
END
$body$;

GRANT EXECUTE ON FUNCTION nova_meta.agent_actor_bound(),nova_meta.agent_is_principal(text),
    nova_meta.agent_in_scope(text,text),nova_meta.agent_record_head(text,text),
    nova_meta.agent_memory_head(text,text) TO nova_meta_task;
GRANT SELECT ON nova_meta.config_agents,nova_meta.config_agent_roles,
    nova_meta.config_agent_versions,nova_meta.config_agent_release_manifests TO nova_meta_task;
CREATE POLICY task_agent_read ON nova_meta.config_agents FOR SELECT TO nova_meta_task
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agent_roles r WHERE r.agent_id=config_agents.agent_id
            AND r.owner_name=config_agents.owner_name
            AND r.role_name=current_setting('nova.active_role',true))));
CREATE POLICY task_role_read ON nova_meta.config_agent_roles FOR SELECT TO nova_meta_task
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound()
        AND role_name=current_setting('nova.active_role',true)));
CREATE POLICY task_version_read ON nova_meta.config_agent_versions FOR SELECT TO nova_meta_task
    USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY task_manifest_read ON nova_meta.config_agent_release_manifests FOR SELECT TO nova_meta_task
    USING (nova_meta.agent_is_principal(owner_name));

DO $quality$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_quality_cases','config_agent_quality_runs',
        'config_agent_quality_monitoring','config_agent_improvement_proposals'] LOOP
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO nova_meta_task',tbl);
        EXECUTE format('CREATE POLICY task_read ON nova_meta.%I FOR SELECT TO nova_meta_task USING (nova_meta.agent_in_scope(principal,active_role))',tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['config_agent_quality_runs','config_agent_improvement_proposals'] LOOP
        EXECUTE format('GRANT INSERT(id,revision,operation_id,agent_id,principal,active_role,security_context_version,payload) ON nova_meta.%I TO nova_meta_task',tbl);
        EXECUTE format('CREATE POLICY task_insert ON nova_meta.%I FOR INSERT TO nova_meta_task WITH CHECK (nova_meta.agent_in_scope(principal,active_role))',tbl);
        EXECUTE format('CREATE POLICY task_head ON nova_meta.%I FOR SELECT TO nova_meta_owner USING (session_user=''nova_meta_task'' AND nova_meta.agent_actor_bound())',tbl);
    END LOOP;
END
$quality$;
GRANT SELECT ON nova_meta.config_agent_memories,nova_meta.config_agent_memory_revisions,
    nova_meta.config_agent_memory_evidence TO nova_meta_task;
GRANT INSERT(memory_id,user_name,agent_id,role_name,fact_key,fact,source_quote,source_thread_id,revision),
    UPDATE(fact_key,fact,source_quote,source_thread_id,revision)
    ON nova_meta.config_agent_memories TO nova_meta_task;
GRANT INSERT(memory_id,revision,operation_id,user_name,agent_id,role_name,payload)
    ON nova_meta.config_agent_memory_revisions TO nova_meta_task;
GRANT INSERT(memory_id,evidence_id,user_name,agent_id,role_name,payload)
    ON nova_meta.config_agent_memory_evidence TO nova_meta_task;
DO $memory$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_memories','config_agent_memory_revisions',
        'config_agent_memory_evidence'] LOOP
        EXECUTE format('CREATE POLICY task_read ON nova_meta.%I FOR SELECT TO nova_meta_task USING (nova_meta.agent_in_scope(user_name,role_name))',tbl);
        EXECUTE format('CREATE POLICY task_insert ON nova_meta.%I FOR INSERT TO nova_meta_task WITH CHECK (nova_meta.agent_in_scope(user_name,role_name))',tbl);
    END LOOP;
END
$memory$;
CREATE POLICY task_memory_update ON nova_meta.config_agent_memories FOR UPDATE TO nova_meta_task
    USING (nova_meta.agent_in_scope(user_name,role_name))
    WITH CHECK (nova_meta.agent_in_scope(user_name,role_name));

CREATE POLICY task_thread_read ON nova_meta.config_assistant_threads FOR SELECT TO nova_meta_task
    USING (nova_meta.apb_actor_is(user_name) AND current_setting('nova.principal_type',true)='USER');
CREATE POLICY task_message_read ON nova_meta.config_assistant_messages FOR SELECT TO nova_meta_task
    USING (nova_meta.apb_actor_is(user_name) AND current_setting('nova.principal_type',true)='USER'
        AND security_context->>'active_role'=current_setting('nova.active_role',true)
        AND security_context->>'security_context_version'=current_setting('nova.security_context_version',true));
CREATE POLICY task_message_learning ON nova_meta.config_assistant_messages FOR UPDATE TO nova_meta_task
    USING (nova_meta.apb_actor_is(user_name) AND current_setting('nova.principal_type',true)='USER'
        AND role='user' AND learning_state='pending'
        AND security_context->>'active_role'=current_setting('nova.active_role',true)
        AND security_context->>'security_context_version'=current_setting('nova.security_context_version',true))
    WITH CHECK (nova_meta.apb_actor_is(user_name) AND learning_state IN ('pending','complete'));
