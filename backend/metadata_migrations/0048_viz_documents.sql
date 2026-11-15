-- Native datasets, charts and dashboards share one identity/revision registry.
-- Existing Superset pointers remain authoritative until verified conversion.
CREATE TABLE nova_meta.viz_resources (
    resource_id uuid PRIMARY KEY,
    workspace_id varchar(64) NOT NULL REFERENCES nova_meta.config_workspaces(id),
    kind text NOT NULL CHECK (kind IN ('dataset','chart','dashboard')),
    owner_name varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    draft_revision bigint NOT NULL DEFAULT 1 CHECK (draft_revision BETWEEN 1 AND 9007199254740991),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    grants_revision bigint NOT NULL DEFAULT 1 CHECK (grants_revision>0),
    current_publication_id uuid,
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX viz_resources_workspace ON nova_meta.viz_resources(workspace_id,kind,updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE nova_meta.viz_revisions (
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    revision bigint NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991),
    schema_version integer NOT NULL CHECK (schema_version=2),
    definition jsonb NOT NULL CHECK (jsonb_typeof(definition)='object'
        AND octet_length(definition::text)<=1048576),
    digest char(64) NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    author_name varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(resource_id,revision)
);
ALTER TABLE nova_meta.viz_resources ADD CONSTRAINT viz_draft_exists
    FOREIGN KEY(resource_id,draft_revision) REFERENCES nova_meta.viz_revisions(resource_id,revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE nova_meta.viz_acl_grants (
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    subject_type text NOT NULL CHECK (subject_type IN ('user','role')),
    subject_id varchar(128) NOT NULL CHECK (subject_id=btrim(subject_id) AND subject_id<>''),
    permission text NOT NULL CHECK (permission IN
        ('view','interact','comment','edit','publish','manage','export')),
    expires_at timestamptz,
    granted_by varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(resource_id,subject_type,subject_id,permission)
);
CREATE INDEX viz_acl_subject ON nova_meta.viz_acl_grants(subject_type,subject_id,resource_id);

CREATE TABLE nova_meta.viz_publications (
    publication_id uuid PRIMARY KEY,
    resource_id uuid NOT NULL,
    revision bigint NOT NULL,
    release_notes varchar(2000) NOT NULL DEFAULT '',
    published_by varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(resource_id,revision) REFERENCES nova_meta.viz_revisions(resource_id,revision),
    UNIQUE(resource_id,publication_id)
);
ALTER TABLE nova_meta.viz_resources ADD CONSTRAINT viz_publication_owner
    FOREIGN KEY(resource_id,current_publication_id)
    REFERENCES nova_meta.viz_publications(resource_id,publication_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE nova_meta.viz_publication_references (
    publication_id uuid NOT NULL REFERENCES nova_meta.viz_publications(publication_id),
    resource_id uuid NOT NULL,
    revision bigint NOT NULL,
    PRIMARY KEY(publication_id,resource_id,revision),
    FOREIGN KEY(resource_id,revision) REFERENCES nova_meta.viz_revisions(resource_id,revision)
);

CREATE TABLE nova_meta.viz_operations (
    operation_id uuid PRIMARY KEY,
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    actor_name varchar(128) NOT NULL DEFAULT current_setting('nova.principal'),
    actor_role varchar(128) NOT NULL DEFAULT current_setting('nova.active_role'),
    session_id text NOT NULL DEFAULT current_setting('nova.session_id'),
    security_context_version bigint NOT NULL
        DEFAULT current_setting('nova.security_context_version')::bigint,
    request_digest char(64) NOT NULL CHECK (request_digest ~ '^[a-f0-9]{64}$'),
    kind text NOT NULL CHECK (kind IN ('create','edit','publish','restore','grants','delete')),
    base_revision bigint NOT NULL CHECK (base_revision>=0),
    new_revision bigint NOT NULL CHECK (new_revision>0),
    event_sequence bigint NOT NULL CHECK (event_sequence>0),
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(resource_id,event_sequence),
    UNIQUE(operation_id,resource_id,event_sequence,kind),
    FOREIGN KEY(resource_id,new_revision) REFERENCES nova_meta.viz_revisions(resource_id,revision)
);

-- Journal entries are committed with receipts. Realtime replay reads this
-- durable order; Redis notifications are optional wakeups, never authority.
CREATE TABLE nova_meta.viz_outbox (
    event_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL UNIQUE REFERENCES nova_meta.viz_operations(operation_id),
    resource_id uuid NOT NULL REFERENCES nova_meta.viz_resources(resource_id),
    sequence bigint NOT NULL CHECK (sequence>0),
    event_kind text NOT NULL CHECK (event_kind IN
        ('create','edit','publish','restore','grants','delete')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(resource_id,sequence),
    FOREIGN KEY(operation_id,resource_id,sequence,event_kind)
        REFERENCES nova_meta.viz_operations(operation_id,resource_id,event_sequence,kind)
);

CREATE FUNCTION nova_meta.viz_actor_valid() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
       AND session_user IN ('nova_meta_web','nova_meta_query','nova_meta_agent')
       AND current_setting('nova.service_identity',true)=session_user
       AND current_setting('nova.principal_type',true)='USER'
       AND nullif(current_setting('nova.session_id',true),'') IS NOT NULL
$body$;

CREATE FUNCTION nova_meta.viz_can(identity uuid, action text) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE resource nova_meta.viz_resources; actions text[];
BEGIN
    IF NOT nova_meta.viz_actor_valid() OR action NOT IN
        ('view','interact','comment','edit','publish','manage','export') THEN RETURN false; END IF;
    SELECT * INTO resource FROM nova_meta.viz_resources WHERE resource_id=identity;
    IF NOT FOUND OR resource.deleted_at IS NOT NULL
       OR nova_meta.workspace_permission(resource.workspace_id) IS NULL THEN RETURN false; END IF;
    IF resource.owner_name=current_setting('nova.principal',true) THEN RETURN true; END IF;
    SELECT coalesce(array_agg(permission),'{}'::text[]) INTO actions
        FROM nova_meta.viz_acl_grants WHERE resource_id=identity
        AND (expires_at IS NULL OR expires_at>statement_timestamp())
        AND ((subject_type='user' AND subject_id=current_setting('nova.principal',true))
            OR (subject_type='role' AND subject_id=current_setting('nova.active_role',true)));
    IF 'manage'=ANY(actions) AND action<>'export' THEN RETURN true; END IF;
    IF action='publish' THEN RETURN 'publish'=ANY(actions) AND 'edit'=ANY(actions); END IF;
    IF action=ANY(actions) THEN RETURN true; END IF;
    IF 'edit'=ANY(actions) AND action IN ('view','interact','comment') THEN RETURN true; END IF;
    RETURN action='view' AND actions && ARRAY['interact','comment'];
END
$body$;

CREATE FUNCTION nova_meta.viz_revision_visible(identity uuid, version bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
    SELECT nova_meta.viz_can(identity,'edit') OR EXISTS (
        SELECT 1 FROM nova_meta.viz_publications p
        JOIN nova_meta.viz_resources r ON r.current_publication_id=p.publication_id
        WHERE nova_meta.viz_can(r.resource_id,'view') AND
        ((p.resource_id=identity AND p.revision=version) OR EXISTS (
            SELECT 1 FROM nova_meta.viz_publication_references ref
            WHERE ref.publication_id=p.publication_id
              AND ref.resource_id=identity AND ref.revision=version
              AND nova_meta.viz_can(identity,'view'))))
$body$;

REVOKE ALL ON FUNCTION nova_meta.viz_actor_valid(),nova_meta.viz_can(uuid,text),
    nova_meta.viz_revision_visible(uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.viz_actor_valid(),nova_meta.viz_can(uuid,text),
    nova_meta.viz_revision_visible(uuid,bigint) TO nova_meta_web,nova_meta_query,nova_meta_agent;

DO $rls$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['viz_resources','viz_revisions','viz_acl_grants','viz_publications',
        'viz_publication_references','viz_operations','viz_outbox'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_owner USING (true)',
            tbl||'_projection',tbl);
    END LOOP;
END
$rls$;
CREATE POLICY viz_resource_read ON nova_meta.viz_resources FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent USING (
        (nova_meta.viz_actor_valid() AND owner_name=current_setting('nova.principal',true)
         AND deleted_at IS NULL AND nova_meta.workspace_permission(workspace_id) IS NOT NULL)
        OR nova_meta.viz_can(resource_id,'view'));
CREATE POLICY viz_resource_create ON nova_meta.viz_resources FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_actor_valid() AND owner_name=current_setting('nova.principal',true)
        AND nova_meta.workspace_permission(workspace_id) IN ('owner','edit'));
CREATE POLICY viz_resource_edit ON nova_meta.viz_resources FOR UPDATE TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'edit') OR nova_meta.viz_can(resource_id,'manage'))
    WITH CHECK (nova_meta.viz_actor_valid());
CREATE POLICY viz_revision_read ON nova_meta.viz_revisions FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent
    USING (nova_meta.viz_revision_visible(resource_id,revision));
CREATE POLICY viz_revision_create ON nova_meta.viz_revisions FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'edit')
        AND author_name=current_setting('nova.principal',true));
CREATE POLICY viz_acl_read ON nova_meta.viz_acl_grants FOR SELECT TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'manage'));
CREATE POLICY viz_acl_insert ON nova_meta.viz_acl_grants FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'manage')
        AND granted_by=current_setting('nova.principal',true));
