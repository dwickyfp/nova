-- The remaining small control domains: assistant conversations, UI state and
-- the translation cache; Nova's access-control projections and business
-- policy; Superset author roles, the Dataviz publication journal and the
-- Semantic View dataset map; migration sources and jobs; and AI function
-- usage metering.
-- PG stores Nova's own configuration and journals. Ranger and StarRocks stay
-- the authority for user data: nothing here grants or bypasses access to it.
-- Rows carry provenance: imported source rows are kept as they were, while
-- identity, ownership and lifecycle rules apply to rows written at runtime.

-- ── Assistant ─────────────────────────────────────────────────────────────

CREATE TABLE nova_meta.config_assistant_threads (
    thread_id varchar(64) PRIMARY KEY CHECK (octet_length(thread_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    title varchar(256) NOT NULL CHECK (octet_length(title)<=256),
    workspace_file_id varchar(64) CHECK (octet_length(workspace_file_id)<=64),
    agent_id varchar(64) CHECK (octet_length(agent_id)<=64),
    learning_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_assistant_threads_owner ON nova_meta.config_assistant_threads
    (user_name,updated_at DESC,thread_id DESC);

CREATE TABLE nova_meta.config_assistant_messages (
    message_id varchar(64) PRIMARY KEY CHECK (octet_length(message_id)<=64),
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    seq integer NOT NULL,
    role varchar(16) NOT NULL CHECK (octet_length(role)<=16),
    content text CHECK (char_length(content)<=1048576),
    created_at timestamptz,
    agent_id varchar(64) CHECK (octet_length(agent_id)<=64),
    model_name varchar(128) CHECK (octet_length(model_name)<=128),
    prompt_tokens integer,
    completion_tokens integer,
    total_tokens integer,
    steps jsonb,
    instructions text CHECK (char_length(instructions)<=1048576),
    security_context jsonb,
    feedback varchar(16) CHECK (octet_length(feedback)<=16),
    attachments jsonb,
    learning_state varchar(32) CHECK (octet_length(learning_state)<=32),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- Replay order is exact per thread for runtime rows; imported duplicates of a
-- sequence number are kept as they were.
CREATE UNIQUE INDEX config_assistant_messages_runtime_seq ON nova_meta.config_assistant_messages
    (thread_id,seq) WHERE provenance='runtime';
CREATE INDEX config_assistant_messages_thread ON nova_meta.config_assistant_messages
    (thread_id,user_name,seq);
CREATE INDEX config_assistant_messages_pending ON nova_meta.config_assistant_messages
    (message_id) WHERE learning_state='pending';

CREATE TABLE nova_meta.config_assistant_ui_state (
    state_id varchar(64) PRIMARY KEY CHECK (octet_length(state_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    message_id varchar(64) NOT NULL CHECK (octet_length(message_id)<=64),
    content_id varchar(64) NOT NULL CHECK (octet_length(content_id)<=64),
    active_role varchar(128) NOT NULL CHECK (octet_length(active_role)<=128),
    security_context_version bigint NOT NULL,
    program_sha256 varchar(64) NOT NULL CHECK (octet_length(program_sha256)<=64),
    revision bigint NOT NULL,
    state jsonb NOT NULL,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_assistant_ui_state_owner ON nova_meta.config_assistant_ui_state
    (user_name,thread_id,message_id);

-- Model translations of built-in product messages, shared by every caller.
CREATE TABLE nova_meta.config_i18n_messages (
    language varchar(16) NOT NULL CHECK (octet_length(language)<=16),
    message_id varchar(64) NOT NULL CHECK (octet_length(message_id)<=64),
    version integer NOT NULL,
    text varchar(1024) NOT NULL CHECK (char_length(text)<=1024),
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (language,message_id)
);

-- ── Access control ────────────────────────────────────────────────────────

CREATE TABLE nova_meta.config_security_role_projections (
    role_name varchar(128) PRIMARY KEY CHECK (octet_length(role_name)<=128),
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    marker_exists boolean NOT NULL DEFAULT false,
    ranger_exists boolean NOT NULL DEFAULT false,
    message varchar(2048) CHECK (char_length(message)<=2048),
    updated_at timestamptz,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_role varchar(128) CHECK (octet_length(updated_role)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_security_policies (
    policy_name varchar(512) PRIMARY KEY CHECK (octet_length(policy_name)<=512),
    ranger_id bigint,
    ranger_guid varchar(128) CHECK (octet_length(ranger_guid)<=128),
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    desired_hash text CHECK (char_length(desired_hash)<=65533),
    updated_at timestamptz,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_role varchar(128) CHECK (octet_length(updated_role)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_data_scope_assignments (
    assignment_id varchar(128) PRIMARY KEY CHECK (octet_length(assignment_id)<=128),
    principal varchar(128) NOT NULL CHECK (octet_length(principal)<=128),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    dimension_key varchar(128) NOT NULL CHECK (octet_length(dimension_key)<=128),
    scope_values_json text NOT NULL CHECK (char_length(scope_values_json)<=65533),
    state varchar(32) NOT NULL CHECK (octet_length(state)<=32),
    updated_at timestamptz,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_role varchar(128) CHECK (octet_length(updated_role)<=128),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

-- Every saved business policy is an immutable revision; the newest revision of
-- a policy family ("default", stored as "default:<digest>") is in force.
CREATE TABLE nova_meta.config_business_policies (
    id varchar(64) NOT NULL CHECK (octet_length(id)<=64),
    revision bigint NOT NULL,
    definition jsonb NOT NULL,
    created_at timestamptz,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    created_role varchar(128) CHECK (octet_length(created_role)<=128),
    policy_key varchar(64) GENERATED ALWAYS AS (split_part(id,':',1)) STORED,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (id,revision)
);
-- Two editors saving from the same revision cannot both win.
CREATE UNIQUE INDEX config_business_policies_runtime_revision ON nova_meta.config_business_policies
    (policy_key,revision) WHERE provenance='runtime';

-- ── Superset ──────────────────────────────────────────────────────────────

CREATE TABLE nova_meta.config_bi_author_roles (
    role_name varchar(128) PRIMARY KEY CHECK (octet_length(role_name)<=128),
    created_by varchar(128) NOT NULL CHECK (octet_length(created_by)<=128),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- The author role set changes as a whole: a change names the set revision it
-- read, and role rows change only in the transaction that advanced it.
CREATE TABLE nova_meta.config_bi_author_role_set (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL CHECK (revision>0),
    changed_xact xid8,
    updated_by varchar(128) CHECK (octet_length(updated_by)<=128),
    updated_at timestamptz
);

CREATE TABLE nova_meta.config_dataviz_publications (
    operation_id varchar(64) PRIMARY KEY CHECK (octet_length(operation_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    idempotency_scope varchar(80) NOT NULL CHECK (octet_length(idempotency_scope)<=80),
    request_fingerprint varchar(80) NOT NULL CHECK (octet_length(request_fingerprint)<=80),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    target_mode varchar(16) CHECK (octet_length(target_mode)<=16),
    dashboard_id bigint,
    chart_id bigint,
    dataset_id bigint,
    artifact_id varchar(64) CHECK (octet_length(artifact_id)<=64),
    created_objects varchar(256) CHECK (octet_length(created_objects)<=256),
    query_fingerprint varchar(80) CHECK (octet_length(query_fingerprint)<=80),
    visualization_fingerprint varchar(80) CHECK (octet_length(visualization_fingerprint)<=80),
    source_thread_id varchar(64) CHECK (octet_length(source_thread_id)<=64),
    source_message_id varchar(64) CHECK (octet_length(source_message_id)<=64),
    source_content_id varchar(64) CHECK (octet_length(source_content_id)<=64),
    strategy_id varchar(64) CHECK (octet_length(strategy_id)<=64),
    revision_before varchar(80) CHECK (octet_length(revision_before)<=80),
    revision_after varchar(80) CHECK (octet_length(revision_after)<=80),
    business_context jsonb,
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    security_context_version bigint,
    error varchar(1024) CHECK (char_length(error)<=1024),
    created_at timestamptz,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_dataviz_publications_scope ON nova_meta.config_dataviz_publications
    (owner_name,idempotency_scope,created_at DESC);
CREATE INDEX config_dataviz_publications_chart ON nova_meta.config_dataviz_publications
    (chart_id) WHERE status='SUCCEEDED';

CREATE TABLE nova_meta.config_semantic_bi_datasets (
    view_id varchar(64) NOT NULL CHECK (octet_length(view_id)<=64),
    dataset_name varchar(128) NOT NULL CHECK (octet_length(dataset_name)<=128),
    bi_dataset_id bigint,
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    version integer,
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    message varchar(1024) CHECK (char_length(message)<=1024),
    synced_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (view_id,dataset_name)
);
CREATE INDEX config_semantic_bi_datasets_dataset ON nova_meta.config_semantic_bi_datasets
    (bi_dataset_id);

-- ── Migration ─────────────────────────────────────────────────────────────

-- A source holds its address and a reference to its credential; the value
-- stays in the secret store and is resolved at call time.
CREATE TABLE nova_meta.config_migration_sources (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    name varchar(256) NOT NULL CHECK (octet_length(name)<=256),
    host varchar(256) NOT NULL CHECK (octet_length(host)<=256),
    port integer NOT NULL DEFAULT 9030,
    username varchar(128) NOT NULL DEFAULT 'root' CHECK (octet_length(username)<=128),
    secret_ref varchar(1024) CHECK (octet_length(secret_ref)<=1024),
    comment varchar(1024) CHECK (char_length(comment)<=1024),
    created_at timestamptz,
    created_by varchar(128) CHECK (octet_length(created_by)<=128),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE UNIQUE INDEX config_migration_sources_runtime_name ON nova_meta.config_migration_sources
    (name) WHERE provenance='runtime';

CREATE TABLE nova_meta.config_migration_jobs (
    id varchar(64) PRIMARY KEY CHECK (octet_length(id)<=64),
    operation varchar(32) NOT NULL CHECK (octet_length(operation)<=32),
    actor varchar(128) NOT NULL CHECK (octet_length(actor)<=128),
    session_fingerprint varchar(64) NOT NULL DEFAULT '' CHECK (octet_length(session_fingerprint)<=64),
    active_role varchar(128) CHECK (octet_length(active_role)<=128),
    security_context_version integer NOT NULL DEFAULT 1,
    source_name varchar(256) NOT NULL CHECK (octet_length(source_name)<=256),
    request_json text NOT NULL CHECK (char_length(request_json)<=1048576),
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    current_database varchar(256) CHECK (octet_length(current_database)<=256),
    result_json text CHECK (char_length(result_json)<=1048576),
    error_code varchar(64) CHECK (octet_length(error_code)<=64),
    created_at timestamptz,
    started_at timestamptz,
    heartbeat_at timestamptz,
    finished_at timestamptz,
    lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_migration_jobs_queue ON nova_meta.config_migration_jobs (status,created_at,id);

-- ── AI function usage ─────────────────────────────────────────────────────

-- Append-only metering. The source table is a duplicate-key table: identical
-- rows are distinct events, so the target identity is (recorded_at,
-- record_id) and the legacy usage_id is kept without inferring uniqueness.
CREATE SEQUENCE nova_meta.usage_llm_functions_usage_id AS bigint START 1000000000;
CREATE TABLE nova_meta.usage_llm_functions (
    record_id uuid NOT NULL CHECK (record_id<>'00000000-0000-0000-0000-000000000000'::uuid),
    usage_id bigint NOT NULL,
    recorded_at timestamptz NOT NULL,
    function_name varchar(64) NOT NULL CHECK (octet_length(function_name)<=64),
    alias_name varchar(128) CHECK (octet_length(alias_name)<=128),
    model_name varchar(256) CHECK (octet_length(model_name)<=256),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    request_count bigint NOT NULL,
    prompt_tokens bigint,
    completion_tokens bigint,
    total_tokens bigint,
    duration_ms bigint,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (recorded_at,record_id)
);

DO $rls$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_assistant_threads','config_assistant_messages',
        'config_assistant_ui_state','config_i18n_messages','config_security_role_projections',
        'config_security_policies','config_data_scope_assignments','config_business_policies',
        'config_bi_author_roles','config_bi_author_role_set','config_dataviz_publications',
        'config_semantic_bi_datasets','config_migration_sources','config_migration_jobs',
        'usage_llm_functions'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY',tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY',tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            tbl||'_import',tbl);
    END LOOP;
END
$rls$;

-- ── Actor predicates ──────────────────────────────────────────────────────

CREATE FUNCTION nova_meta.apb_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
-- The bound principal itself.
CREATE FUNCTION nova_meta.apb_actor_is(who text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.apb_actor_bound() AND who IS NOT DISTINCT FROM current_setting('nova.principal',true)
$body$;
-- A person acting in a role allowed to administer access control.
CREATE FUNCTION nova_meta.apb_security_admin() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.apb_actor_bound()
        AND current_setting('nova.principal_type',true)='USER'
        AND current_setting('nova.active_role',true) IN ('ACCOUNTADMIN','SECURITYADMIN','security_admin')
$body$;
CREATE FUNCTION nova_meta.apb_account_admin() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.apb_actor_bound()
        AND current_setting('nova.principal_type',true)='USER'
        AND current_setting('nova.active_role',true)='ACCOUNTADMIN'
$body$;
-- An explicitly authorized service principal (scheduled learning work).
CREATE FUNCTION nova_meta.apb_service() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.apb_actor_bound() AND current_setting('nova.principal_type',true)='SERVICE'
$body$;
-- A person whose active role administers the deployment (usage monitoring).
CREATE FUNCTION nova_meta.apb_administrator() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.apb_actor_bound()
        AND current_setting('nova.principal_type',true)='USER'
        AND nova_meta.actor_is_administrator()
$body$;
REVOKE ALL ON FUNCTION nova_meta.apb_actor_bound(),nova_meta.apb_actor_is(text),
    nova_meta.apb_security_admin(),nova_meta.apb_account_admin(),nova_meta.apb_service(),
    nova_meta.apb_administrator() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.apb_actor_bound(),nova_meta.apb_actor_is(text),
    nova_meta.apb_security_admin(),nova_meta.apb_account_admin(),nova_meta.apb_service(),
    nova_meta.apb_administrator()
    TO nova_meta_web,nova_meta_agent,nova_meta_automation,nova_meta_task,nova_meta_ingestion;

-- Who really reaches each table today:
--   web         API routers: conversations, UI state, feedback, observability,
--               AI usage monitoring, access-control administration, Superset
--               author roles and SSO mapping, Dataviz publication, Semantic
--               View sync, migration sources and jobs, the AI function gateway
--   agent,      the Studio assistant loop and its tools (conversation turns,
--   automation  translations, Dataviz publication, business policy reads)
--   task        the learning handlers, as the scheduled service principal
--   ingestion   the migration job worker
-- Query, proxy, scheduler, ml, notebook, maintenance and outbox have no grant.

-- ── Assistant access: a person reads and writes only their own rows ───────

CREATE POLICY config_assistant_threads_own ON nova_meta.config_assistant_threads FOR ALL
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_is(user_name)) WITH CHECK (nova_meta.apb_actor_is(user_name));
CREATE POLICY config_assistant_threads_monitor ON nova_meta.config_assistant_threads FOR SELECT
    TO nova_meta_web USING (nova_meta.apb_administrator());
CREATE POLICY config_assistant_threads_learning ON nova_meta.config_assistant_threads FOR SELECT
    TO nova_meta_task USING (nova_meta.apb_service() AND learning_enabled);
GRANT SELECT,INSERT (thread_id,user_name,title,workspace_file_id,agent_id),
    UPDATE (title,learning_enabled,updated_at)
    ON nova_meta.config_assistant_threads TO nova_meta_web,nova_meta_agent,nova_meta_automation;
GRANT DELETE ON nova_meta.config_assistant_threads TO nova_meta_web;
GRANT SELECT ON nova_meta.config_assistant_threads TO nova_meta_task;

CREATE POLICY config_assistant_messages_own ON nova_meta.config_assistant_messages FOR ALL
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_is(user_name)) WITH CHECK (nova_meta.apb_actor_is(user_name));
CREATE POLICY config_assistant_messages_monitor ON nova_meta.config_assistant_messages FOR SELECT
    TO nova_meta_web USING (nova_meta.apb_administrator());
-- Learning sees only questions queued for learning: pending ones it works on
-- and, because a completed row must stay readable to its updater, completed ones.
CREATE POLICY config_assistant_messages_learning_read ON nova_meta.config_assistant_messages
    FOR SELECT TO nova_meta_task
    USING (nova_meta.apb_service() AND role='user' AND learning_state IN ('pending','complete'));
CREATE POLICY config_assistant_messages_learning_update ON nova_meta.config_assistant_messages
    FOR UPDATE TO nova_meta_task
    USING (nova_meta.apb_service() AND role='user' AND learning_state='pending')
    WITH CHECK (nova_meta.apb_service() AND learning_state IN ('pending','complete'));
GRANT SELECT,INSERT (message_id,thread_id,user_name,seq,role,content,agent_id,model_name,
        prompt_tokens,completion_tokens,total_tokens,steps,instructions,security_context,
        attachments,learning_state),
    UPDATE (feedback)
    ON nova_meta.config_assistant_messages TO nova_meta_web,nova_meta_agent,nova_meta_automation;
GRANT DELETE ON nova_meta.config_assistant_messages TO nova_meta_web;
GRANT SELECT,UPDATE (steps,learning_state) ON nova_meta.config_assistant_messages TO nova_meta_task;

CREATE POLICY config_assistant_ui_state_own ON nova_meta.config_assistant_ui_state FOR ALL
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_is(user_name)) WITH CHECK (nova_meta.apb_actor_is(user_name));
GRANT SELECT,INSERT (state_id,user_name,thread_id,message_id,content_id,active_role,
        security_context_version,program_sha256,revision,state),
    UPDATE (revision,state,security_context_version)
    ON nova_meta.config_assistant_ui_state TO nova_meta_web,nova_meta_agent,nova_meta_automation;
GRANT DELETE ON nova_meta.config_assistant_ui_state TO nova_meta_web;

CREATE POLICY config_i18n_messages_shared ON nova_meta.config_i18n_messages FOR ALL
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_bound()) WITH CHECK (nova_meta.apb_actor_bound());
GRANT SELECT,INSERT (language,message_id,version,text),UPDATE (version,text)
    ON nova_meta.config_i18n_messages TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- ── Access-control access: configuration changed by security administrators
-- through the web API only; Ranger and StarRocks remain the authority. ─────

DO $access_control$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_security_role_projections','config_security_policies',
                               'config_data_scope_assignments'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web USING (nova_meta.apb_actor_bound())',
            tbl||'_read',tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_web WITH CHECK (nova_meta.apb_security_admin())',
            tbl||'_insert',tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO nova_meta_web USING (nova_meta.apb_security_admin()) WITH CHECK (nova_meta.apb_security_admin())',
            tbl||'_update',tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO nova_meta_web USING (nova_meta.apb_security_admin())',
            tbl||'_delete',tbl);
        EXECUTE format('GRANT SELECT,DELETE ON nova_meta.%I TO nova_meta_web',tbl);
    END LOOP;
END
$access_control$;
GRANT INSERT (role_name,state,marker_exists,ranger_exists,message),
    UPDATE (state,marker_exists,ranger_exists,message,metadata_revision)
    ON nova_meta.config_security_role_projections TO nova_meta_web;
GRANT INSERT (policy_name,ranger_id,ranger_guid,state,desired_hash),
    UPDATE (ranger_id,ranger_guid,state,desired_hash,metadata_revision)
    ON nova_meta.config_security_policies TO nova_meta_web;
GRANT INSERT (assignment_id,principal,role_name,dimension_key,scope_values_json,state),
    UPDATE (scope_values_json,state,metadata_revision)
    ON nova_meta.config_data_scope_assignments TO nova_meta_web;

-- Business policy is read wherever decisions are evaluated and saved only by
-- ACCOUNTADMIN; revisions are never changed or removed.
CREATE POLICY config_business_policies_read ON nova_meta.config_business_policies FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.apb_actor_bound());
CREATE POLICY config_business_policies_insert ON nova_meta.config_business_policies FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.apb_account_admin());
GRANT SELECT ON nova_meta.config_business_policies
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;
GRANT INSERT (id,revision,definition) ON nova_meta.config_business_policies TO nova_meta_web;

-- ── Superset access ───────────────────────────────────────────────────────

-- Every Nova session is mapped to a Superset role on sign-in; only
-- ACCOUNTADMIN changes which roles author.
CREATE POLICY config_bi_author_roles_read ON nova_meta.config_bi_author_roles FOR SELECT
    TO nova_meta_web USING (nova_meta.apb_actor_bound());
CREATE POLICY config_bi_author_roles_insert ON nova_meta.config_bi_author_roles FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.apb_account_admin());
CREATE POLICY config_bi_author_roles_delete ON nova_meta.config_bi_author_roles FOR DELETE
    TO nova_meta_web USING (nova_meta.apb_account_admin());
CREATE POLICY config_bi_author_role_set_read ON nova_meta.config_bi_author_role_set FOR SELECT
    TO nova_meta_web USING (nova_meta.apb_actor_bound());
CREATE POLICY config_bi_author_role_set_insert ON nova_meta.config_bi_author_role_set FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.apb_account_admin());
CREATE POLICY config_bi_author_role_set_update ON nova_meta.config_bi_author_role_set FOR UPDATE
    TO nova_meta_web USING (nova_meta.apb_account_admin()) WITH CHECK (nova_meta.apb_account_admin());
GRANT SELECT,INSERT (role_name,created_by),DELETE ON nova_meta.config_bi_author_roles TO nova_meta_web;
GRANT SELECT,INSERT (singleton,revision),UPDATE (revision)
    ON nova_meta.config_bi_author_role_set TO nova_meta_web;

-- The publication journal belongs to the person who published, in the role
-- they published as. Dashboards themselves follow roles in Superset
-- (DASHBOARD_RBAC); nothing here shares a dashboard with a person.
CREATE POLICY config_dataviz_publications_own ON nova_meta.config_dataviz_publications
    FOR ALL TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_is(owner_name)) WITH CHECK (nova_meta.apb_actor_is(owner_name));
GRANT SELECT,
    INSERT (operation_id,owner_name,idempotency_scope,request_fingerprint,status,target_mode,
        dashboard_id,chart_id,dataset_id,artifact_id,created_objects,query_fingerprint,
        visualization_fingerprint,source_thread_id,source_message_id,source_content_id,
        strategy_id,revision_before,revision_after,business_context,active_role,
        security_context_version,error),
    UPDATE (status,target_mode,dashboard_id,chart_id,dataset_id,artifact_id,created_objects,
        query_fingerprint,visualization_fingerprint,strategy_id,revision_before,revision_after,
        business_context,error)
    ON nova_meta.config_dataviz_publications TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- Dashboard context needs the business context recorded for charts a viewer
-- already sees through Superset, whoever published them: one fixed projection
-- of successful publications, never the journal rows themselves.
CREATE FUNCTION nova_meta.dataviz_published_contexts(chart_ids bigint[])
RETURNS TABLE (chart_id bigint, business_context jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
       OR NOT nova_meta.apb_actor_bound() OR cardinality(chart_ids) > 500 THEN
        RAISE EXCEPTION 'Invalid publication context request' USING ERRCODE='42501';
    END IF;
    RETURN QUERY SELECT DISTINCT ON (p.chart_id) p.chart_id,p.business_context
        FROM nova_meta.config_dataviz_publications p
        WHERE p.status='SUCCEEDED' AND p.chart_id=ANY(chart_ids)
          AND jsonb_typeof(p.business_context)='object'
        ORDER BY p.chart_id,p.updated_at DESC NULLS LAST,p.operation_id DESC;
END
$body$;
-- The projection reads as the schema owner, which sees only successful
-- publications and only on behalf of a bound caller.
CREATE POLICY config_dataviz_publications_contexts ON nova_meta.config_dataviz_publications
    FOR SELECT TO nova_meta_owner
    USING (status='SUCCEEDED'
        AND session_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
        AND nova_meta.apb_actor_bound());
REVOKE ALL ON FUNCTION nova_meta.dataviz_published_contexts(bigint[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.dataviz_published_contexts(bigint[])
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- The Semantic View dataset map is written by the web publish/sync path and
-- read where dashboards are explained.
CREATE POLICY config_semantic_bi_datasets_read ON nova_meta.config_semantic_bi_datasets
    FOR SELECT TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.apb_actor_bound());
CREATE POLICY config_semantic_bi_datasets_write ON nova_meta.config_semantic_bi_datasets
    FOR INSERT TO nova_meta_web WITH CHECK (nova_meta.apb_actor_bound());
CREATE POLICY config_semantic_bi_datasets_update ON nova_meta.config_semantic_bi_datasets
    FOR UPDATE TO nova_meta_web
    USING (nova_meta.apb_actor_bound()) WITH CHECK (nova_meta.apb_actor_bound());
CREATE POLICY config_semantic_bi_datasets_delete ON nova_meta.config_semantic_bi_datasets
    FOR DELETE TO nova_meta_web USING (nova_meta.apb_actor_bound());
GRANT SELECT ON nova_meta.config_semantic_bi_datasets
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;
GRANT INSERT (view_id,dataset_name,bi_dataset_id,owner_name,version,status,message),
    UPDATE (bi_dataset_id,owner_name,version,status,message),DELETE
    ON nova_meta.config_semantic_bi_datasets TO nova_meta_web;

-- ── Migration access ──────────────────────────────────────────────────────

CREATE POLICY config_migration_sources_read ON nova_meta.config_migration_sources FOR SELECT
    TO nova_meta_web,nova_meta_ingestion USING (nova_meta.apb_actor_bound());
CREATE POLICY config_migration_sources_insert ON nova_meta.config_migration_sources FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.apb_actor_bound()
        AND current_setting('nova.principal_type',true)='USER');
GRANT SELECT ON nova_meta.config_migration_sources TO nova_meta_web,nova_meta_ingestion;
GRANT INSERT (id,name,host,port,username,secret_ref,comment,created_by)
    ON nova_meta.config_migration_sources TO nova_meta_web;

-- A job is queued, read and cleaned up by the person who asked for it; the
-- worker claims, advances and prunes every job.
CREATE POLICY config_migration_jobs_own ON nova_meta.config_migration_jobs FOR ALL
    TO nova_meta_web
    USING (nova_meta.apb_actor_is(actor)) WITH CHECK (nova_meta.apb_actor_is(actor));
CREATE POLICY config_migration_jobs_worker ON nova_meta.config_migration_jobs FOR SELECT
    TO nova_meta_ingestion USING (nova_meta.apb_actor_bound());
CREATE POLICY config_migration_jobs_advance ON nova_meta.config_migration_jobs FOR UPDATE
    TO nova_meta_ingestion USING (nova_meta.apb_actor_bound()) WITH CHECK (nova_meta.apb_actor_bound());
CREATE POLICY config_migration_jobs_prune ON nova_meta.config_migration_jobs FOR DELETE
    TO nova_meta_ingestion USING (nova_meta.apb_actor_bound()
        AND operation NOT IN ('execute','execute_batch')
        AND status IN ('succeeded','partial','failed','interrupted')
        AND finished_at<clock_timestamp()-interval '1 day');
GRANT SELECT,INSERT (id,operation,actor,session_fingerprint,active_role,security_context_version,
        source_name,request_json,status),DELETE
    ON nova_meta.config_migration_jobs TO nova_meta_web;
GRANT SELECT,DELETE,UPDATE (status,current_database,result_json,error_code,heartbeat_at,
        lease_generation)
    ON nova_meta.config_migration_jobs TO nova_meta_ingestion;

-- ── Usage access: appended by the AI function gateway, read by administrators

CREATE POLICY usage_llm_functions_append ON nova_meta.usage_llm_functions FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.apb_actor_bound());
CREATE POLICY usage_llm_functions_monitor ON nova_meta.usage_llm_functions FOR SELECT
    TO nova_meta_web USING (nova_meta.apb_administrator());
GRANT SELECT,INSERT (function_name,alias_name,model_name,status,request_count,prompt_tokens,
        completion_tokens,total_tokens,duration_ms)
    ON nova_meta.usage_llm_functions TO nova_meta_web;
GRANT USAGE ON SEQUENCE nova_meta.usage_llm_functions_usage_id TO nova_meta_web;

-- ── Statement guard: epoch admission, maintenance-only import ─────────────

CREATE FUNCTION nova_meta.apb_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Metadata import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation',
            'nova_meta_task','nova_meta_ingestion')
        AND TG_OP IN ('INSERT','UPDATE','DELETE') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;
DO $guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_assistant_threads','config_assistant_messages',
        'config_assistant_ui_state','config_i18n_messages','config_security_role_projections',
        'config_security_policies','config_data_scope_assignments','config_business_policies',
        'config_bi_author_roles','config_bi_author_role_set','config_dataviz_publications',
        'config_semantic_bi_datasets','config_migration_sources','config_migration_jobs',
        'usage_llm_functions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.apb_write_guard()',
            tbl||'_write_guard',tbl);
    END LOOP;
END
$guards$;

-- A credential reference names a secret-store entry (scheme://name or an AWS
-- Secrets Manager ARN); anything else may be the credential itself.
CREATE FUNCTION nova_meta.apb_secret_reference(ref text) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
    SELECT ref IS NULL OR ref=''
        OR (char_length(ref)<=1024 AND (
            ref ~ '^(aws|aws-sm|secretsmanager|aws_secrets_manager)://[A-Za-z0-9/_+=.@-]+$'
            OR ref ~ '^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$'))
$body$;
REVOKE ALL ON FUNCTION nova_meta.apb_secret_reference(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.apb_secret_reference(text) TO nova_meta_web;

-- A JSON document carries no credential-named member at any depth.
CREATE FUNCTION nova_meta.apb_credential_free(doc jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $body$
    SELECT NOT jsonb_path_exists(doc,
        'strict $.** ? (@.type() == "object") .keyvalue() ? (@.key like_regex "^(password|passwd|secret|token|api_key|private_key|credential)$" flag "i")')
$body$;
REVOKE ALL ON FUNCTION nova_meta.apb_credential_free(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.apb_credential_free(jsonb) TO nova_meta_web,nova_meta_ingestion;

-- ── Row rules ─────────────────────────────────────────────────────────────
-- Imported rows bypass them on insert; a later runtime change to an imported
-- row must still be a valid transition. One branch per table: a field a table
-- lacks never appears in its expressions.
CREATE FUNCTION nova_meta.apb_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE
    principal text := current_setting('nova.principal',true);
    active text := current_setting('nova.active_role',true);
    version text := current_setting('nova.security_context_version',true);
    latest bigint;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME='config_assistant_threads' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF btrim(NEW.title)='' OR btrim(NEW.thread_id)='' THEN
            RAISE EXCEPTION 'Invalid conversation' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.learning_enabled:=true;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.thread_id,NEW.user_name,NEW.workspace_file_id,NEW.agent_id,NEW.created_at,
                  NEW.provenance)
              IS DISTINCT FROM ROW(OLD.thread_id,OLD.user_name,OLD.workspace_file_id,
                  OLD.agent_id,OLD.created_at,OLD.provenance)
              OR (NEW.learning_enabled AND NOT OLD.learning_enabled) THEN
            -- Learning, once turned off for a conversation, stays off.
            RAISE EXCEPTION 'Invalid conversation change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_assistant_messages' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.role NOT IN ('user','assistant','tool') OR NEW.seq<0
               OR btrim(NEW.message_id)=''
               OR coalesce(NEW.prompt_tokens,0)<0 OR coalesce(NEW.completion_tokens,0)<0
               OR coalesce(NEW.total_tokens,0)<0
               OR jsonb_typeof(coalesce(NEW.steps,'[]'::jsonb))<>'array'
               OR jsonb_typeof(coalesce(NEW.attachments,'[]'::jsonb))<>'array'
               OR NOT EXISTS (SELECT 1 FROM nova_meta.config_assistant_threads t
                              WHERE t.thread_id=NEW.thread_id AND t.user_name=NEW.user_name) THEN
                RAISE EXCEPTION 'Invalid message' USING ERRCODE='23514';
            END IF;
            -- A stamped turn names the person and role that produced it.
            IF NEW.security_context IS NOT NULL
               AND (jsonb_typeof(NEW.security_context)<>'object'
                    OR NEW.security_context->>'principal' IS DISTINCT FROM principal
                    OR NEW.security_context->>'active_role' IS DISTINCT FROM active) THEN
                RAISE EXCEPTION 'A message is stamped with its own security context'
                    USING ERRCODE='23514';
            END IF;
            IF NEW.learning_state IS NOT NULL
               AND (NEW.learning_state<>'pending' OR NEW.role<>'user' OR NEW.agent_id IS NULL) THEN
                RAISE EXCEPTION 'Invalid learning state' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.feedback:=NULL; NEW.created_at:=clock_timestamp();
            RETURN NEW;
        END IF;
        -- Messages are append-only: the turn itself never changes. Feedback
        -- belongs to the owner's answers; learning progress to pending questions.
        IF ROW(NEW.message_id,NEW.thread_id,NEW.user_name,NEW.seq,NEW.role,NEW.content,
               NEW.created_at,NEW.agent_id,NEW.model_name,NEW.prompt_tokens,
               NEW.completion_tokens,NEW.total_tokens,NEW.instructions,NEW.security_context,
               NEW.attachments,NEW.provenance)
           IS DISTINCT FROM ROW(OLD.message_id,OLD.thread_id,OLD.user_name,OLD.seq,OLD.role,
               OLD.content,OLD.created_at,OLD.agent_id,OLD.model_name,OLD.prompt_tokens,
               OLD.completion_tokens,OLD.total_tokens,OLD.instructions,OLD.security_context,
               OLD.attachments,OLD.provenance) THEN
            RAISE EXCEPTION 'Messages are append-only' USING ERRCODE='23514';
        END IF;
        IF NEW.feedback IS DISTINCT FROM OLD.feedback
           AND (OLD.role<>'assistant' OR coalesce(NEW.feedback,'like') NOT IN ('like','dislike')) THEN
            RAISE EXCEPTION 'Invalid feedback' USING ERRCODE='23514';
        END IF;
        IF NEW.steps IS DISTINCT FROM OLD.steps
           AND (OLD.learning_state IS DISTINCT FROM 'pending' OR OLD.role<>'user'
                OR jsonb_typeof(NEW.steps)<>'array') THEN
            RAISE EXCEPTION 'Only a pending learning trace can change' USING ERRCODE='23514';
        END IF;
        IF NEW.learning_state IS DISTINCT FROM OLD.learning_state
           AND NOT (OLD.learning_state='pending' AND NEW.learning_state='complete') THEN
            RAISE EXCEPTION 'Invalid learning state' USING ERRCODE='23514';
        END IF;
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_assistant_ui_state' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        -- Interactive state follows the role of the session that saved it.
        IF jsonb_typeof(NEW.state)<>'object' OR NEW.active_role IS DISTINCT FROM active
           OR NEW.program_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'Invalid interactive state' USING ERRCODE='23514';
        END IF;
        NEW.security_context_version:=version::bigint;
        IF TG_OP='INSERT' THEN
            IF NEW.revision<>1 OR NOT EXISTS (SELECT 1 FROM nova_meta.config_assistant_messages m
                    WHERE m.message_id=NEW.message_id AND m.thread_id=NEW.thread_id
                      AND m.user_name=NEW.user_name AND m.role='assistant') THEN
                RAISE EXCEPTION 'Invalid interactive state' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime';
        ELSIF ROW(NEW.state_id,NEW.user_name,NEW.thread_id,NEW.message_id,NEW.content_id,
                  NEW.active_role,NEW.program_sha256,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.state_id,OLD.user_name,OLD.thread_id,OLD.message_id,
                  OLD.content_id,OLD.active_role,OLD.program_sha256,OLD.provenance)
              OR NEW.revision<>OLD.revision+1 THEN
            RAISE EXCEPTION 'Invalid interactive state revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_i18n_messages' THEN
        IF NEW.language !~ '^[a-z]{2,3}$' OR btrim(NEW.message_id)='' OR btrim(NEW.text)=''
           OR NEW.version<1 THEN
            RAISE EXCEPTION 'Invalid translation' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (NEW.version<OLD.version
                OR ROW(NEW.language,NEW.message_id) IS DISTINCT FROM ROW(OLD.language,OLD.message_id)) THEN
            RAISE EXCEPTION 'A translation never moves back a version' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_security_role_projections' THEN
        IF TG_OP='DELETE' THEN
            IF upper(OLD.role_name)='ACCOUNTADMIN' THEN
                RAISE EXCEPTION 'ACCOUNTADMIN is immutable' USING ERRCODE='42501';
            END IF;
            RETURN OLD;
        END IF;
        IF upper(NEW.role_name)='ACCOUNTADMIN' THEN
            RAISE EXCEPTION 'ACCOUNTADMIN is immutable' USING ERRCODE='42501';
        END IF;
        IF NEW.state NOT IN ('PENDING','PROVISIONING','PROPAGATING','ACTIVE','ERROR','DRIFTED',
                             'DELETING')
           OR NEW.role_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$' THEN
            RAISE EXCEPTION 'Invalid role projection' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.metadata_revision:=1; NEW.provenance:='runtime';
        ELSIF NEW.role_name IS DISTINCT FROM OLD.role_name OR NEW.provenance IS DISTINCT FROM OLD.provenance
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid role projection revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_role:=active; NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_security_policies' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.state NOT IN ('PENDING','PROVISIONING','PROPAGATING','ACTIVE','ERROR','DRIFTED',
                             'DELETING')
           OR btrim(NEW.policy_name)=''
           OR (NEW.desired_hash IS NOT NULL AND NEW.desired_hash !~ '^[0-9a-f]{64}$') THEN
            RAISE EXCEPTION 'Invalid security policy state' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.metadata_revision:=1; NEW.provenance:='runtime';
        ELSIF NEW.policy_name IS DISTINCT FROM OLD.policy_name
              OR NEW.provenance IS DISTINCT FROM OLD.provenance
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid security policy revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_role:=active; NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_data_scope_assignments' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        -- The id is the source's digest of principal, role and dimension.
        IF NEW.assignment_id IS DISTINCT FROM encode(sha256(convert_to(
                NEW.principal||chr(31)||NEW.role_name||chr(31)||NEW.dimension_key,'UTF8')),'hex')
           OR NEW.state NOT IN ('PENDING','PROVISIONING','PROPAGATING','ACTIVE','ERROR','DRIFTED',
                                'DELETING')
           OR btrim(NEW.principal)='' OR btrim(NEW.role_name)='' OR btrim(NEW.dimension_key)=''
           OR NOT pg_input_is_valid(NEW.scope_values_json,'jsonb') THEN
            RAISE EXCEPTION 'Invalid data scope assignment' USING ERRCODE='23514';
        END IF;
        IF jsonb_typeof(NEW.scope_values_json::jsonb)<>'array'
           OR jsonb_path_exists(NEW.scope_values_json::jsonb,'$[*] ? (@.type() != "string")') THEN
            RAISE EXCEPTION 'Invalid data scope values' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.metadata_revision:=1; NEW.provenance:='runtime';
        ELSIF ROW(NEW.assignment_id,NEW.principal,NEW.role_name,NEW.dimension_key,NEW.provenance)
              IS DISTINCT FROM ROW(OLD.assignment_id,OLD.principal,OLD.role_name,
                  OLD.dimension_key,OLD.provenance)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid data scope revision' USING ERRCODE='23514';
        END IF;
        NEW.updated_by:=principal; NEW.updated_role:=active; NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_business_policies' THEN
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'Business policy revisions are immutable' USING ERRCODE='42501';
        END IF;
        IF NEW.id !~ '^default:[0-9a-f]{32}$' OR jsonb_typeof(NEW.definition)<>'object'
           OR NEW.definition->>'id' IS DISTINCT FROM 'default'
           OR NEW.definition->>'revision' IS DISTINCT FROM NEW.revision::text THEN
            RAISE EXCEPTION 'Invalid business policy' USING ERRCODE='23514';
        END IF;
        SELECT max(p.revision) INTO latest FROM nova_meta.config_business_policies p
            WHERE p.policy_key='default';
        IF NEW.revision<>coalesce(latest,1)+1 THEN
            RAISE EXCEPTION 'A policy revision follows the one it was edited from'
                USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_by:=principal; NEW.created_role:=active;
        NEW.created_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_bi_author_role_set' THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'The author role set is never removed' USING ERRCODE='42501';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.revision<>1 THEN
                RAISE EXCEPTION 'Invalid author role set revision' USING ERRCODE='23514';
            END IF;
        ELSIF NEW.revision<>OLD.revision+1 THEN
            RAISE EXCEPTION 'Invalid author role set revision' USING ERRCODE='23514';
        END IF;
        NEW.changed_xact:=pg_current_xact_id(); NEW.updated_by:=principal;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_bi_author_roles' THEN
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_bi_author_role_set s
                       WHERE s.singleton AND s.changed_xact=pg_current_xact_id()) THEN
            RAISE EXCEPTION 'An author role change names the set revision it read'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF TG_OP='UPDATE' OR NEW.role_name !~ '^[A-Za-z_][A-Za-z0-9_]{0,127}$' THEN
            RAISE EXCEPTION 'Invalid author role' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_by:=principal; NEW.created_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_dataviz_publications' THEN
        IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'The publication journal is kept' USING ERRCODE='42501';
        END IF;
        IF NEW.status NOT IN ('VALIDATING','DATASET_READY','CHART_READY','ATTACHING','SUCCEEDED',
                              'FAILED_RETRYABLE','FAILED_FINAL','COMPENSATING',
                              'NEEDS_RECONCILIATION')
           OR coalesce(NEW.target_mode,'new') NOT IN ('new','existing')
           OR (NEW.business_context IS NOT NULL AND jsonb_typeof(NEW.business_context)<>'object') THEN
            RAISE EXCEPTION 'Invalid publication' USING ERRCODE='23514';
        END IF;
        -- An operation is continued only in the role and session version it began in.
        IF NEW.active_role IS DISTINCT FROM active
           OR NEW.security_context_version::text IS DISTINCT FROM version THEN
            RAISE EXCEPTION 'A publication belongs to the role that started it'
                USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'VALIDATING' OR btrim(NEW.idempotency_scope)=''
               OR btrim(NEW.request_fingerprint)='' THEN
                RAISE EXCEPTION 'A publication starts validating' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
            NEW.updated_at:=NEW.created_at;
            RETURN NEW;
        END IF;
        IF ROW(NEW.operation_id,NEW.owner_name,NEW.idempotency_scope,NEW.request_fingerprint,
               NEW.source_thread_id,NEW.source_message_id,NEW.source_content_id,NEW.created_at,
               NEW.provenance)
           IS DISTINCT FROM ROW(OLD.operation_id,OLD.owner_name,OLD.idempotency_scope,
               OLD.request_fingerprint,OLD.source_thread_id,OLD.source_message_id,
               OLD.source_content_id,OLD.created_at,OLD.provenance)
           OR OLD.status IN ('SUCCEEDED','FAILED_FINAL') THEN
            RAISE EXCEPTION 'Invalid publication transition' USING ERRCODE='23514';
        END IF;
        NEW.updated_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_semantic_bi_datasets' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        IF NEW.status NOT IN ('SYNCED','FAILED') OR btrim(NEW.view_id)=''
           OR btrim(NEW.dataset_name)='' OR coalesce(NEW.version,1)<1 THEN
            RAISE EXCEPTION 'Invalid semantic dataset' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND ROW(NEW.view_id,NEW.dataset_name)
                IS DISTINCT FROM ROW(OLD.view_id,OLD.dataset_name) THEN
            RAISE EXCEPTION 'Invalid semantic dataset change' USING ERRCODE='23514';
        END IF;
        -- The row names whoever last synced it.
        NEW.owner_name:=principal; NEW.provenance:='runtime'; NEW.synced_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_migration_sources' THEN
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'Migration sources are registered once' USING ERRCODE='42501';
        END IF;
        IF NOT nova_meta.apb_secret_reference(NEW.secret_ref) THEN
            -- The value is never echoed: it may be the credential itself.
            RAISE EXCEPTION 'A migration source stores a secret reference, never a credential'
                USING ERRCODE='23514';
        END IF;
        IF btrim(NEW.name)='' OR btrim(NEW.host)='' OR btrim(NEW.username)=''
           OR NEW.port NOT BETWEEN 1 AND 65535 THEN
            RAISE EXCEPTION 'Invalid migration source' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_by:=principal; NEW.created_at:=clock_timestamp();
        RETURN NEW;

    ELSIF TG_TABLE_NAME='config_migration_jobs' THEN
        IF TG_OP='DELETE' THEN
            IF OLD.status NOT IN ('succeeded','partial','failed','interrupted') THEN
                RAISE EXCEPTION 'An unfinished migration job is kept' USING ERRCODE='23514';
            END IF;
            RETURN OLD;
        END IF;
        IF NEW.status NOT IN ('queued','running','succeeded','partial','failed','interrupted') THEN
            RAISE EXCEPTION 'Invalid migration job' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            -- A job names the person, role and security version that queued it.
            IF NEW.status<>'queued' OR NEW.actor IS DISTINCT FROM principal
               OR NEW.active_role IS DISTINCT FROM active
               OR NEW.security_context_version::text IS DISTINCT FROM version
               OR NEW.operation !~ '^[a-z_]{1,32}$' OR btrim(NEW.source_name)=''
               OR NOT pg_input_is_valid(NEW.request_json,'jsonb') THEN
                RAISE EXCEPTION 'Invalid migration job' USING ERRCODE='23514';
            END IF;
            IF jsonb_typeof(NEW.request_json::jsonb)<>'object'
               OR NOT nova_meta.apb_credential_free(NEW.request_json::jsonb) THEN
                RAISE EXCEPTION 'A migration request carries no credential' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.lease_generation:=0; NEW.current_database:=NULL;
            NEW.result_json:=NULL; NEW.error_code:=NULL; NEW.created_at:=clock_timestamp();
            NEW.started_at:=NULL; NEW.heartbeat_at:=NULL; NEW.finished_at:=NULL;
            RETURN NEW;
        END IF;
        IF ROW(NEW.id,NEW.operation,NEW.actor,NEW.session_fingerprint,NEW.active_role,
               NEW.security_context_version,NEW.source_name,NEW.request_json,NEW.created_at,
               NEW.provenance)
           IS DISTINCT FROM ROW(OLD.id,OLD.operation,OLD.actor,OLD.session_fingerprint,
               OLD.active_role,OLD.security_context_version,OLD.source_name,OLD.request_json,
               OLD.created_at,OLD.provenance)
           OR OLD.status IN ('succeeded','partial','failed','interrupted')
           OR NEW.lease_generation<OLD.lease_generation THEN
            RAISE EXCEPTION 'Invalid migration job transition' USING ERRCODE='23514';
        END IF;
        IF OLD.status='queued' THEN
            -- A claim takes the next lease generation.
            IF NEW.status<>'running' OR NEW.lease_generation<>OLD.lease_generation+1 THEN
                RAISE EXCEPTION 'Invalid migration job claim' USING ERRCODE='23514';
            END IF;
            NEW.started_at:=clock_timestamp(); NEW.heartbeat_at:=NEW.started_at;
        ELSIF NEW.lease_generation<>OLD.lease_generation OR NEW.status='queued' THEN
            RAISE EXCEPTION 'Invalid migration job transition' USING ERRCODE='23514';
        END IF;
        IF NEW.result_json IS NOT NULL THEN
            IF NOT pg_input_is_valid(NEW.result_json,'jsonb') THEN
                RAISE EXCEPTION 'Invalid migration result' USING ERRCODE='23514';
            END IF;
            IF NOT nova_meta.apb_credential_free(NEW.result_json::jsonb) THEN
                RAISE EXCEPTION 'A migration result carries no credential' USING ERRCODE='23514';
            END IF;
        END IF;
        IF NEW.status IN ('succeeded','partial','failed','interrupted') THEN
            NEW.finished_at:=clock_timestamp(); NEW.current_database:=NULL;
        END IF;
        RETURN NEW;

    ELSE
        -- usage_llm_functions: appended once, never changed.
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'Usage records are append-only' USING ERRCODE='42501';
        END IF;
        IF btrim(NEW.function_name)='' OR btrim(NEW.status)='' OR NEW.request_count<1
           OR coalesce(NEW.prompt_tokens,0)<0 OR coalesce(NEW.completion_tokens,0)<0
           OR coalesce(NEW.total_tokens,0)<0 OR coalesce(NEW.duration_ms,0)<0 THEN
            RAISE EXCEPTION 'Invalid usage record' USING ERRCODE='23514';
        END IF;
        NEW.record_id:=gen_random_uuid();
        NEW.usage_id:=nextval('nova_meta.usage_llm_functions_usage_id');
        NEW.recorded_at:=clock_timestamp(); NEW.provenance:='runtime';
        RETURN NEW;
    END IF;
END
$body$;
DO $rows$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_assistant_threads','config_assistant_messages',
        'config_assistant_ui_state','config_i18n_messages','config_security_role_projections',
        'config_security_policies','config_data_scope_assignments','config_business_policies',
        'config_bi_author_roles','config_bi_author_role_set','config_dataviz_publications',
        'config_semantic_bi_datasets','config_migration_sources','config_migration_jobs',
        'usage_llm_functions'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.apb_row_guard()',
            tbl||'_row_guard',tbl);
    END LOOP;
END
$rows$;

-- ── Success audit for governed configuration ──────────────────────────────
-- Access-control projections, business policy, Superset author roles and
-- migration source registration are governed changes. Conversations, the
-- translation cache, the publication journal, the dataset map, job progress
-- and usage are not. Audit rows name objects only: never a credential
-- reference, scope values or a policy definition.
CREATE FUNCTION nova_meta.audit_apb_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity text; object_key jsonb; revision bigint := 1;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user<>'nova_meta_web' OR NOT nova_meta.actor_context_valid()
       OR TG_TABLE_SCHEMA<>'nova_meta'
       OR TG_TABLE_NAME NOT IN ('config_security_role_projections','config_security_policies',
           'config_data_scope_assignments','config_business_policies','config_bi_author_roles',
           'config_bi_author_role_set','config_migration_sources') THEN
        RAISE EXCEPTION 'Invalid governed metadata audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_security_role_projections' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.role_name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.role_name; revision:=NEW.metadata_revision; END IF;
        object_key:=jsonb_build_object('role_name',identity);
    ELSIF TG_TABLE_NAME='config_security_policies' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.policy_name; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.policy_name; revision:=NEW.metadata_revision; END IF;
        object_key:=jsonb_build_object('policy_name',identity);
    ELSIF TG_TABLE_NAME='config_data_scope_assignments' THEN
        IF TG_OP='DELETE' THEN identity:=OLD.assignment_id; revision:=OLD.metadata_revision;
        ELSE identity:=NEW.assignment_id; revision:=NEW.metadata_revision; END IF;
        object_key:=jsonb_build_object('assignment_id',identity);
    ELSIF TG_TABLE_NAME='config_business_policies' THEN
        identity:=NEW.id; revision:=NEW.revision;
        object_key:=jsonb_build_object('id',NEW.id,'revision',NEW.revision);
    ELSIF TG_TABLE_NAME='config_bi_author_roles' THEN
        -- Role rows are never updated; the set revision is audited with the set.
        IF TG_OP='DELETE' THEN identity:=OLD.role_name; ELSE identity:=NEW.role_name; END IF;
        object_key:=jsonb_build_object('role_name',identity);
    ELSIF TG_TABLE_NAME='config_bi_author_role_set' THEN
        identity:='bi_author_roles'; revision:=NEW.revision;
        object_key:=jsonb_build_object('set','bi_author_roles');
    ELSE
        identity:=NEW.id; object_key:=jsonb_build_object('id',NEW.id);
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,identity,
        (CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END),
        'SUCCESS','ALLOW',TG_TABLE_NAME,object_key,greatest(coalesce(revision,1),1));
    RETURN NULL;
END
$body$;
DO $audit$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_security_role_projections','config_security_policies',
        'config_data_scope_assignments','config_business_policies','config_bi_author_roles',
        'config_bi_author_role_set','config_migration_sources'] LOOP
        EXECUTE format('CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_apb_change()',
            tbl||'_audit',tbl);
    END LOOP;
END
$audit$;
