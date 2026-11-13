-- Deleted projections retain their revision chain. Relearning appends above
-- that chain in the same owner/agent/role scope, never reuses revision 1.
DO $memory_heads$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_memories','config_agent_memory_revisions',
                               'config_agents','config_agent_roles'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_owner USING (nova_meta.agent_actor_bound())',
            tbl||'_memory_projection',tbl);
    END LOOP;
END
$memory_heads$;

CREATE FUNCTION nova_meta.agent_memory_head(requested_id text, requested_agent text,
    OUT head bigint, OUT foreign_scope boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF NOT nova_meta.agent_actor_bound() THEN
        RAISE EXCEPTION 'Memory unavailable' USING ERRCODE='42501';
    END IF;
    SELECT max(r.revision),coalesce(bool_or(r.user_name IS DISTINCT FROM current_setting('nova.principal',true)
        OR r.role_name IS DISTINCT FROM current_setting('nova.active_role',true)
        OR r.agent_id IS DISTINCT FROM requested_agent),false)
    INTO head,foreign_scope FROM nova_meta.config_agent_memory_revisions r WHERE r.memory_id=requested_id;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.agent_memory_head(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.agent_memory_head(text,text)
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

GRANT DELETE ON nova_meta.config_agent_memories TO nova_meta_web,nova_meta_agent,nova_meta_automation;
CREATE POLICY config_agent_memories_delete ON nova_meta.config_agent_memories FOR DELETE
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_in_scope(user_name,role_name));
CREATE POLICY config_agent_memories_owner_delete ON nova_meta.config_agent_memories FOR DELETE
    TO nova_meta_owner USING (session_user='nova_meta_web' AND nova_meta.agent_is_principal(user_name));

CREATE FUNCTION nova_meta.delete_agent_memories(requested_agent text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE removed bigint;
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.agent_actor_bound() THEN
        RAISE EXCEPTION 'Memory removal unavailable' USING ERRCODE='42501';
    END IF;
    DELETE FROM nova_meta.config_agent_memories m WHERE m.agent_id=requested_agent
        AND m.user_name=current_setting('nova.principal',true);
    GET DIAGNOSTICS removed=ROW_COUNT;
    RETURN removed;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.delete_agent_memories(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.delete_agent_memories(text) TO nova_meta_web;

CREATE FUNCTION nova_meta.agent_memory_agent_visible(requested_agent text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
    SELECT nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agents a WHERE a.agent_id=requested_agent
        AND (a.owner_name=current_setting('nova.principal',true) OR (a.visibility='shared' AND EXISTS (
            SELECT 1 FROM nova_meta.config_agent_roles r WHERE r.agent_id=a.agent_id
                AND r.owner_name=a.owner_name AND r.role_name=current_setting('nova.active_role',true)))))
$body$;
REVOKE ALL ON FUNCTION nova_meta.agent_memory_agent_visible(text) FROM PUBLIC;

CREATE FUNCTION nova_meta.shared_agent_memories(requested_agent text)
RETURNS TABLE(memory_id varchar,agent_id varchar,payload text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE views jsonb;
BEGIN
    IF NOT nova_meta.agent_actor_bound()
       OR current_setting('nova.principal_type',true) IS DISTINCT FROM 'USER' THEN
        RAISE EXCEPTION 'Shared memory unavailable' USING ERRCODE='42501';
    END IF;
    IF NOT nova_meta.agent_memory_agent_visible(requested_agent) THEN RETURN; END IF;
    SELECT coalesce(a.semantic_view_ids,a.semantic_model_ids,'[]'::jsonb) INTO views
        FROM nova_meta.config_agents a WHERE a.agent_id=requested_agent;
    RETURN QUERY
    SELECT latest.memory_id,latest.agent_id,
        (latest.payload::jsonb - ARRAY['alternatives','evidence_ids','review_note','reviewed_by',
          'review_operation_id','review_request_hash'])::text
    FROM (
        SELECT r.*,row_number() OVER (PARTITION BY r.memory_id ORDER BY r.revision DESC) AS rn,
            count(*) OVER (PARTITION BY r.memory_id,r.revision) AS branches
        FROM nova_meta.config_agent_memory_revisions r
    ) latest
    WHERE latest.rn=1 AND latest.branches=1
        AND (latest.agent_id=requested_agent OR views ? (latest.payload::jsonb #>> '{semantic,view_id}'))
        AND (latest.user_name<>current_setting('nova.principal',true) OR latest.agent_id<>requested_agent)
        AND latest.payload::jsonb->>'visibility'='DOMAIN' AND latest.payload::jsonb->>'state'='VERIFIED'
        AND nova_meta.agent_memory_agent_visible(latest.agent_id)
        AND EXISTS (SELECT 1 FROM nova_meta.config_agent_memories m WHERE m.memory_id=latest.memory_id
            AND m.user_name=latest.user_name AND m.agent_id=latest.agent_id AND m.role_name=latest.role_name)
    ORDER BY latest.memory_id LIMIT 100;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.shared_agent_memories(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.shared_agent_memories(text) TO nova_meta_web,nova_meta_agent,nova_meta_automation;

CREATE OR REPLACE FUNCTION nova_meta.agent_knowledge_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE head bigint; foreign_scope boolean;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' AND NEW.provenance IS DISTINCT FROM OLD.provenance THEN
        RAISE EXCEPTION 'Provenance is fixed' USING ERRCODE='23514';
    END IF;
    IF TG_TABLE_NAME='config_agent_memories' THEN
        IF btrim(NEW.fact_key)='' OR btrim(NEW.fact)='' OR NEW.revision IS NULL THEN
            RAISE EXCEPTION 'Invalid memory' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            SELECT h.head,h.foreign_scope INTO head,foreign_scope
                FROM nova_meta.agent_memory_head(NEW.memory_id,NEW.agent_id) h;
            IF foreign_scope OR NEW.revision IS DISTINCT FROM head THEN
                RAISE EXCEPTION 'Memory history changed' USING ERRCODE='40001';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.memory_id,NEW.user_name,NEW.agent_id,NEW.role_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.memory_id,OLD.user_name,OLD.agent_id,OLD.role_name,OLD.created_at)
              OR NEW.revision<=coalesce(OLD.revision,0) THEN
            RAISE EXCEPTION 'Invalid memory change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_memory_revisions' THEN
        IF json_typeof(NEW.payload::json)<>'object' OR NEW.operation_id !~ '^[0-9a-f]{32}$' THEN
            RAISE EXCEPTION 'Invalid memory revision' USING ERRCODE='23514';
        END IF;
        SELECT h.head,h.foreign_scope INTO head,foreign_scope
            FROM nova_meta.agent_memory_head(NEW.memory_id,NEW.agent_id) h;
        IF foreign_scope THEN
            RAISE EXCEPTION 'Memory unavailable' USING ERRCODE='42501';
        END IF;
        IF NEW.revision<>coalesce(head,0)+1 THEN
            RAISE EXCEPTION 'Memory revision changed' USING ERRCODE='40001';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_agent_memory_evidence' THEN
        IF json_typeof(NEW.payload::json)<>'object' OR NOT EXISTS (
                SELECT 1 FROM nova_meta.config_agent_memories m WHERE m.memory_id=NEW.memory_id
                    AND m.user_name=NEW.user_name AND m.agent_id=NEW.agent_id
                    AND m.role_name=NEW.role_name) THEN
            RAISE EXCEPTION 'Invalid memory evidence' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSE
        -- Quality revision stores.
        IF json_typeof(NEW.payload::json)<>'object' OR NEW.operation_id !~ '^[0-9a-f]{32}$' THEN
            RAISE EXCEPTION 'Invalid quality record' USING ERRCODE='23514';
        END IF;
        NEW.security_context_version:=current_setting('nova.security_context_version',true)::bigint;
        SELECT h.head,h.foreign_scope INTO head,foreign_scope
            FROM nova_meta.agent_record_head(TG_TABLE_NAME,NEW.id) h;
        IF foreign_scope THEN
            RAISE EXCEPTION 'Quality record unavailable' USING ERRCODE='42501';
        END IF;
        IF NEW.revision<>coalesce(head,0)+1 THEN
            RAISE EXCEPTION 'Quality record changed' USING ERRCODE='40001';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
