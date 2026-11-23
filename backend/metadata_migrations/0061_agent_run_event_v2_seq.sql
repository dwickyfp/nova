-- Studio protocol v2 frames carry the run sequence as "seq" beside "run_id";
-- v1 frames carry "sequence". The run-event guard from revision 0021 read only
-- "sequence", so it refused every v2 event batch ("Invalid run event frame") and
-- Studio stopped each run because its progress could not be saved. Accept either
-- key; the stored sequence must still equal the one inside the delivered frame.
-- The function keeps its owner, grants and triggers.
CREATE OR REPLACE FUNCTION nova_meta.agent_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE login text; version bigint; run record; data text; claimed text;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    login:=nullif(current_setting('nova.session_id',true),'');
    version:=current_setting('nova.security_context_version',true)::bigint;
    IF TG_TABLE_NAME='config_agent_runs' THEN
        IF NEW.status NOT IN ('running','completed','failed','interrupted','cancelled') THEN
            RAISE EXCEPTION 'Invalid agent run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            -- A run starts with its producer's claim, bound to this login.
            IF NEW.status<>'running' OR login IS NULL THEN
                RAISE EXCEPTION 'An agent run starts running for a login' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.last_sequence:=-1; NEW.claim_generation:=1;
            NEW.session_id:=login; NEW.security_version:=version;
            NEW.started_at:=clock_timestamp(); NEW.updated_at:=NEW.started_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.run_id,NEW.owner_name,NEW.agent_id,NEW.thread_id,NEW.started_at,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.run_id,OLD.owner_name,OLD.agent_id,OLD.thread_id,OLD.started_at,
               OLD.provenance) THEN
            RAISE EXCEPTION 'Invalid agent run change' USING ERRCODE='23514';
        END IF;
        IF NEW.role_name IS DISTINCT FROM OLD.role_name THEN
            -- After a role change the run can no longer be replayed or claimed.
            IF NEW.role_name<>'__role_changed__'
               OR ROW(NEW.status,NEW.last_sequence,NEW.session_id,NEW.security_version,NEW.claim_generation)
                  IS DISTINCT FROM ROW(OLD.status,OLD.last_sequence,OLD.session_id,
                      OLD.security_version,OLD.claim_generation) THEN
                RAISE EXCEPTION 'Invalid agent run role change' USING ERRCODE='23514';
            END IF;
            NEW.updated_at:=clock_timestamp();
            RETURN NEW;
        END IF;
        IF OLD.status<>'running' OR NEW.last_sequence<OLD.last_sequence THEN
            RAISE EXCEPTION 'Invalid agent run transition' USING ERRCODE='23514';
        END IF;
        IF NEW.claim_generation<>OLD.claim_generation THEN
            -- A new claim (takeover or interruption) binds the run to this login
            -- and security version and fences every earlier producer.
            IF NEW.claim_generation<>OLD.claim_generation+1 OR login IS NULL
               OR NEW.last_sequence<>OLD.last_sequence
               OR NEW.status NOT IN ('running','interrupted') THEN
                RAISE EXCEPTION 'Invalid agent run claim' USING ERRCODE='23514';
            END IF;
            NEW.session_id:=login; NEW.security_version:=version;
        ELSIF OLD.session_id IS DISTINCT FROM login OR OLD.security_version IS DISTINCT FROM version
              OR NEW.session_id IS DISTINCT FROM OLD.session_id
              OR NEW.security_version IS DISTINCT FROM OLD.security_version THEN
            -- Only the claim holder's login at the claimed security version writes.
            RAISE EXCEPTION 'The agent run belongs to another claim' USING ERRCODE='42501';
        END IF;
        IF NEW.last_sequence<>OLD.last_sequence AND NOT EXISTS (
                SELECT 1 FROM nova_meta.config_agent_run_events e
                WHERE e.run_id=NEW.run_id AND e.sequence=NEW.last_sequence) THEN
            RAISE EXCEPTION 'The high-water mark needs its event' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;
    END IF;
    -- config_agent_run_events: insert only.
    SELECT r.status,r.last_sequence,r.session_id,r.security_version,r.claim_generation INTO run
        FROM nova_meta.config_agent_runs r WHERE r.run_id=NEW.run_id FOR UPDATE;
    IF NOT FOUND OR run.status<>'running' OR run.session_id IS DISTINCT FROM login
       OR run.security_version IS DISTINCT FROM version
       OR NEW.claim_generation IS DISTINCT FROM run.claim_generation THEN
        RAISE EXCEPTION 'The agent run belongs to another claim' USING ERRCODE='42501';
    END IF;
    IF NEW.sequence<=run.last_sequence THEN
        RAISE EXCEPTION 'Run events are appended above the high-water mark' USING ERRCODE='23514';
    END IF;
    -- The stored sequence must be the one inside the delivered frame.
    data:=substr(NEW.frame,strpos(NEW.frame,'data: ')+6);
    IF strpos(NEW.frame,'data: ')=0 OR NOT pg_input_is_valid(data,'jsonb')
       OR jsonb_typeof(data::jsonb)<>'object' THEN
        RAISE EXCEPTION 'Invalid run event frame' USING ERRCODE='23514';
    END IF;
    -- Studio protocol v2 numbers a frame as seq; v1 frames as sequence.
    claimed:=coalesce(data::jsonb->>'seq',data::jsonb->>'sequence');
    IF claimed IS NULL OR claimed !~ '^[0-9]{1,18}$' OR claimed::bigint<>NEW.sequence THEN
        RAISE EXCEPTION 'Invalid run event frame' USING ERRCODE='23514';
    END IF;
    NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
