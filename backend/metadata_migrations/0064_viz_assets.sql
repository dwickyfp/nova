-- Dashboard images (ADR-001, P2 part 3). An editor uploads a PNG, JPEG or WebP
-- picture for an image item or a page background. Its bytes live in the
-- dashboard workspace's storage area, under
-- <base>/<namespace>/.viz-assets/<dashboard>/<asset>, written before this row
-- commits and removed when the commit fails. This registry is what makes an
-- object a dashboard image:
--
-- * reading needs View on the dashboard (viz_can), so whoever can see the
--   dashboard sees its pictures and nobody else does;
-- * registering needs Edit, as the person who uploaded it;
-- * a row never changes and is never removed: an older revision of the
--   dashboard may still show the picture;
-- * every registration is audited.
--
-- An object the registry does not hold (a crash between the write and the
-- commit, or an unknown commit outcome) is removed by the maintenance
-- reconciler (0060) after its retention, by key.
CREATE TABLE nova_meta.viz_assets (
    asset_id uuid PRIMARY KEY,
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    object_key text NOT NULL UNIQUE
        CHECK (char_length(object_key) BETWEEN 1 AND 1024 AND object_key LIKE '%/.viz-assets/%'),
    media_type varchar(16) NOT NULL CHECK (media_type IN ('image/png','image/jpeg','image/webp')),
    size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 2097152),
    content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    width integer NOT NULL CHECK (width BETWEEN 1 AND 8192),
    height integer NOT NULL CHECK (height BETWEEN 1 AND 8192),
    created_by varchar(128) NOT NULL CHECK (created_by=btrim(created_by) AND created_by<>''),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX viz_assets_by_resource ON nova_meta.viz_assets(resource_id, created_at);
ALTER TABLE nova_meta.viz_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.viz_assets FORCE ROW LEVEL SECURITY;

CREATE POLICY viz_asset_read ON nova_meta.viz_assets FOR SELECT TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'view'));
CREATE POLICY viz_asset_create ON nova_meta.viz_assets FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'edit')
        AND created_by=current_setting('nova.principal',true));
CREATE POLICY viz_assets_projection ON nova_meta.viz_assets FOR SELECT TO nova_meta_owner
    USING (true);

GRANT SELECT ON nova_meta.viz_assets TO nova_meta_web;
GRANT INSERT(asset_id,resource_id,object_key,media_type,size_bytes,content_sha256,width,height,
             created_by) ON nova_meta.viz_assets TO nova_meta_web;

CREATE FUNCTION nova_meta.viz_asset_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'A dashboard image cannot be changed or removed' USING ERRCODE='42501';
    END IF;
    NEW.created_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER viz_asset_rows BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.viz_assets
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_asset_guard();
-- Same statement guard as every other document table: web login, valid actor, active epoch.
CREATE TRIGGER viz_assets_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.viz_assets FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.viz_write_guard();

CREATE FUNCTION nova_meta.audit_viz_asset() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME<>'viz_assets' THEN
        RAISE EXCEPTION 'Invalid image audit source' USING ERRCODE='42501';
    END IF;
    -- The key, type and size are recorded; the picture itself never is.
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata','viz_asset',NEW.resource_id::text,'UPLOAD_IMAGE',
        'SUCCESS','ALLOW','viz_assets',
        jsonb_build_object('asset_id',NEW.asset_id,'media_type',NEW.media_type,
                           'size_bytes',NEW.size_bytes),1);
    RETURN NULL;
END
$body$;
CREATE TRIGGER viz_asset_audit AFTER INSERT ON nova_meta.viz_assets
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_viz_asset();
REVOKE ALL ON FUNCTION nova_meta.viz_asset_guard(),nova_meta.audit_viz_asset() FROM PUBLIC;

-- The reconciler reads keys and nothing else, as for workspace files (0060).
GRANT SELECT (object_key) ON nova_meta.viz_assets TO nova_meta_maintenance;
CREATE POLICY viz_asset_reconciler_read ON nova_meta.viz_assets
    FOR SELECT TO nova_meta_maintenance USING (nova_meta.workspace_object_reconciler());
