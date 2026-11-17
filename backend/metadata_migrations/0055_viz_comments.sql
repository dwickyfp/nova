-- Dashboard comments. People discuss a dashboard, one of its pages or one of
-- its widgets. A comment is a note about the document, never data. Reading
-- needs View; writing and resolving need Comment; only the author or someone
-- who manages the dashboard removes one. Text and author never change after
-- posting, removal is a soft delete, and every change is audited.
CREATE TABLE nova_meta.viz_comments (
    comment_id uuid PRIMARY KEY,
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    page_id uuid,
    widget_id uuid,
    author varchar(128) NOT NULL CHECK (author=btrim(author) AND author<>''),
    body text NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 2000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    resolved_by varchar(128),
    deleted_at timestamptz,
    CHECK ((resolved_at IS NULL)=(resolved_by IS NULL)),
    CHECK (widget_id IS NULL OR page_id IS NOT NULL)
);
CREATE INDEX viz_comments_by_resource ON nova_meta.viz_comments(resource_id, created_at);
ALTER TABLE nova_meta.viz_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.viz_comments FORCE ROW LEVEL SECURITY;

-- Removed comments stay readable to the policy (an UPDATE's new row must pass it);
-- the application never lists them.
CREATE POLICY viz_comment_read ON nova_meta.viz_comments FOR SELECT TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'view'));
CREATE POLICY viz_comment_create ON nova_meta.viz_comments FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'comment')
        AND author=current_setting('nova.principal',true)
        AND resolved_at IS NULL AND resolved_by IS NULL AND deleted_at IS NULL);
-- Which change is allowed is decided by viz_comment_guard, row by row.
CREATE POLICY viz_comment_change ON nova_meta.viz_comments FOR UPDATE TO nova_meta_web
    USING (deleted_at IS NULL AND nova_meta.viz_can(resource_id,'view')) WITH CHECK (true);
CREATE POLICY viz_comments_projection ON nova_meta.viz_comments FOR SELECT TO nova_meta_owner
    USING (true);

GRANT SELECT ON nova_meta.viz_comments TO nova_meta_web;
GRANT INSERT(comment_id,resource_id,page_id,widget_id,author,body)
    ON nova_meta.viz_comments TO nova_meta_web;
GRANT UPDATE(resolved_at,resolved_by,deleted_at) ON nova_meta.viz_comments TO nova_meta_web;

CREATE FUNCTION nova_meta.viz_comment_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE actor text := current_setting('nova.principal',true);
BEGIN
    IF TG_OP='INSERT' THEN
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
    END IF;
    IF ROW(NEW.comment_id,NEW.resource_id,NEW.page_id,NEW.widget_id,NEW.author,NEW.body,
           NEW.created_at)
       IS DISTINCT FROM ROW(OLD.comment_id,OLD.resource_id,OLD.page_id,OLD.widget_id,OLD.author,
           OLD.body,OLD.created_at) OR OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'A comment cannot be rewritten' USING ERRCODE='42501';
    END IF;
    IF NEW.deleted_at IS NOT NULL THEN
        IF OLD.author<>actor AND NOT nova_meta.viz_can(OLD.resource_id,'manage') THEN
            RAISE EXCEPTION 'Only the author or a manager removes a comment' USING ERRCODE='42501';
        END IF;
        NEW.deleted_at:=clock_timestamp();
        NEW.resolved_at:=OLD.resolved_at; NEW.resolved_by:=OLD.resolved_by;
        RETURN NEW;
    END IF;
    IF NOT nova_meta.viz_can(OLD.resource_id,'comment') THEN
        RAISE EXCEPTION 'Resolving a comment needs Comment access' USING ERRCODE='42501';
    END IF;
    IF NEW.resolved_by IS NOT NULL THEN
        NEW.resolved_by:=actor; NEW.resolved_at:=clock_timestamp();
    ELSE
        NEW.resolved_at:=NULL;
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER viz_comment_rows BEFORE INSERT OR UPDATE ON nova_meta.viz_comments
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_comment_guard();
-- Same statement guard as every other document table: web login, valid actor, active epoch.
CREATE TRIGGER viz_comments_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.viz_comments FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.viz_write_guard();

CREATE FUNCTION nova_meta.audit_viz_comment() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME<>'viz_comments' THEN
        RAISE EXCEPTION 'Invalid comment audit source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata','viz_comment',NEW.resource_id::text,
        CASE WHEN TG_OP='INSERT' THEN 'COMMENT' WHEN NEW.deleted_at IS NOT NULL THEN 'DELETE_COMMENT'
             WHEN NEW.resolved_at IS NOT NULL THEN 'RESOLVE_COMMENT' ELSE 'REOPEN_COMMENT' END,
        'SUCCESS','ALLOW','viz_comments',jsonb_build_object('comment_id',NEW.comment_id),1);
    RETURN NULL;
END
$body$;
CREATE TRIGGER viz_comment_audit AFTER INSERT OR UPDATE ON nova_meta.viz_comments
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_viz_comment();
REVOKE ALL ON FUNCTION nova_meta.viz_comment_guard(),nova_meta.audit_viz_comment() FROM PUBLIC;