CREATE POLICY viz_acl_delete ON nova_meta.viz_acl_grants FOR DELETE TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'manage'));
CREATE POLICY viz_publication_read ON nova_meta.viz_publications FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent USING (nova_meta.viz_can(resource_id,'view'));
CREATE POLICY viz_publication_create ON nova_meta.viz_publications FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'publish')
        AND published_by=current_setting('nova.principal',true));
CREATE POLICY viz_reference_read ON nova_meta.viz_publication_references FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent USING (EXISTS (
        SELECT 1 FROM nova_meta.viz_publications p
        WHERE p.publication_id=viz_publication_references.publication_id));
CREATE POLICY viz_reference_create ON nova_meta.viz_publication_references FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.viz_can(resource_id,'view') AND EXISTS (
        SELECT 1 FROM nova_meta.viz_publications p
        WHERE p.publication_id=viz_publication_references.publication_id
          AND nova_meta.viz_can(p.resource_id,'publish')));
CREATE POLICY viz_operation_read ON nova_meta.viz_operations FOR SELECT TO nova_meta_web
    USING (nova_meta.viz_can(resource_id,'edit') OR
        (nova_meta.viz_can(resource_id,'view') AND actor_name=current_setting('nova.principal',true)
         AND actor_role=current_setting('nova.active_role',true)
         AND session_id=current_setting('nova.session_id',true)
         AND security_context_version=current_setting('nova.security_context_version',true)::bigint));
