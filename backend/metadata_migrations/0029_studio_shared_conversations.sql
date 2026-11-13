-- Sharing exposes a bounded conversation projection. It never grants SELECT
-- on the owner's messages, attachments, instructions, or recorded result rows.
CREATE FUNCTION nova_meta.strip_shared_results(value jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $body$
DECLARE result jsonb; item record;
BEGIN
    IF jsonb_typeof(value)='object' THEN
        result:='{}'::jsonb;
        FOR item IN SELECT key,val FROM jsonb_each(value) AS entry(key,val) LOOP
            IF item.key NOT IN ('rows','data','values','table','result','results','preview') THEN
                result:=result||jsonb_build_object(item.key,nova_meta.strip_shared_results(item.val));
            END IF;
        END LOOP;
    ELSIF jsonb_typeof(value)='array' THEN
        result:='[]'::jsonb;
        FOR item IN SELECT val FROM jsonb_array_elements(value) AS entry(val) LOOP
            result:=result||jsonb_build_array(nova_meta.strip_shared_results(item.val));
        END LOOP;
    ELSE
        result:=value;
    END IF;
    RETURN result;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.strip_shared_results(jsonb) FROM PUBLIC;

CREATE POLICY config_studio_shares_projection ON nova_meta.config_studio_shares
FOR SELECT TO nova_meta_owner USING (
    session_user='nova_meta_web' AND nova_meta.agent_actor_bound() AND (
        (target_type='user' AND target_name=current_setting('nova.principal',true)) OR
        (target_type='role' AND target_name=current_setting('nova.active_role',true))
    )
);
CREATE POLICY config_assistant_threads_shared_projection ON nova_meta.config_assistant_threads
FOR SELECT TO nova_meta_owner USING (
    session_user='nova_meta_web' AND nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_studio_shares s WHERE s.object_type='thread'
            AND s.object_id=thread_id AND s.owner_name=user_name
            AND ((s.target_type='user' AND s.target_name=current_setting('nova.principal',true))
                OR (s.target_type='role' AND s.target_name=current_setting('nova.active_role',true)))
    )
);
CREATE POLICY config_assistant_messages_shared_projection ON nova_meta.config_assistant_messages
FOR SELECT TO nova_meta_owner USING (
    session_user='nova_meta_web' AND nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_studio_shares s WHERE s.object_type='thread'
            AND s.object_id=thread_id AND s.owner_name=user_name
            AND ((s.target_type='user' AND s.target_name=current_setting('nova.principal',true))
                OR (s.target_type='role' AND s.target_name=current_setting('nova.active_role',true)))
    )
);

CREATE FUNCTION nova_meta.shared_conversation(requested_thread text, requested_owner text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE thread record; message record; step jsonb; trace jsonb; kept jsonb;
        messages jsonb:='[]'::jsonb; steps jsonb; private_input boolean:=false;
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.agent_actor_bound()
       OR current_setting('nova.principal_type',true) IS DISTINCT FROM 'USER' THEN
        RAISE EXCEPTION 'Shared conversation is unavailable' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM nova_meta.config_studio_shares s
        WHERE s.object_type='thread' AND s.object_id=requested_thread
            AND s.owner_name=requested_owner
            AND ((s.target_type='user' AND s.target_name=current_setting('nova.principal',true))
                OR (s.target_type='role' AND s.target_name=current_setting('nova.active_role',true)))) THEN
        RETURN NULL;
    END IF;
    SELECT t.thread_id,t.title,t.agent_id INTO thread FROM nova_meta.config_assistant_threads t
        WHERE t.thread_id=requested_thread AND t.user_name=requested_owner;
    IF NOT FOUND THEN RETURN NULL; END IF;
    FOR message IN SELECT m.message_id,m.role,m.content,m.steps
        FROM nova_meta.config_assistant_messages m
        WHERE m.thread_id=requested_thread AND m.user_name=requested_owner
        ORDER BY m.seq,m.message_id LOOP
        IF message.steps IS NOT NULL AND jsonb_typeof(message.steps)<>'array' THEN
            RAISE EXCEPTION 'Shared conversation requires reconciliation' USING ERRCODE='23514';
        END IF;
        steps:='[]'::jsonb;
        FOR step IN SELECT val FROM jsonb_array_elements(coalesce(message.steps,'[]'::jsonb)) AS entry(val) LOOP
            IF jsonb_typeof(step)<>'object' THEN CONTINUE; END IF;
            IF step->>'kind' IN ('ui_action','report_context') OR
                (step->>'kind'='tool' AND step->>'name'='smart_report') THEN
                private_input:=true;
            END IF;
            IF step->>'kind' IN ('ui','ui_action','smart_report','report_context') THEN CONTINUE; END IF;
            kept:=nova_meta.strip_shared_results(step);
            IF step->>'kind'='tool' AND step->>'name'='semantic_query' THEN
                trace:=kept->'trace_detail';
                kept:=jsonb_set(kept,'{trace_detail}',coalesce((SELECT jsonb_object_agg(key,val)
                    FROM jsonb_each(CASE WHEN jsonb_typeof(trace)='object' THEN trace ELSE '{}'::jsonb END) AS entry(key,val)
                    WHERE key IN ('question','semantic_view','semantic_plan','metrics')),'{}'::jsonb));
            END IF;
            steps:=steps||jsonb_build_array(kept);
        END LOOP;
        IF message.role NOT IN ('user','assistant') THEN CONTINUE; END IF;
        messages:=messages||jsonb_build_array(jsonb_build_object(
            'message_id',message.message_id,'role',message.role,
            'content',CASE WHEN private_input THEN
                'This part uses private interactive inputs. Continue in your own conversation.'
                ELSE coalesce(message.content,'') END,
            'steps',CASE WHEN private_input THEN '[]'::jsonb ELSE steps END));
    END LOOP;
    RETURN jsonb_build_object('thread_id',thread.thread_id,'title',thread.title,
        'agent_id',thread.agent_id,'messages',messages);
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.shared_conversation(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.shared_conversation(text,text) TO nova_meta_web;
