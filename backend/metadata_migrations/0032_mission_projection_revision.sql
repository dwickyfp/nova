-- Cursor persistence must fence concurrent projectors without emitting another
-- public Mission revision for a mission_updated frame.
ALTER TABLE nova_meta.config_studio_missions
    ADD COLUMN storage_revision bigint NOT NULL DEFAULT 1 CHECK (storage_revision > 0);
GRANT UPDATE(storage_revision) ON nova_meta.config_studio_missions
    TO nova_meta_web,nova_meta_agent;

CREATE FUNCTION nova_meta.mission_projection_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE login text; version bigint;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    login:=nullif(current_setting('nova.session_id',true),'');
    version:=current_setting('nova.security_context_version',true)::bigint;
    IF json_typeof(NEW.payload::json)<>'object' OR login IS NULL THEN
        RAISE EXCEPTION 'Invalid Mission' USING ERRCODE='23514';
    END IF;
    NEW.session_id:=login; NEW.security_version:=version;
    IF TG_OP='INSERT' THEN
        NEW.provenance:='runtime'; NEW.storage_revision:=1;
        NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
    ELSE
        IF ROW(NEW.mission_id,NEW.owner_name,NEW.thread_id,NEW.role_name,NEW.created_at,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.mission_id,OLD.owner_name,OLD.thread_id,OLD.role_name,
                                   OLD.created_at,OLD.provenance)
           OR NEW.storage_revision<>OLD.storage_revision+1
           OR NEW.revision NOT IN (OLD.revision,OLD.revision+1) THEN
            RAISE EXCEPTION 'Invalid Mission change' USING ERRCODE='23514';
        END IF;
        IF NEW.revision=OLD.revision AND (
            NEW.payload::jsonb-'projection_cursor' IS DISTINCT FROM OLD.payload::jsonb-'projection_cursor'
            OR ROW(NEW.session_id,NEW.security_version) IS DISTINCT FROM ROW(OLD.session_id,OLD.security_version)
        ) THEN
            RAISE EXCEPTION 'A Mission change needs a new revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
DROP TRIGGER config_studio_missions_row_guard ON nova_meta.config_studio_missions;
CREATE TRIGGER config_studio_missions_row_guard BEFORE INSERT OR UPDATE
    ON nova_meta.config_studio_missions FOR EACH ROW
    EXECUTE FUNCTION nova_meta.mission_projection_guard();