CREATE POLICY viz_operation_create ON nova_meta.viz_operations FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'edit')
        AND actor_name=current_setting('nova.principal',true)
        AND actor_role=current_setting('nova.active_role',true)
        AND session_id=current_setting('nova.session_id',true)
        AND security_context_version=current_setting('nova.security_context_version',true)::bigint);
CREATE POLICY viz_outbox_read ON nova_meta.viz_outbox FOR SELECT
    TO nova_meta_web,nova_meta_query,nova_meta_agent USING (nova_meta.viz_can(resource_id,'view'));
CREATE POLICY viz_outbox_create ON nova_meta.viz_outbox FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.viz_can(resource_id,'edit'));

GRANT SELECT ON nova_meta.viz_resources,nova_meta.viz_revisions,nova_meta.viz_publications,
    nova_meta.viz_publication_references,nova_meta.viz_outbox
    TO nova_meta_web,nova_meta_query,nova_meta_agent;
GRANT SELECT ON nova_meta.viz_operations,nova_meta.viz_acl_grants TO nova_meta_web;
GRANT INSERT(resource_id,workspace_id,kind) ON nova_meta.viz_resources TO nova_meta_web;
GRANT UPDATE(draft_revision,metadata_revision,grants_revision,current_publication_id,deleted_at)
    ON nova_meta.viz_resources TO nova_meta_web;
GRANT INSERT(resource_id,revision,schema_version,definition,digest)
    ON nova_meta.viz_revisions TO nova_meta_web;
GRANT INSERT(resource_id,subject_type,subject_id,permission,expires_at),DELETE
    ON nova_meta.viz_acl_grants TO nova_meta_web;
GRANT INSERT(publication_id,resource_id,revision,release_notes)
    ON nova_meta.viz_publications TO nova_meta_web;
GRANT INSERT ON nova_meta.viz_publication_references TO nova_meta_web;
GRANT INSERT(operation_id,resource_id,request_digest,kind,base_revision,new_revision,event_sequence)
    ON nova_meta.viz_operations TO nova_meta_web;
GRANT INSERT(event_id,operation_id,resource_id,sequence,event_kind)
    ON nova_meta.viz_outbox TO nova_meta_web;

CREATE FUNCTION nova_meta.viz_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid() THEN
        RAISE EXCEPTION 'Native document mutation is not authorized' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
    IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true) THEN
        RAISE EXCEPTION 'Metadata is in maintenance' USING ERRCODE='25006';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['viz_resources','viz_revisions','viz_acl_grants','viz_publications',
        'viz_publication_references','viz_operations','viz_outbox'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
            'ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.viz_write_guard()',
            tbl||'_guard',tbl);
    END LOOP;
END
$guards$;

CREATE FUNCTION nova_meta.viz_resource_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF NEW.metadata_revision<>OLD.metadata_revision+1 OR OLD.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid document revision' USING ERRCODE='23514';
    END IF;
    IF NEW.draft_revision<>OLD.draft_revision AND
       (NEW.draft_revision<>OLD.draft_revision+1 OR NOT nova_meta.viz_can(OLD.resource_id,'edit'))
    THEN RAISE EXCEPTION 'Invalid draft revision' USING ERRCODE='42501'; END IF;
    IF NEW.current_publication_id IS DISTINCT FROM OLD.current_publication_id
       AND NOT nova_meta.viz_can(OLD.resource_id,'publish') THEN
        RAISE EXCEPTION 'Publication requires publish permission' USING ERRCODE='42501';
    END IF;
    IF NEW.grants_revision<>OLD.grants_revision AND
       (NEW.grants_revision<>OLD.grants_revision+1 OR NOT nova_meta.viz_can(OLD.resource_id,'manage'))
    THEN RAISE EXCEPTION 'Grants require manage permission' USING ERRCODE='42501'; END IF;
    IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at
       AND NOT nova_meta.viz_can(OLD.resource_id,'manage') THEN
        RAISE EXCEPTION 'Deletion requires manage permission' USING ERRCODE='42501';
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER viz_resource_revision BEFORE UPDATE ON nova_meta.viz_resources
    FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_resource_guard();

CREATE FUNCTION nova_meta.viz_receipt_required() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity uuid; receipt_sequence bigint; expected_base bigint;
    expected_revision bigint; allowed_kinds text[];
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid() THEN
        RAISE EXCEPTION 'Invalid document transaction' USING ERRCODE='42501';
    END IF;
    identity:=CASE WHEN TG_OP='DELETE' THEN OLD.resource_id ELSE NEW.resource_id END;
    IF TG_TABLE_NAME='viz_resources' THEN
        receipt_sequence:=NEW.metadata_revision;
        expected_revision:=NEW.draft_revision;
        IF TG_OP='INSERT' THEN
            expected_base:=0; allowed_kinds:=ARRAY['create'];
        ELSE
            expected_base:=OLD.draft_revision;
            IF ((NEW.draft_revision<>OLD.draft_revision)::int
                +(NEW.grants_revision<>OLD.grants_revision)::int
                +(NEW.current_publication_id IS DISTINCT FROM OLD.current_publication_id)::int
                +(NEW.deleted_at IS DISTINCT FROM OLD.deleted_at)::int)<>1 THEN
                RAISE EXCEPTION 'Document mutation must have one semantic effect'
                    USING ERRCODE='23514';
            END IF;
            allowed_kinds:=CASE
                WHEN NEW.draft_revision<>OLD.draft_revision THEN ARRAY['edit','restore']
                WHEN NEW.grants_revision<>OLD.grants_revision THEN ARRAY['grants']
                WHEN NEW.current_publication_id IS DISTINCT FROM OLD.current_publication_id
                    THEN ARRAY['publish']
                ELSE ARRAY['delete'] END;
        END IF;
    ELSIF TG_TABLE_NAME='viz_revisions' THEN
        expected_revision:=NEW.revision; allowed_kinds:=ARRAY['create','edit','restore'];
    ELSIF TG_TABLE_NAME='viz_publications' THEN
        expected_revision:=NEW.revision; allowed_kinds:=ARRAY['publish'];
        IF NOT EXISTS (SELECT 1 FROM nova_meta.viz_resources r
            WHERE r.resource_id=identity AND r.current_publication_id=NEW.publication_id) THEN
            RAISE EXCEPTION 'Publication must advance the published pointer'
                USING ERRCODE='23514';
        END IF;
    ELSE
        SELECT metadata_revision INTO receipt_sequence FROM nova_meta.viz_resources
            WHERE resource_id=identity;
        allowed_kinds:=ARRAY['grants'];
    END IF;
    IF NOT EXISTS (SELECT 1 FROM nova_meta.viz_operations o JOIN nova_meta.viz_outbox e
        ON e.operation_id=o.operation_id WHERE o.resource_id=identity
          AND (receipt_sequence IS NULL OR o.event_sequence=receipt_sequence)
          AND (expected_revision IS NULL OR o.new_revision=expected_revision)
          AND (expected_base IS NULL OR o.base_revision=expected_base)
          AND o.transaction_id=pg_current_xact_id() AND o.kind=ANY(allowed_kinds)) THEN
        RAISE EXCEPTION 'Document change requires atomic receipt and event' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER viz_resource_receipt AFTER INSERT OR UPDATE ON nova_meta.viz_resources
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_receipt_required();
CREATE CONSTRAINT TRIGGER viz_acl_receipt AFTER INSERT OR DELETE ON nova_meta.viz_acl_grants
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_receipt_required();
CREATE CONSTRAINT TRIGGER viz_revision_receipt AFTER INSERT ON nova_meta.viz_revisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_receipt_required();
CREATE CONSTRAINT TRIGGER viz_publication_receipt AFTER INSERT ON nova_meta.viz_publications
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_receipt_required();

CREATE FUNCTION nova_meta.viz_event_required() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid() THEN
        RAISE EXCEPTION 'Invalid document transaction' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM nova_meta.viz_resources r JOIN nova_meta.viz_outbox e
        ON e.resource_id=r.resource_id WHERE r.resource_id=NEW.resource_id
          AND e.operation_id=NEW.operation_id AND r.metadata_revision>=NEW.event_sequence
          AND r.draft_revision>=NEW.new_revision) THEN
        RAISE EXCEPTION 'Receipt requires a committed head and event' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_event_required() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER viz_operation_event AFTER INSERT ON nova_meta.viz_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_event_required();

CREATE FUNCTION nova_meta.viz_reference_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nova_meta.viz_publications p
        JOIN nova_meta.viz_resources r ON r.current_publication_id=p.publication_id
        JOIN nova_meta.viz_operations o ON o.resource_id=r.resource_id
        JOIN nova_meta.viz_outbox e ON e.operation_id=o.operation_id
        WHERE p.publication_id=NEW.publication_id AND o.kind='publish'
          AND o.transaction_id=pg_current_xact_id() AND o.event_sequence=r.metadata_revision
          AND o.new_revision=p.revision) THEN
        RAISE EXCEPTION 'Published references are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.viz_reference_guard() FROM PUBLIC;
CREATE CONSTRAINT TRIGGER viz_reference_receipt AFTER INSERT ON nova_meta.viz_publication_references
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nova_meta.viz_reference_guard();

CREATE FUNCTION nova_meta.audit_viz_operation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user<>'nova_meta_web' OR NOT nova_meta.viz_actor_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_TABLE_NAME<>'viz_operations' THEN
        RAISE EXCEPTION 'Invalid document audit source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (NEW.operation_id::text,'metadata','viz_document',NEW.resource_id::text,
        upper(NEW.kind),'SUCCESS','ALLOW','viz_resources',
        jsonb_build_object('resource_id',NEW.resource_id),NEW.event_sequence);
    RETURN NULL;
END
$body$;
CREATE TRIGGER viz_operation_audit AFTER INSERT ON nova_meta.viz_operations
    FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_viz_operation();
REVOKE ALL ON FUNCTION nova_meta.viz_write_guard(),nova_meta.viz_resource_guard(),
    nova_meta.viz_receipt_required(),nova_meta.audit_viz_operation() FROM PUBLIC;
