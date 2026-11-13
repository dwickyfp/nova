-- Studio agent metadata: governed definitions, run journals, Missions,
-- automations, knowledge, quality records and Studio objects.
-- Every row carries provenance. Imported source rows keep their identities,
-- owners, timestamps and gaps (for example runs whose events were never
-- written); the runtime rules below apply to rows written after cutover.
-- No table here stores a credential: the source screens agent configuration
-- for credential shapes, and connector credentials stay with the operator.

-- ── Governed definitions ─────────────────────────────────────────────────

CREATE TABLE nova_meta.config_agents (
    agent_id varchar(64) PRIMARY KEY CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) CHECK (octet_length(schema_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description text CHECK (char_length(description)<=65533),
    avatar varchar(256) CHECK (octet_length(avatar)<=256),
    color varchar(32) CHECK (octet_length(color)<=32),
    model_provider_id varchar(64) CHECK (octet_length(model_provider_id)<=64),
    model_name varchar(128) CHECK (octet_length(model_name)<=128),
    instructions_response text CHECK (char_length(instructions_response)<=65533),
    instructions_orchestration text CHECK (char_length(instructions_orchestration)<=65533),
    response_style varchar(64) CHECK (octet_length(response_style)<=64),
    sample_questions jsonb,
    budget_seconds integer,
    budget_tokens integer,
    tool_not_accessible varchar(16) CHECK (octet_length(tool_not_accessible)<=16),
    default_tools jsonb,
    default_skills jsonb,
    discoverable_skills jsonb,
    compiled_instructions jsonb,
    harness_mode varchar(16) CHECK (octet_length(harness_mode)<=16),
    policy varchar(32) CHECK (octet_length(policy)<=32),
    semantic_model_id varchar(64) CHECK (octet_length(semantic_model_id)<=64),
    -- Historical bindings: null and an explicit empty array stay distinct.
    semantic_model_ids jsonb,
    semantic_view_ids jsonb,
    visibility varchar(16) CHECK (octet_length(visibility)<=16),
    created_at timestamptz,
    updated_at timestamptz,
    resource_bindings jsonb,
    config_revision varchar(64) CHECK (octet_length(config_revision)<=64),
    budget_profile varchar(16) CHECK (octet_length(budget_profile)<=16),
    release_manifest_id varchar(64) CHECK (octet_length(release_manifest_id)<=64),
    harness_policy jsonb,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_agents_owner ON nova_meta.config_agents (owner_name,name);

CREATE TABLE nova_meta.config_agent_roles (
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    grant_type varchar(32) NOT NULL CHECK (octet_length(grant_type)<=32),
    verified_fingerprint varchar(64) CHECK (octet_length(verified_fingerprint)<=64),
    verified_at timestamptz,
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (agent_id,role_name)
);
CREATE INDEX config_agent_roles_role ON nova_meta.config_agent_roles (role_name,agent_id);

CREATE TABLE nova_meta.config_agent_skills (
    skill_id varchar(64) PRIMARY KEY CHECK (octet_length(skill_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description varchar(1024) CHECK (octet_length(description)<=1024),
    body text CHECK (char_length(body)<=65533),
    scope varchar(16) CHECK (octet_length(scope)<=16),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE UNIQUE INDEX config_agent_skills_runtime_name ON nova_meta.config_agent_skills
    (owner_name,name) WHERE provenance='runtime';

-- Long skill bodies are stored as immutable parts; the skill row points at a
-- complete part set only after every part exists.
CREATE TABLE nova_meta.config_skill_body_chunks (
    skill_id varchar(64) NOT NULL CHECK (octet_length(skill_id)<=64),
    revision varchar(64) NOT NULL CHECK (octet_length(revision)<=64),
    part_index integer NOT NULL CHECK (part_index>=0),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    content text NOT NULL CHECK (char_length(content)<=65533),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (skill_id,revision,part_index)
);

CREATE TABLE nova_meta.config_mcp_servers (
    server_id varchar(64) PRIMARY KEY CHECK (octet_length(server_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description text CHECK (char_length(description)<=65533),
    transport varchar(16) NOT NULL CHECK (octet_length(transport)<=16),
    endpoint varchar(1024) CHECK (octet_length(endpoint)<=1024),
    command varchar(1024) CHECK (octet_length(command)<=1024),
    args jsonb,
    is_active boolean NOT NULL DEFAULT true,
    last_status varchar(32) CHECK (octet_length(last_status)<=32),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_tools (
    tool_id varchar(64) PRIMARY KEY CHECK (octet_length(tool_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description text CHECK (char_length(description)<=65533),
    source varchar(160) NOT NULL CHECK (octet_length(source)<=160),
    input_schema jsonb,
    is_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- Discovery re-registers tools idempotently on (owner, source, name).
CREATE UNIQUE INDEX config_tools_runtime_key ON nova_meta.config_tools
    (owner_name,source,name) WHERE provenance='runtime';

CREATE TABLE nova_meta.config_custom_tools (
    tool_id varchar(64) PRIMARY KEY CHECK (octet_length(tool_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    name varchar(128) NOT NULL CHECK (octet_length(name)<=128),
    description text CHECK (char_length(description)<=65533),
    kind varchar(16) NOT NULL CHECK (octet_length(kind)<=16),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    function_name varchar(256) CHECK (octet_length(function_name)<=256),
    definition jsonb,
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE UNIQUE INDEX config_custom_tools_runtime_name ON nova_meta.config_custom_tools
    (owner_name,name) WHERE provenance='runtime';

-- Saved configuration snapshots and evaluated release manifests are
-- immutable: an existing ID is never rewritten (the same ID with different
-- content is a conflict). JSON is kept as text so stored fingerprints still
-- match the bytes they were computed from.
CREATE TABLE nova_meta.config_agent_versions (
    version_id varchar(64) PRIMARY KEY CHECK (octet_length(version_id)<=64),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    configuration text NOT NULL CHECK (char_length(configuration)<=1048576
        AND pg_input_is_valid(configuration,'json')),
    label varchar(256) NOT NULL CHECK (octet_length(label)<=256),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_agent_versions_agent ON nova_meta.config_agent_versions
    (agent_id,owner_name,created_at DESC);

CREATE TABLE nova_meta.config_agent_release_manifests (
    manifest_id varchar(64) PRIMARY KEY CHECK (octet_length(manifest_id)<=64),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    version_id varchar(64) NOT NULL CHECK (octet_length(version_id)<=64),
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_agent_automations (
    automation_id varchar(64) PRIMARY KEY CHECK (octet_length(automation_id)<=64),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    title varchar(256) NOT NULL CHECK (octet_length(title)<=256),
    prompt varchar(4000) NOT NULL CHECK (char_length(prompt)<=4000),
    schedule_kind varchar(16) NOT NULL CHECK (octet_length(schedule_kind)<=16),
    schedule_expr varchar(128) NOT NULL CHECK (octet_length(schedule_expr)<=128),
    timezone varchar(64) NOT NULL CHECK (octet_length(timezone)<=64),
    condition_json text CHECK (char_length(condition_json)<=65533 AND pg_input_is_valid(condition_json,'json')),
    delivery_json text CHECK (char_length(delivery_json)<=65533 AND pg_input_is_valid(delivery_json,'json')),
    enabled boolean NOT NULL,
    next_run_at timestamptz,
    last_run_at timestamptz,
    last_status varchar(64) CHECK (octet_length(last_status)<=64),
    last_thread_id varchar(64) CHECK (octet_length(last_thread_id)<=64),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    -- Each due claim advances the generation; only its holder records the run.
    run_generation bigint NOT NULL DEFAULT 0 CHECK (run_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_agent_automations_due ON nova_meta.config_agent_automations (next_run_at)
    WHERE enabled;
CREATE INDEX config_agent_automations_owner ON nova_meta.config_agent_automations
    (owner_name,agent_id);

CREATE TABLE nova_meta.config_studio_shares (
    share_id varchar(64) PRIMARY KEY CHECK (octet_length(share_id)<=64),
    object_type varchar(16) NOT NULL CHECK (octet_length(object_type)<=16),
    object_id varchar(64) NOT NULL CHECK (octet_length(object_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    target_type varchar(8) NOT NULL CHECK (octet_length(target_type)<=8),
    target_name varchar(128) NOT NULL CHECK (octet_length(target_name)<=128),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE UNIQUE INDEX config_studio_shares_runtime_target ON nova_meta.config_studio_shares
    (object_type,object_id,target_type,target_name) WHERE provenance='runtime';
CREATE INDEX config_studio_shares_target ON nova_meta.config_studio_shares
    (target_type,target_name,object_type,object_id);

-- ── Run journals ─────────────────────────────────────────────────────────

CREATE TABLE nova_meta.config_agent_runs (
    run_id varchar(64) PRIMARY KEY CHECK (octet_length(run_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    last_sequence bigint NOT NULL CHECK (last_sequence>=-1),
    started_at timestamptz,
    updated_at timestamptz,
    session_id varchar(128) CHECK (octet_length(session_id)<=128),
    security_version bigint,
    -- The producer's claim. A newer claim (or an interruption) fences every
    -- older producer out of the run and its event stream.
    claim_generation bigint NOT NULL DEFAULT 0 CHECK (claim_generation>=0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_agent_runs_thread ON nova_meta.config_agent_runs
    (owner_name,agent_id,thread_id,role_name,started_at DESC);

CREATE TABLE nova_meta.config_agent_run_events (
    run_id varchar(64) NOT NULL CHECK (octet_length(run_id)<=64),
    sequence bigint NOT NULL CHECK (sequence>=0),
    -- The exact SSE frame, byte for byte, as it was delivered.
    frame text NOT NULL CHECK (char_length(frame)<=1048576),
    created_at timestamptz,
    claim_generation bigint,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (run_id,sequence)
);

-- ── Missions, deliverables and Studio objects ────────────────────────────

CREATE TABLE nova_meta.config_studio_missions (
    mission_id varchar(64) PRIMARY KEY CHECK (octet_length(mission_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    session_id varchar(128) NOT NULL CHECK (octet_length(session_id)<=128),
    security_version bigint NOT NULL,
    revision bigint NOT NULL CHECK (revision>0),
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    created_at timestamptz,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_studio_missions_thread ON nova_meta.config_studio_missions
    (owner_name,role_name,thread_id,created_at DESC);

CREATE TABLE nova_meta.config_studio_deliverables (
    deliverable_id varchar(64) PRIMARY KEY CHECK (octet_length(deliverable_id)<=64),
    mission_id varchar(64) NOT NULL CHECK (octet_length(mission_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    session_id varchar(128) NOT NULL CHECK (octet_length(session_id)<=128),
    security_version bigint NOT NULL,
    request_digest varchar(64) NOT NULL CHECK (octet_length(request_digest)<=64),
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- One receipt per Mission request; a retry finds the existing receipt.
CREATE UNIQUE INDEX config_studio_deliverables_runtime_request
    ON nova_meta.config_studio_deliverables (mission_id,request_digest) WHERE provenance='runtime';

-- No current writer: imported rows are readable, nothing writes new ones.
CREATE TABLE nova_meta.config_studio_resources (
    resource_id varchar(64) PRIMARY KEY CHECK (octet_length(resource_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    session_id varchar(128) NOT NULL CHECK (octet_length(session_id)<=128),
    security_version bigint NOT NULL,
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    root_run_id varchar(64) NOT NULL CHECK (octet_length(root_run_id)<=64),
    message_id varchar(64) NOT NULL CHECK (octet_length(message_id)<=64),
    attachment_index integer NOT NULL,
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance='imported')
);
CREATE TABLE nova_meta.config_studio_resource_grants (
    resource_id varchar(64) NOT NULL CHECK (octet_length(resource_id)<=64),
    participant_id varchar(64) NOT NULL CHECK (octet_length(participant_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    session_id varchar(128) NOT NULL CHECK (octet_length(session_id)<=128),
    security_version bigint NOT NULL,
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    root_run_id varchar(64) NOT NULL CHECK (octet_length(root_run_id)<=64),
    grantor_participant_id varchar(64) NOT NULL CHECK (octet_length(grantor_participant_id)<=64),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance='imported'),
    PRIMARY KEY (resource_id,participant_id)
);

CREATE TABLE nova_meta.config_studio_artifacts (
    artifact_id varchar(64) PRIMARY KEY CHECK (octet_length(artifact_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    agent_id varchar(64) CHECK (octet_length(agent_id)<=64),
    thread_id varchar(64) CHECK (octet_length(thread_id)<=64),
    title varchar(256) NOT NULL CHECK (octet_length(title)<=256),
    artifact_type varchar(16) NOT NULL CHECK (octet_length(artifact_type)<=16),
    -- Stored provenance only; this store never executes it.
    sql_text text NOT NULL CHECK (char_length(sql_text)<=65533),
    database_name varchar(128) CHECK (octet_length(database_name)<=128),
    schema_name varchar(128) CHECK (octet_length(schema_name)<=128),
    bi_kind varchar(16) CHECK (octet_length(bi_kind)<=16),
    bi_object_id bigint,
    bi_dataset_id bigint,
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_studio_artifacts_owner ON nova_meta.config_studio_artifacts
    (owner_name,updated_at DESC);

-- Legacy tile dashboards: kept until their verified conversion, never written.
CREATE TABLE nova_meta.config_studio_dashboards (
    dashboard_id varchar(64) PRIMARY KEY CHECK (octet_length(dashboard_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    title varchar(256) NOT NULL CHECK (octet_length(title)<=256),
    layout_json text CHECK (char_length(layout_json)<=1048576 AND pg_input_is_valid(layout_json,'json')),
    created_at timestamptz,
    updated_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance='imported')
);

CREATE TABLE nova_meta.config_deep_research_runs (
    run_id varchar(64) PRIMARY KEY CHECK (octet_length(run_id)<=64),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    thread_id varchar(64) NOT NULL CHECK (octet_length(thread_id)<=64),
    question varchar(4000) NOT NULL CHECK (char_length(question)<=4000),
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    plan_json text CHECK (char_length(plan_json)<=1048576 AND pg_input_is_valid(plan_json,'json')),
    progress_json text CHECK (char_length(progress_json)<=1048576 AND pg_input_is_valid(progress_json,'json')),
    report text CHECK (char_length(report)<=1048576),
    created_at timestamptz,
    updated_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

CREATE TABLE nova_meta.config_verified_query_candidates (
    candidate_id varchar(64) PRIMARY KEY CHECK (octet_length(candidate_id)<=64),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    proposed_by varchar(128) NOT NULL CHECK (octet_length(proposed_by)<=128),
    view_id varchar(128) NOT NULL CHECK (octet_length(view_id)<=128),
    view_version integer,
    question varchar(2000) NOT NULL CHECK (char_length(question)<=2000),
    semantic_plan text NOT NULL CHECK (char_length(semantic_plan)<=1048576
        AND pg_input_is_valid(semantic_plan,'json')),
    plan_sha256 varchar(64) NOT NULL CHECK (octet_length(plan_sha256)<=64),
    source_thread_id varchar(64) NOT NULL CHECK (octet_length(source_thread_id)<=64),
    source_message_id varchar(64) NOT NULL CHECK (octet_length(source_message_id)<=64),
    status varchar(16) NOT NULL CHECK (octet_length(status)<=16),
    decided_by varchar(128) CHECK (octet_length(decided_by)<=128),
    created_at timestamptz,
    decided_at timestamptz,
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
-- The source deduplicated by read-then-insert; a pending or adopted plan is
-- now unique per agent and View in the transaction (Wave F).
CREATE UNIQUE INDEX config_verified_query_candidates_runtime_active
    ON nova_meta.config_verified_query_candidates (agent_id,view_id,plan_sha256)
    WHERE provenance='runtime' AND status IN ('pending','approved');

CREATE TABLE nova_meta.config_agent_rule_proposals (
    proposal_id varchar(64) PRIMARY KEY CHECK (octet_length(proposal_id)<=64),
    owner_name varchar(128) NOT NULL CHECK (octet_length(owner_name)<=128),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    memory_id varchar(64) NOT NULL CHECK (octet_length(memory_id)<=64),
    semantic_model_id varchar(64) NOT NULL CHECK (octet_length(semantic_model_id)<=64),
    metric_name varchar(160) NOT NULL CHECK (octet_length(metric_name)<=160),
    prior_expression text NOT NULL CHECK (char_length(prior_expression)<=65533),
    proposed_expression text NOT NULL CHECK (char_length(proposed_expression)<=65533),
    prior_fingerprint varchar(128) NOT NULL CHECK (octet_length(prior_fingerprint)<=128),
    proposed_fingerprint varchar(128) NOT NULL CHECK (octet_length(proposed_fingerprint)<=128),
    status varchar(32) NOT NULL CHECK (octet_length(status)<=32),
    previewed_at timestamptz,
    reviewed_by varchar(128) CHECK (octet_length(reviewed_by)<=128),
    created_at timestamptz,
    updated_at timestamptz,
    proposal_kind varchar(32) DEFAULT 'metric' CHECK (octet_length(proposal_kind)<=32),
    details text CHECK (char_length(details)<=1048576 AND pg_input_is_valid(details,'json')),
    metadata_revision bigint NOT NULL DEFAULT 1 CHECK (metadata_revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);

-- ── Knowledge ────────────────────────────────────────────────────────────

-- Memory has a mutable projection, immutable revisions and separate
-- evidence. ``revision`` names the revision the projection reflects; a
-- projection change commits only together with that revision row.
CREATE TABLE nova_meta.config_agent_memories (
    memory_id varchar(64) PRIMARY KEY CHECK (octet_length(memory_id)<=64),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    fact_key varchar(160) NOT NULL CHECK (octet_length(fact_key)<=160),
    fact text NOT NULL CHECK (char_length(fact)<=65533),
    source_quote varchar(512) NOT NULL CHECK (char_length(source_quote)<=512),
    source_thread_id varchar(64) NOT NULL CHECK (octet_length(source_thread_id)<=64),
    created_at timestamptz,
    updated_at timestamptz,
    revision bigint CHECK (revision>0),
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported'))
);
CREATE INDEX config_agent_memories_scope ON nova_meta.config_agent_memories
    (user_name,role_name,agent_id,fact_key);

CREATE TABLE nova_meta.config_agent_memory_revisions (
    memory_id varchar(64) NOT NULL CHECK (octet_length(memory_id)<=64),
    revision bigint NOT NULL CHECK (revision>0),
    operation_id varchar(32) NOT NULL CHECK (octet_length(operation_id)<=32),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (memory_id,revision,operation_id)
);
-- Imported same-revision forks are kept; a new runtime revision cannot fork.
CREATE UNIQUE INDEX config_agent_memory_revisions_runtime
    ON nova_meta.config_agent_memory_revisions (memory_id,revision) WHERE provenance='runtime';

CREATE TABLE nova_meta.config_agent_memory_evidence (
    memory_id varchar(64) NOT NULL CHECK (octet_length(memory_id)<=64),
    evidence_id varchar(32) NOT NULL CHECK (octet_length(evidence_id)<=32),
    user_name varchar(128) NOT NULL CHECK (octet_length(user_name)<=128),
    agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
    role_name varchar(128) NOT NULL CHECK (octet_length(role_name)<=128),
    payload text NOT NULL CHECK (char_length(payload)<=1048576 AND pg_input_is_valid(payload,'json')),
    created_at timestamptz,
    provenance varchar(16) NOT NULL DEFAULT 'imported' CHECK (provenance IN ('runtime','imported')),
    PRIMARY KEY (memory_id,evidence_id)
);

-- Quality records share one revision-store shape: (id, revision,
-- operation_id) keeps every imported operation, including same-revision forks
-- that readers must refuse, and binds each record to its security scope.
DO $quality$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons'] LOOP
        EXECUTE format($ddl$
            CREATE TABLE nova_meta.%I (
                id varchar(64) NOT NULL CHECK (octet_length(id)<=64),
                revision bigint NOT NULL CHECK (revision>0),
                operation_id varchar(32) NOT NULL CHECK (octet_length(operation_id)<=32),
                agent_id varchar(64) NOT NULL CHECK (octet_length(agent_id)<=64),
                principal varchar(128) NOT NULL CHECK (octet_length(principal)<=128),
                active_role varchar(128) NOT NULL CHECK (octet_length(active_role)<=128),
                security_context_version bigint NOT NULL,
                payload text NOT NULL CHECK (char_length(payload)<=1048576
                    AND pg_input_is_valid(payload,'json')),
                created_at timestamptz,
                provenance varchar(16) NOT NULL DEFAULT 'imported'
                    CHECK (provenance IN ('runtime','imported')),
                PRIMARY KEY (id,revision,operation_id)
            )$ddl$, tbl);
        EXECUTE format('CREATE UNIQUE INDEX %I ON nova_meta.%I (id,revision) WHERE provenance=''runtime''',
            tbl||'_runtime_revision', tbl);
        EXECUTE format('CREATE INDEX %I ON nova_meta.%I (agent_id,principal,active_role,id,revision DESC)',
            tbl||'_scope', tbl);
    END LOOP;
END
$quality$;

-- ── Row-level security ───────────────────────────────────────────────────

DO $rls$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agents','config_agent_roles','config_agent_skills',
            'config_skill_body_chunks','config_mcp_servers','config_tools','config_custom_tools',
            'config_agent_versions','config_agent_release_manifests','config_agent_automations',
            'config_studio_shares','config_agent_runs','config_agent_run_events',
            'config_studio_missions','config_studio_deliverables','config_studio_resources',
            'config_studio_resource_grants','config_studio_artifacts','config_studio_dashboards',
            'config_deep_research_runs','config_verified_query_candidates',
            'config_agent_rule_proposals','config_agent_memories','config_agent_memory_revisions',
            'config_agent_memory_evidence','config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons'] LOOP
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            tbl||'_import', tbl);
    END LOOP;
END
$rls$;

-- Agent metadata is used by the web API, the interactive agent workload and
-- the automation worker, always through a bound actor; no other workload.
CREATE FUNCTION nova_meta.agent_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT session_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
        AND nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
CREATE FUNCTION nova_meta.agent_is_principal(owner text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.agent_actor_bound()
        AND owner IS NOT DISTINCT FROM current_setting('nova.principal',true)
$body$;
CREATE FUNCTION nova_meta.agent_in_scope(owner text, role text) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.agent_is_principal(owner)
        AND role IS NOT DISTINCT FROM current_setting('nova.active_role',true)
$body$;
-- The automation worker's global due scan and run bookkeeping.
CREATE FUNCTION nova_meta.agent_automation_service() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT session_user='nova_meta_automation' AND nova_meta.agent_actor_bound()
        AND current_setting('nova.principal_type',true)='SERVICE'
$body$;
REVOKE ALL ON FUNCTION nova_meta.agent_actor_bound(),nova_meta.agent_is_principal(text),
    nova_meta.agent_in_scope(text,text),nova_meta.agent_automation_service() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.agent_actor_bound(),nova_meta.agent_is_principal(text),
    nova_meta.agent_in_scope(text,text),nova_meta.agent_automation_service()
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- Agents: the owner, or a caller whose ACTIVE role holds a grant on the agent.
-- Assigned-but-inactive roles never widen access. Access verification itself
-- (Ranger) stays with the service before a run.
CREATE POLICY config_agents_read ON nova_meta.config_agents FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agent_roles r WHERE r.agent_id=config_agents.agent_id
            AND r.owner_name=config_agents.owner_name
            AND r.role_name=current_setting('nova.active_role',true))));
CREATE POLICY config_agents_insert ON nova_meta.config_agents FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agents_update ON nova_meta.config_agents FOR UPDATE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name)) WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agents_delete ON nova_meta.config_agents FOR DELETE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name));

CREATE POLICY config_agent_roles_read ON nova_meta.config_agent_roles FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound()
        AND role_name=current_setting('nova.active_role',true)));
CREATE POLICY config_agent_roles_insert ON nova_meta.config_agent_roles FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_roles_update ON nova_meta.config_agent_roles FOR UPDATE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name)) WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_roles_delete ON nova_meta.config_agent_roles FOR DELETE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name));

-- A shared agent's run loads the owner's skills and custom tools it names,
-- and nothing else of the owner's.
CREATE POLICY config_agent_skills_read ON nova_meta.config_agent_skills FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agents a WHERE a.owner_name=config_agent_skills.owner_name
            AND (coalesce(a.default_skills,'[]'::jsonb) ? config_agent_skills.name
                OR coalesce(a.discoverable_skills,'[]'::jsonb) ? config_agent_skills.name))));
CREATE POLICY config_agent_skills_insert ON nova_meta.config_agent_skills FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_skills_update ON nova_meta.config_agent_skills FOR UPDATE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name)) WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_skills_delete ON nova_meta.config_agent_skills FOR DELETE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_skill_body_chunks_read ON nova_meta.config_skill_body_chunks FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agent_skills s WHERE s.skill_id=config_skill_body_chunks.skill_id
            AND s.owner_name=config_skill_body_chunks.owner_name)));
CREATE POLICY config_skill_body_chunks_insert ON nova_meta.config_skill_body_chunks FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_skill_body_chunks_delete ON nova_meta.config_skill_body_chunks FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));

CREATE POLICY config_custom_tools_read ON nova_meta.config_custom_tools FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agents a WHERE a.owner_name=config_custom_tools.owner_name
            AND coalesce(a.default_tools,'[]'::jsonb) ? config_custom_tools.name)));
CREATE POLICY config_custom_tools_insert ON nova_meta.config_custom_tools FOR INSERT TO nova_meta_web
    WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_custom_tools_update ON nova_meta.config_custom_tools FOR UPDATE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name)) WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_custom_tools_delete ON nova_meta.config_custom_tools FOR DELETE TO nova_meta_web
    USING (nova_meta.agent_is_principal(owner_name));

-- MCP registrations and tool records are the owner's own.
DO $owner_only$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_mcp_servers','config_tools'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.agent_is_principal(owner_name))', tbl||'_read', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_web WITH CHECK (nova_meta.agent_is_principal(owner_name))', tbl||'_insert', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR UPDATE TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name)) WITH CHECK (nova_meta.agent_is_principal(owner_name))', tbl||'_update', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR DELETE TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name))', tbl||'_delete', tbl);
    END LOOP;
END
$owner_only$;

CREATE POLICY config_agent_versions_read ON nova_meta.config_agent_versions FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_versions_insert ON nova_meta.config_agent_versions FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.agent_is_principal(owner_name));
-- A shared agent's run loads the release manifest the agent points at.
CREATE POLICY config_agent_release_manifests_read ON nova_meta.config_agent_release_manifests
    FOR SELECT TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agents a
        WHERE a.release_manifest_id=config_agent_release_manifests.manifest_id
            AND a.agent_id=config_agent_release_manifests.agent_id)));
CREATE POLICY config_agent_release_manifests_insert ON nova_meta.config_agent_release_manifests
    FOR INSERT TO nova_meta_web WITH CHECK (nova_meta.agent_is_principal(owner_name));

CREATE POLICY config_agent_automations_read ON nova_meta.config_agent_automations FOR SELECT
    TO nova_meta_web,nova_meta_automation
    USING (nova_meta.agent_is_principal(owner_name) OR nova_meta.agent_automation_service());
-- An automation runs as its stored role, so it is created and redefined only
-- while that role is active.
CREATE POLICY config_agent_automations_insert ON nova_meta.config_agent_automations FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_agent_automations_update ON nova_meta.config_agent_automations FOR UPDATE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name))
    WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_agent_automations_schedule ON nova_meta.config_agent_automations FOR UPDATE
    TO nova_meta_automation USING (nova_meta.agent_automation_service())
    WITH CHECK (nova_meta.agent_automation_service());
CREATE POLICY config_agent_automations_delete ON nova_meta.config_agent_automations FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));

-- Shares follow the active security scope (``active_grants``). The source's
-- wider assigned-role listing is not reproduced as an RLS rule (Wave F).
CREATE POLICY config_studio_shares_read ON nova_meta.config_studio_shares FOR SELECT
    TO nova_meta_web,nova_meta_agent
    USING (nova_meta.agent_is_principal(owner_name) OR (nova_meta.agent_actor_bound() AND (
        (target_type='user' AND target_name=current_setting('nova.principal',true))
        OR (target_type='role' AND target_name=current_setting('nova.active_role',true)))));
CREATE POLICY config_studio_shares_insert ON nova_meta.config_studio_shares FOR INSERT
    TO nova_meta_web WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_shares_delete ON nova_meta.config_studio_shares FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));

-- Runs are readable by their owner (thread deletion spans roles); every write
-- needs the run's role to be the active one, and the row guard binds the
-- login, security version and claim generation.
CREATE POLICY config_agent_runs_read ON nova_meta.config_agent_runs FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_runs_insert ON nova_meta.config_agent_runs FOR INSERT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_agent_runs_update ON nova_meta.config_agent_runs FOR UPDATE
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_in_scope(owner_name,role_name))
    WITH CHECK (nova_meta.agent_is_principal(owner_name)
        AND role_name IN (current_setting('nova.active_role',true),'__role_changed__'));
CREATE POLICY config_agent_runs_delete ON nova_meta.config_agent_runs FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_agent_run_events_read ON nova_meta.config_agent_run_events FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_actor_bound() AND EXISTS (SELECT 1 FROM nova_meta.config_agent_runs r
        WHERE r.run_id=config_agent_run_events.run_id));
CREATE POLICY config_agent_run_events_insert ON nova_meta.config_agent_run_events FOR INSERT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    WITH CHECK (nova_meta.agent_actor_bound() AND EXISTS (SELECT 1 FROM nova_meta.config_agent_runs r
        WHERE r.run_id=config_agent_run_events.run_id
            AND r.role_name=current_setting('nova.active_role',true)));
CREATE POLICY config_agent_run_events_delete ON nova_meta.config_agent_run_events FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_actor_bound() AND EXISTS (
        SELECT 1 FROM nova_meta.config_agent_runs r WHERE r.run_id=config_agent_run_events.run_id));

-- Missions, deliverables and delegated resources belong to an owner and role.
CREATE POLICY config_studio_missions_read ON nova_meta.config_studio_missions FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_missions_insert ON nova_meta.config_studio_missions FOR INSERT
    TO nova_meta_web,nova_meta_agent WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_missions_update ON nova_meta.config_studio_missions FOR UPDATE
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name))
    WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_deliverables_read ON nova_meta.config_studio_deliverables FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_deliverables_insert ON nova_meta.config_studio_deliverables FOR INSERT
    TO nova_meta_web,nova_meta_agent WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_resources_read ON nova_meta.config_studio_resources FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_studio_resource_grants_read ON nova_meta.config_studio_resource_grants
    FOR SELECT TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name));

CREATE POLICY config_studio_artifacts_read ON nova_meta.config_studio_artifacts FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_artifacts_insert ON nova_meta.config_studio_artifacts FOR INSERT
    TO nova_meta_web,nova_meta_agent WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_artifacts_update ON nova_meta.config_studio_artifacts FOR UPDATE
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_is_principal(owner_name))
    WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_artifacts_delete ON nova_meta.config_studio_artifacts FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_dashboards_read ON nova_meta.config_studio_dashboards FOR SELECT
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_studio_dashboards_delete ON nova_meta.config_studio_dashboards FOR DELETE
    TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name));

CREATE POLICY config_deep_research_runs_read ON nova_meta.config_deep_research_runs FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_deep_research_runs_insert ON nova_meta.config_deep_research_runs FOR INSERT
    TO nova_meta_web,nova_meta_agent WITH CHECK (nova_meta.agent_is_principal(owner_name));
CREATE POLICY config_deep_research_runs_update ON nova_meta.config_deep_research_runs FOR UPDATE
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_is_principal(owner_name))
    WITH CHECK (nova_meta.agent_is_principal(owner_name));

-- A candidate is visible to the agent owner, who decides it, and to its proposer.
CREATE POLICY config_verified_query_candidates_read ON nova_meta.config_verified_query_candidates
    FOR SELECT TO nova_meta_web,nova_meta_agent
    USING (nova_meta.agent_is_principal(owner_name) OR nova_meta.agent_is_principal(proposed_by));
CREATE POLICY config_verified_query_candidates_insert ON nova_meta.config_verified_query_candidates
    FOR INSERT TO nova_meta_web,nova_meta_agent
    WITH CHECK (nova_meta.agent_is_principal(proposed_by));
CREATE POLICY config_verified_query_candidates_update ON nova_meta.config_verified_query_candidates
    FOR UPDATE TO nova_meta_web USING (nova_meta.agent_is_principal(owner_name))
    WITH CHECK (nova_meta.agent_is_principal(owner_name));

CREATE POLICY config_agent_rule_proposals_read ON nova_meta.config_agent_rule_proposals FOR SELECT
    TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_agent_rule_proposals_insert ON nova_meta.config_agent_rule_proposals
    FOR INSERT TO nova_meta_web,nova_meta_agent WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));
CREATE POLICY config_agent_rule_proposals_update ON nova_meta.config_agent_rule_proposals
    FOR UPDATE TO nova_meta_web USING (nova_meta.agent_in_scope(owner_name,role_name))
    WITH CHECK (nova_meta.agent_in_scope(owner_name,role_name));

-- Memory, its revisions and evidence stay in the owner's role scope. Shared
-- domain memory is a service decision with semantic access checks, not an
-- RLS widening.
DO $knowledge$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_memories','config_agent_memory_revisions',
                               'config_agent_memory_evidence'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.agent_in_scope(user_name,role_name))', tbl||'_read', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_web,nova_meta_agent,nova_meta_automation WITH CHECK (nova_meta.agent_in_scope(user_name,role_name))', tbl||'_insert', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_web,nova_meta_agent USING (nova_meta.agent_in_scope(principal,active_role))', tbl||'_read', tbl);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO nova_meta_web WITH CHECK (nova_meta.agent_in_scope(principal,active_role))', tbl||'_insert', tbl);
    END LOOP;
END
$knowledge$;
CREATE POLICY config_agent_memories_update ON nova_meta.config_agent_memories FOR UPDATE
    TO nova_meta_web,nova_meta_agent,nova_meta_automation
    USING (nova_meta.agent_in_scope(user_name,role_name))
    WITH CHECK (nova_meta.agent_in_scope(user_name,role_name));

-- ── Grants ───────────────────────────────────────────────────────────────

-- Column grants: server-owned columns (provenance, timestamps, revisions,
-- bound scope) are never written by a workload directly; the row guards set
-- them. ``ins`` / ``upd`` name the writable columns.
DO $grants$
DECLARE item record; who text;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_agents','web,agent,automation','web',
         'agent_id,owner_name,database_name,schema_name,name,description,avatar,color,model_provider_id,model_name,instructions_response,instructions_orchestration,response_style,sample_questions,budget_seconds,budget_tokens,tool_not_accessible,default_tools,default_skills,discoverable_skills,compiled_instructions,harness_mode,policy,semantic_model_id,semantic_model_ids,semantic_view_ids,visibility,resource_bindings,config_revision,budget_profile,release_manifest_id,harness_policy',
         'web','database_name,schema_name,name,description,avatar,color,model_provider_id,model_name,instructions_response,instructions_orchestration,response_style,sample_questions,budget_seconds,budget_tokens,tool_not_accessible,default_tools,default_skills,discoverable_skills,compiled_instructions,harness_mode,policy,semantic_model_id,semantic_model_ids,semantic_view_ids,visibility,resource_bindings,config_revision,budget_profile,release_manifest_id,harness_policy,metadata_revision',
         true),
        ('config_agent_roles','web,agent,automation','web','agent_id,role_name,owner_name,grant_type',
         'web','grant_type,verified_fingerprint,verified_at,metadata_revision',true),
        ('config_agent_skills','web,agent,automation','web','skill_id,owner_name,name,description,body,scope',
         'web','name,description,body,scope,metadata_revision',true),
        ('config_skill_body_chunks','web,agent,automation','web','skill_id,revision,part_index,owner_name,content',
         '',NULL,true),
        ('config_mcp_servers','web,agent,automation','web','server_id,owner_name,name,description,transport,endpoint,command,args,is_active',
         'web','name,description,transport,endpoint,command,args,is_active,last_status,metadata_revision',true),
        ('config_tools','web,agent,automation','web','tool_id,owner_name,name,description,source,input_schema,is_enabled',
         'web','description,input_schema,is_enabled,metadata_revision',true),
        ('config_custom_tools','web,agent,automation','web','tool_id,owner_name,name,description,kind,database_name,function_name,definition',
         'web','name,description,kind,database_name,function_name,definition,metadata_revision',true),
        ('config_agent_versions','web,agent,automation','web','version_id,agent_id,owner_name,configuration,label',
         '',NULL,false),
        ('config_agent_release_manifests','web,agent,automation','web','manifest_id,agent_id,owner_name,version_id,payload',
         '',NULL,false),
        ('config_agent_automations','web,automation','web','automation_id,agent_id,owner_name,role_name,title,prompt,schedule_kind,schedule_expr,timezone,condition_json,delivery_json,enabled,next_run_at',
         'web','role_name,title,prompt,schedule_kind,schedule_expr,timezone,condition_json,delivery_json,enabled,next_run_at,metadata_revision',true),
        ('config_studio_shares','web,agent','web','share_id,object_type,object_id,owner_name,target_type,target_name',
         '',NULL,true),
        ('config_agent_runs','web,agent,automation','web,agent,automation','run_id,owner_name,agent_id,thread_id,role_name,status',
         'web,agent,automation','role_name,status,last_sequence,session_id,security_version,claim_generation',false),
        ('config_agent_run_events','web,agent,automation','web,agent,automation','run_id,sequence,frame,claim_generation',
         '',NULL,false),
        ('config_studio_missions','web,agent','web,agent','mission_id,owner_name,thread_id,role_name,revision,payload',
         'web,agent','revision,payload,session_id,security_version',false),
        ('config_studio_deliverables','web,agent','web,agent','deliverable_id,mission_id,owner_name,role_name,request_digest,payload',
         '',NULL,false),
        ('config_studio_resources','web,agent','',NULL,'',NULL,false),
        ('config_studio_resource_grants','web,agent','',NULL,'',NULL,false),
        ('config_studio_artifacts','web,agent','web,agent','artifact_id,owner_name,agent_id,thread_id,title,artifact_type,sql_text,database_name,schema_name,bi_kind,bi_object_id,bi_dataset_id',
         'web,agent','title,sql_text,database_name,schema_name,bi_kind,bi_object_id,bi_dataset_id,metadata_revision',false),
        ('config_studio_dashboards','web','',NULL,'',NULL,false),
        ('config_deep_research_runs','web,agent','web,agent','run_id,agent_id,owner_name,thread_id,question,status',
         'web,agent','status,plan_json,progress_json,report,metadata_revision',false),
        ('config_verified_query_candidates','web,agent','web,agent','candidate_id,agent_id,owner_name,proposed_by,view_id,view_version,question,semantic_plan,plan_sha256,source_thread_id,source_message_id,status',
         'web','status,decided_by,metadata_revision',false),
        ('config_agent_rule_proposals','web,agent','web,agent','proposal_id,owner_name,agent_id,role_name,memory_id,semantic_model_id,metric_name,prior_expression,proposed_expression,prior_fingerprint,proposed_fingerprint,status,proposal_kind,details',
         'web','status,previewed_at,reviewed_by,metadata_revision',false),
        ('config_agent_memories','web,agent,automation','web,agent,automation','memory_id,user_name,agent_id,role_name,fact_key,fact,source_quote,source_thread_id,revision',
         'web,agent,automation','fact_key,fact,source_quote,source_thread_id,revision',false),
        ('config_agent_memory_revisions','web,agent,automation','web,agent,automation','memory_id,revision,operation_id,user_name,agent_id,role_name,payload',
         '',NULL,false),
        ('config_agent_memory_evidence','web,agent,automation','web,agent,automation','memory_id,evidence_id,user_name,agent_id,role_name,payload',
         '',NULL,false)
    ) AS t(tbl,readers,inserters,ins,updaters,upd,deletable) LOOP
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.readers,',')) r));
        IF item.inserters<>'' THEN
            EXECUTE format('GRANT INSERT (%s) ON nova_meta.%I TO %s', item.ins, item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        END IF;
        IF item.updaters<>'' THEN
            EXECUTE format('GRANT UPDATE (%s) ON nova_meta.%I TO %s', item.upd, item.tbl,
                (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.updaters,',')) r));
        END IF;
    END LOOP;
END
$grants$;
-- Deletes the source performs: definitions, shares, thread run history and
-- converted legacy dashboards. Versions, manifests, Missions, knowledge and
-- quality history are never deleted by a workload.
GRANT DELETE ON nova_meta.config_agents,nova_meta.config_agent_roles,nova_meta.config_agent_skills,
    nova_meta.config_skill_body_chunks,nova_meta.config_mcp_servers,nova_meta.config_tools,
    nova_meta.config_custom_tools,nova_meta.config_agent_automations,nova_meta.config_studio_shares,
    nova_meta.config_agent_runs,nova_meta.config_agent_run_events,nova_meta.config_studio_artifacts,
    nova_meta.config_studio_dashboards TO nova_meta_web;
-- The automation worker changes only run bookkeeping.
GRANT UPDATE (last_run_at,last_status,last_thread_id,next_run_at,run_generation)
    ON nova_meta.config_agent_automations TO nova_meta_automation;
GRANT SELECT,INSERT (id,revision,operation_id,agent_id,principal,active_role,
        security_context_version,payload)
    ON nova_meta.config_agent_quality_cases,nova_meta.config_agent_quality_runs,
       nova_meta.config_agent_quality_monitoring,nova_meta.config_agent_improvement_proposals,
       nova_meta.config_agent_harness_lessons TO nova_meta_web;
GRANT SELECT ON nova_meta.config_agent_quality_cases,nova_meta.config_agent_quality_runs,
    nova_meta.config_agent_quality_monitoring,nova_meta.config_agent_improvement_proposals,
    nova_meta.config_agent_harness_lessons TO nova_meta_agent;

-- ── Statement write guard ────────────────────────────────────────────────

CREATE FUNCTION nova_meta.agent_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Agent import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
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
DO $guards$
DECLARE tbl text;
BEGIN
    FOR tbl IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='nova_meta' AND c.relkind='r' AND c.relname IN ('config_agents',
            'config_agent_roles','config_agent_skills','config_skill_body_chunks',
            'config_mcp_servers','config_tools','config_custom_tools','config_agent_versions',
            'config_agent_release_manifests','config_agent_automations','config_studio_shares',
            'config_agent_runs','config_agent_run_events','config_studio_missions',
            'config_studio_deliverables','config_studio_resources','config_studio_resource_grants',
            'config_studio_artifacts','config_studio_dashboards','config_deep_research_runs',
            'config_verified_query_candidates','config_agent_rule_proposals',
            'config_agent_memories','config_agent_memory_revisions','config_agent_memory_evidence',
            'config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons') LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.agent_write_guard()',
            tbl||'_write_guard', tbl);
    END LOOP;
END
$guards$;

-- ── Row guards: governed definitions ─────────────────────────────────────

CREATE FUNCTION nova_meta.agent_definition_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE pointer text[]; parts integer;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' AND NEW.provenance IS DISTINCT FROM OLD.provenance THEN
        RAISE EXCEPTION 'Provenance is fixed' USING ERRCODE='23514';
    END IF;
    -- One branch per table: a record field that a table lacks cannot appear
    -- in an expression evaluated for that table.
    IF TG_TABLE_NAME='config_agents' THEN
        IF btrim(NEW.name)='' OR coalesce(NEW.visibility,'private') NOT IN ('private','shared')
           OR coalesce(NEW.policy,'auto_read_only') NOT IN ('auto_read_only','ask_every_tool')
           OR coalesce(NEW.tool_not_accessible,'accept') NOT IN ('accept','reject')
           OR coalesce(NEW.harness_mode,'auto') NOT IN ('auto','fast','guided','strict')
           OR coalesce(NEW.budget_profile,'analyst') NOT IN ('fast','analyst','deep')
           OR coalesce(NEW.budget_seconds,1)<=0 OR coalesce(NEW.budget_tokens,1)<=0 THEN
            RAISE EXCEPTION 'Invalid agent' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.agent_id,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.agent_id,OLD.owner_name,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1
              OR NEW.config_revision IS NULL
              OR NEW.config_revision IS NOT DISTINCT FROM OLD.config_revision THEN
            -- Every saved change publishes a new configuration revision.
            RAISE EXCEPTION 'Invalid agent change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_roles' THEN
        IF NEW.grant_type NOT IN ('USAGE','OWNERSHIP') OR btrim(NEW.role_name)=''
           OR NEW.role_name='ACCOUNTADMIN' THEN
            RAISE EXCEPTION 'Invalid agent role' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_agents a WHERE a.agent_id=NEW.agent_id
                AND a.owner_name=NEW.owner_name) THEN
            RAISE EXCEPTION 'Only the agent owner grants access' USING ERRCODE='42501';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.verified_fingerprint:=NULL; NEW.verified_at:=NULL;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.agent_id,NEW.role_name,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.agent_id,OLD.role_name,OLD.owner_name,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid agent role change' USING ERRCODE='23514';
        ELSE
            IF NEW.verified_fingerprint IS DISTINCT FROM OLD.verified_fingerprint THEN
                NEW.verified_at:=CASE WHEN NEW.verified_fingerprint IS NULL THEN NULL
                                      ELSE clock_timestamp() END;
            ELSE
                NEW.verified_at:=OLD.verified_at;
            END IF;
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_skills' THEN
        IF btrim(NEW.name)='' OR coalesce(NEW.scope,'user') NOT IN ('user','global') THEN
            RAISE EXCEPTION 'Invalid skill' USING ERRCODE='23514';
        END IF;
        -- A long body is published only as a pointer to a complete part set.
        IF NEW.body LIKE 'nova-skill-body:v1:%' THEN
            pointer:=regexp_match(NEW.body,'^nova-skill-body:v1:([^:]{1,64}):([1-9][0-9]{0,3})$');
            IF pointer IS NULL THEN
                RAISE EXCEPTION 'Invalid skill body pointer' USING ERRCODE='23514';
            END IF;
            SELECT count(*) INTO parts FROM nova_meta.config_skill_body_chunks c
                WHERE c.skill_id=NEW.skill_id AND c.owner_name=NEW.owner_name
                  AND c.revision=pointer[1] AND c.part_index<pointer[2]::integer;
            IF parts<>pointer[2]::integer THEN
                RAISE EXCEPTION 'The skill body is incomplete' USING ERRCODE='23514';
            END IF;
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.skill_id,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.skill_id,OLD.owner_name,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid skill change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_skill_body_chunks' THEN
        NEW.provenance:='runtime';
    ELSIF TG_TABLE_NAME='config_mcp_servers' THEN
        -- Registrations are inert configuration; a credential never belongs in
        -- the endpoint (userinfo) of a stored registration.
        IF btrim(NEW.name)='' OR NEW.transport NOT IN ('http','sse','stdio')
           OR (NEW.transport='stdio' AND coalesce(btrim(NEW.command),'')='')
           OR (NEW.transport<>'stdio' AND coalesce(NEW.endpoint,'') !~ '^https?://[^/@?#]+(/[^@]*)?$')
           OR (NEW.args IS NOT NULL AND jsonb_typeof(NEW.args)<>'array') THEN
            RAISE EXCEPTION 'Invalid MCP server' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.last_status:=NULL;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.server_id,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.server_id,OLD.owner_name,OLD.created_at) THEN
            RAISE EXCEPTION 'Invalid MCP server change' USING ERRCODE='23514';
        ELSIF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision THEN
            IF NEW.metadata_revision<>OLD.metadata_revision+1 THEN
                RAISE EXCEPTION 'Invalid MCP server revision' USING ERRCODE='23514';
            END IF;
            NEW.updated_at:=clock_timestamp();
        ELSIF ROW(NEW.name,NEW.description,NEW.transport,NEW.endpoint,NEW.command,NEW.args,NEW.is_active)
              IS DISTINCT FROM ROW(OLD.name,OLD.description,OLD.transport,OLD.endpoint,OLD.command,
                  OLD.args,OLD.is_active) THEN
            RAISE EXCEPTION 'An MCP server change needs a new revision' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='config_tools' THEN
        IF btrim(NEW.name)='' OR (NEW.source<>'builtin' AND NEW.source<>'user'
                AND NEW.source !~ '^mcp:[^:]{1,64}$')
           OR (NEW.input_schema IS NOT NULL AND jsonb_typeof(NEW.input_schema)<>'object') THEN
            RAISE EXCEPTION 'Invalid tool' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.tool_id,NEW.owner_name,NEW.name,NEW.source,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.tool_id,OLD.owner_name,OLD.name,OLD.source,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid tool change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_custom_tools' THEN
        IF btrim(NEW.name)='' OR NEW.kind NOT IN ('function','procedure')
           OR (NEW.definition IS NOT NULL AND jsonb_typeof(NEW.definition)<>'object') THEN
            RAISE EXCEPTION 'Invalid custom tool' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.tool_id,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.tool_id,OLD.owner_name,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid custom tool change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_versions' THEN
        IF btrim(NEW.label)='' OR json_typeof(NEW.configuration::json)<>'object' THEN
            RAISE EXCEPTION 'Invalid agent version' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_agent_release_manifests' THEN
        IF json_typeof(NEW.payload::json)<>'object' THEN
            RAISE EXCEPTION 'Invalid release manifest' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_agent_versions v
                WHERE v.version_id=NEW.version_id AND v.agent_id=NEW.agent_id
                  AND v.owner_name=NEW.owner_name) THEN
            RAISE EXCEPTION 'A release manifest needs its saved configuration' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_studio_shares' THEN
        IF NEW.object_type NOT IN ('thread','dashboard','decision')
           OR NEW.target_type NOT IN ('user','role') OR btrim(NEW.target_name)=''
           OR (NEW.target_type='user' AND NEW.target_name=NEW.owner_name) THEN
            RAISE EXCEPTION 'Invalid share' USING ERRCODE='23514';
        END IF;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END
$body$;
DO $definition_guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agents','config_agent_roles','config_agent_skills',
            'config_skill_body_chunks','config_mcp_servers','config_tools','config_custom_tools',
            'config_agent_versions','config_agent_release_manifests','config_studio_shares'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_definition_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$definition_guards$;

-- ── Row guards: run journals ─────────────────────────────────────────────

CREATE FUNCTION nova_meta.agent_run_guard() RETURNS trigger
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
    claimed:=data::jsonb->>'sequence';
    IF claimed IS NULL OR claimed !~ '^[0-9]{1,18}$' OR claimed::bigint<>NEW.sequence THEN
        RAISE EXCEPTION 'Invalid run event frame' USING ERRCODE='23514';
    END IF;
    NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    RETURN NEW;
END
$body$;
CREATE TRIGGER config_agent_runs_row_guard BEFORE INSERT OR UPDATE ON nova_meta.config_agent_runs
    FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_run_guard();
CREATE TRIGGER config_agent_run_events_row_guard BEFORE INSERT ON nova_meta.config_agent_run_events
    FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_run_guard();

-- An event batch and its high-water mark commit together: at commit, every
-- runtime event must be covered by its run's last_sequence.
CREATE FUNCTION nova_meta.agent_event_high_water() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF NEW.provenance='runtime' AND NOT EXISTS (SELECT 1 FROM nova_meta.config_agent_runs r
            WHERE r.run_id=NEW.run_id AND r.last_sequence>=NEW.sequence) THEN
        RAISE EXCEPTION 'Run events need their high-water mark' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER config_agent_run_events_high_water AFTER INSERT
    ON nova_meta.config_agent_run_events DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_event_high_water();

-- ── Row guards: work records ──────────────────────────────────────────────

CREATE FUNCTION nova_meta.agent_work_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE login text; version bigint;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        IF TG_OP='INSERT' THEN NEW.provenance:='imported'; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='UPDATE' AND NEW.provenance IS DISTINCT FROM OLD.provenance THEN
        RAISE EXCEPTION 'Provenance is fixed' USING ERRCODE='23514';
    END IF;
    login:=nullif(current_setting('nova.session_id',true),'');
    version:=current_setting('nova.security_context_version',true)::bigint;
    IF TG_TABLE_NAME='config_studio_missions' THEN
        IF json_typeof(NEW.payload::json)<>'object' OR login IS NULL THEN
            RAISE EXCEPTION 'Invalid Mission' USING ERRCODE='23514';
        END IF;
        -- A Mission is always written by its current login and security
        -- version; resuming an older one rebinds it under the revision CAS.
        NEW.session_id:=login; NEW.security_version:=version;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.mission_id,NEW.owner_name,NEW.thread_id,NEW.role_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.mission_id,OLD.owner_name,OLD.thread_id,OLD.role_name,
                    OLD.created_at)
              OR NEW.revision<>OLD.revision+1 THEN
            RAISE EXCEPTION 'Invalid Mission change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_studio_deliverables' THEN
        IF json_typeof(NEW.payload::json)<>'object' OR login IS NULL OR NOT EXISTS (
                SELECT 1 FROM nova_meta.config_studio_missions m WHERE m.mission_id=NEW.mission_id
                    AND m.owner_name=NEW.owner_name AND m.role_name=NEW.role_name) THEN
            RAISE EXCEPTION 'Invalid deliverable' USING ERRCODE='23514';
        END IF;
        NEW.session_id:=login; NEW.security_version:=version;
        NEW.provenance:='runtime'; NEW.created_at:=clock_timestamp();
    ELSIF TG_TABLE_NAME='config_agent_automations' THEN
        IF btrim(NEW.title)='' OR btrim(NEW.prompt)='' OR NEW.schedule_kind NOT IN ('cron','interval')
           OR (NEW.enabled AND NEW.agent_id IN ('__smart__','__auto__')) THEN
            -- Retired Smart/Auto automations stay disabled.
            RAISE EXCEPTION 'Invalid automation' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.run_generation:=0;
            NEW.last_run_at:=NULL; NEW.last_status:=NULL; NEW.last_thread_id:=NULL;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.automation_id,NEW.agent_id,NEW.owner_name,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.automation_id,OLD.agent_id,OLD.owner_name,OLD.created_at)
              OR NEW.run_generation<OLD.run_generation
              OR NEW.run_generation>OLD.run_generation+1 THEN
            RAISE EXCEPTION 'Invalid automation change' USING ERRCODE='23514';
        ELSIF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision THEN
            -- A definition change by the owner: never also run bookkeeping.
            IF NEW.metadata_revision<>OLD.metadata_revision+1
               OR ROW(NEW.last_run_at,NEW.last_status,NEW.last_thread_id,NEW.run_generation)
                  IS DISTINCT FROM ROW(OLD.last_run_at,OLD.last_status,OLD.last_thread_id,
                      OLD.run_generation) THEN
                RAISE EXCEPTION 'Invalid automation revision' USING ERRCODE='23514';
            END IF;
            NEW.updated_at:=clock_timestamp();
        ELSIF ROW(NEW.role_name,NEW.title,NEW.prompt,NEW.schedule_kind,NEW.schedule_expr,NEW.timezone,
                  NEW.condition_json,NEW.delivery_json,NEW.enabled)
              IS DISTINCT FROM ROW(OLD.role_name,OLD.title,OLD.prompt,OLD.schedule_kind,
                  OLD.schedule_expr,OLD.timezone,OLD.condition_json,OLD.delivery_json,OLD.enabled) THEN
            RAISE EXCEPTION 'An automation change needs a new revision' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_studio_artifacts' THEN
        IF btrim(NEW.title)='' OR NEW.artifact_type NOT IN ('chart','dashboard')
           OR coalesce(NEW.bi_kind,'chart') NOT IN ('chart','dashboard') THEN
            RAISE EXCEPTION 'Invalid artifact' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            NEW.provenance:='runtime'; NEW.metadata_revision:=1;
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.artifact_id,NEW.owner_name,NEW.agent_id,NEW.thread_id,NEW.artifact_type,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.artifact_id,OLD.owner_name,OLD.agent_id,OLD.thread_id,
                    OLD.artifact_type,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1 THEN
            RAISE EXCEPTION 'Invalid artifact change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_deep_research_runs' THEN
        IF NEW.status NOT IN ('planning','running','writing','done','failed','cancelled','interrupted') THEN
            RAISE EXCEPTION 'Invalid research run' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'planning' THEN
                RAISE EXCEPTION 'A research run starts planning' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.progress_json:='[]';
            NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF ROW(NEW.run_id,NEW.agent_id,NEW.owner_name,NEW.thread_id,NEW.question,NEW.created_at)
                IS DISTINCT FROM ROW(OLD.run_id,OLD.agent_id,OLD.owner_name,OLD.thread_id,
                    OLD.question,OLD.created_at)
              OR NEW.metadata_revision<>OLD.metadata_revision+1
              OR OLD.status IN ('done','failed','cancelled','interrupted') THEN
            RAISE EXCEPTION 'Invalid research run change' USING ERRCODE='23514';
        ELSE
            NEW.updated_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_verified_query_candidates' THEN
        IF NEW.status NOT IN ('pending','approved','rejected')
           OR json_typeof(NEW.semantic_plan::json)<>'object' OR NEW.plan_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'Invalid verified-query candidate' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'pending' THEN
                RAISE EXCEPTION 'A candidate starts pending' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.decided_by:=NULL;
            NEW.decided_at:=NULL; NEW.created_at:=clock_timestamp();
        ELSIF OLD.status<>'pending' OR NEW.status='pending'
              OR NEW.metadata_revision<>OLD.metadata_revision+1
              OR ROW(NEW.candidate_id,NEW.agent_id,NEW.owner_name,NEW.proposed_by,NEW.view_id,
                     NEW.view_version,NEW.question,NEW.semantic_plan,NEW.plan_sha256,
                     NEW.source_thread_id,NEW.source_message_id,NEW.created_at)
                 IS DISTINCT FROM ROW(OLD.candidate_id,OLD.agent_id,OLD.owner_name,OLD.proposed_by,
                     OLD.view_id,OLD.view_version,OLD.question,OLD.semantic_plan,OLD.plan_sha256,
                     OLD.source_thread_id,OLD.source_message_id,OLD.created_at) THEN
            -- A candidate is decided exactly once, by the agent owner.
            RAISE EXCEPTION 'Invalid candidate decision' USING ERRCODE='23514';
        ELSE
            NEW.decided_by:=current_setting('nova.principal',true); NEW.decided_at:=clock_timestamp();
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_rule_proposals' THEN
        IF NEW.status NOT IN ('pending','approved','rejected')
           OR coalesce(NEW.proposal_kind,'metric') NOT IN ('metric','autopilot') THEN
            RAISE EXCEPTION 'Invalid rule proposal' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status<>'pending' THEN
                RAISE EXCEPTION 'A rule proposal starts pending' USING ERRCODE='23514';
            END IF;
            NEW.provenance:='runtime'; NEW.metadata_revision:=1; NEW.previewed_at:=NULL;
            NEW.reviewed_by:=NULL; NEW.created_at:=clock_timestamp(); NEW.updated_at:=NEW.created_at;
        ELSIF OLD.status<>'pending' OR NEW.metadata_revision<>OLD.metadata_revision+1
              OR ROW(NEW.proposal_id,NEW.owner_name,NEW.agent_id,NEW.role_name,NEW.memory_id,
                     NEW.semantic_model_id,NEW.metric_name,NEW.prior_expression,NEW.proposed_expression,
                     NEW.prior_fingerprint,NEW.proposed_fingerprint,NEW.created_at,NEW.proposal_kind,
                     NEW.details)
                 IS DISTINCT FROM ROW(OLD.proposal_id,OLD.owner_name,OLD.agent_id,OLD.role_name,
                     OLD.memory_id,OLD.semantic_model_id,OLD.metric_name,OLD.prior_expression,
                     OLD.proposed_expression,OLD.prior_fingerprint,OLD.proposed_fingerprint,
                     OLD.created_at,OLD.proposal_kind,OLD.details) THEN
            RAISE EXCEPTION 'Invalid rule proposal change' USING ERRCODE='23514';
        ELSE
            IF NEW.status<>'pending' THEN
                NEW.reviewed_by:=current_setting('nova.principal',true);
            ELSIF NEW.reviewed_by IS DISTINCT FROM OLD.reviewed_by THEN
                RAISE EXCEPTION 'Invalid rule proposal change' USING ERRCODE='23514';
            END IF;
            IF NEW.previewed_at IS DISTINCT FROM OLD.previewed_at THEN
                NEW.previewed_at:=clock_timestamp();
            END IF;
            NEW.updated_at:=clock_timestamp();
        END IF;
    END IF;
    RETURN NEW;
END
$body$;
DO $work_guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_studio_missions','config_studio_deliverables',
            'config_agent_automations','config_studio_artifacts','config_deep_research_runs',
            'config_verified_query_candidates','config_agent_rule_proposals'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_work_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$work_guards$;

-- ── Row guards: knowledge and revision stores ────────────────────────────

-- The head of a revision-store record across every scope, without its
-- payload: a guessed ID cannot extend or fork another scope's record.
CREATE FUNCTION nova_meta.agent_record_head(tbl text, record_id text,
    OUT head bigint, OUT foreign_scope boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
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
REVOKE ALL ON FUNCTION nova_meta.agent_record_head(text,text) FROM PUBLIC;
-- The definer reads as the owner, whose rows are otherwise visible only to an
-- import under maintenance; this narrow read serves the head check alone.
DO $heads$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons'] LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO nova_meta_owner USING (session_user=''nova_meta_web'' AND nova_meta.agent_actor_bound())',
            tbl||'_head', tbl);
    END LOOP;
END
$heads$;
GRANT EXECUTE ON FUNCTION nova_meta.agent_record_head(text,text) TO nova_meta_web;

CREATE FUNCTION nova_meta.agent_knowledge_guard() RETURNS trigger
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
            IF NEW.revision<>1 THEN
                RAISE EXCEPTION 'A memory starts at revision 1' USING ERRCODE='23514';
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
        -- The next revision of a memory in this scope, above every retained one.
        IF NOT EXISTS (SELECT 1 FROM nova_meta.config_agent_memories m WHERE m.memory_id=NEW.memory_id
                AND m.user_name=NEW.user_name AND m.agent_id=NEW.agent_id AND m.role_name=NEW.role_name)
           AND NEW.revision<>1 THEN
            RAISE EXCEPTION 'Memory unavailable' USING ERRCODE='23514';
        END IF;
        SELECT max(r.revision) INTO head FROM nova_meta.config_agent_memory_revisions r
            WHERE r.memory_id=NEW.memory_id;
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
DO $knowledge_guards$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_agent_memories','config_agent_memory_revisions',
            'config_agent_memory_evidence','config_agent_quality_cases','config_agent_quality_runs',
            'config_agent_quality_monitoring','config_agent_improvement_proposals',
            'config_agent_harness_lessons'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_knowledge_guard()',
            tbl||'_row_guard', tbl);
    END LOOP;
END
$knowledge_guards$;

-- A memory projection change commits only with the revision it names.
CREATE FUNCTION nova_meta.agent_memory_revision_present() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF NEW.provenance='runtime' AND (TG_OP='INSERT' OR NEW.revision IS DISTINCT FROM OLD.revision)
       AND NOT EXISTS (SELECT 1 FROM nova_meta.config_agent_memory_revisions r
            WHERE r.memory_id=NEW.memory_id AND r.revision=NEW.revision) THEN
        RAISE EXCEPTION 'A memory change needs its revision' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE CONSTRAINT TRIGGER config_agent_memories_revision AFTER INSERT OR UPDATE
    ON nova_meta.config_agent_memories DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION nova_meta.agent_memory_revision_present();

-- ── Success audit for governed changes ───────────────────────────────────

-- Definitions, access grants, MCP/tool registrations, release manifests,
-- automation definitions, shares and review decisions are governed and
-- audited in the same transaction. Runs, events, Missions, knowledge and
-- bookkeeping (MCP status, automation run state) are not.
CREATE FUNCTION nova_meta.audit_agent_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
DECLARE identity jsonb; name text; revision bigint;
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta' THEN
        RAISE EXCEPTION 'Invalid agent audit source' USING ERRCODE='42501';
    END IF;
    IF TG_TABLE_NAME='config_agents' THEN
        IF TG_OP='DELETE' THEN name:=OLD.agent_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.agent_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('agent_id',name);
    ELSIF TG_TABLE_NAME='config_agent_roles' THEN
        IF TG_OP='DELETE' THEN
            identity:=jsonb_build_object('agent_id',OLD.agent_id,'role_name',OLD.role_name);
            name:=OLD.agent_id; revision:=OLD.metadata_revision;
        ELSE
            identity:=jsonb_build_object('agent_id',NEW.agent_id,'role_name',NEW.role_name);
            name:=NEW.agent_id; revision:=NEW.metadata_revision;
        END IF;
    ELSIF TG_TABLE_NAME='config_agent_skills' THEN
        IF TG_OP='DELETE' THEN name:=OLD.skill_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.skill_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('skill_id',name);
    ELSIF TG_TABLE_NAME='config_mcp_servers' THEN
        IF TG_OP='UPDATE' AND NEW.metadata_revision IS NOT DISTINCT FROM OLD.metadata_revision THEN
            RETURN NULL;  -- connection status is bookkeeping
        END IF;
        IF TG_OP='DELETE' THEN name:=OLD.server_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.server_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('server_id',name);
    ELSIF TG_TABLE_NAME='config_tools' THEN
        IF TG_OP='DELETE' THEN name:=OLD.tool_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.tool_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('tool_id',name);
    ELSIF TG_TABLE_NAME='config_custom_tools' THEN
        IF TG_OP='DELETE' THEN name:=OLD.tool_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.tool_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('tool_id',name);
    ELSIF TG_TABLE_NAME='config_agent_release_manifests' THEN
        name:=NEW.manifest_id; revision:=1; identity:=jsonb_build_object('manifest_id',name);
    ELSIF TG_TABLE_NAME='config_agent_automations' THEN
        IF TG_OP='UPDATE' AND NEW.metadata_revision IS NOT DISTINCT FROM OLD.metadata_revision THEN
            RETURN NULL;  -- schedule bookkeeping
        END IF;
        IF TG_OP='DELETE' THEN name:=OLD.automation_id; revision:=OLD.metadata_revision;
        ELSE name:=NEW.automation_id; revision:=NEW.metadata_revision; END IF;
        identity:=jsonb_build_object('automation_id',name);
    ELSIF TG_TABLE_NAME='config_studio_shares' THEN
        IF TG_OP='DELETE' THEN name:=OLD.share_id; ELSE name:=NEW.share_id; END IF;
        revision:=1; identity:=jsonb_build_object('share_id',name);
    ELSIF TG_TABLE_NAME='config_verified_query_candidates' THEN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NULL; END IF;
        name:=NEW.candidate_id; revision:=NEW.metadata_revision;
        identity:=jsonb_build_object('candidate_id',name);
    ELSIF TG_TABLE_NAME='config_agent_rule_proposals' THEN
        IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NULL; END IF;
        name:=NEW.proposal_id; revision:=NEW.metadata_revision;
        identity:=jsonb_build_object('proposal_id',name);
    ELSE
        RAISE EXCEPTION 'Invalid agent audit source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,name,
        CASE TG_OP WHEN 'INSERT' THEN 'CREATE' WHEN 'DELETE' THEN 'DELETE' ELSE 'UPDATE' END,
        'SUCCESS','ALLOW',TG_TABLE_NAME,identity,coalesce(revision,1));
    RETURN NULL;
END
$body$;
DO $audits$
DECLARE item record;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_agents','INSERT OR UPDATE OR DELETE'),
        ('config_agent_roles','INSERT OR UPDATE OR DELETE'),
        ('config_agent_skills','INSERT OR UPDATE OR DELETE'),
        ('config_mcp_servers','INSERT OR UPDATE OR DELETE'),
        ('config_tools','INSERT OR UPDATE OR DELETE'),
        ('config_custom_tools','INSERT OR UPDATE OR DELETE'),
        ('config_agent_release_manifests','INSERT'),
        ('config_agent_automations','INSERT OR UPDATE OR DELETE'),
        ('config_studio_shares','INSERT OR DELETE'),
        ('config_verified_query_candidates','UPDATE'),
        ('config_agent_rule_proposals','UPDATE')
    ) AS t(tbl,ops) LOOP
        EXECUTE format('CREATE TRIGGER %I AFTER %s ON nova_meta.%I FOR EACH ROW EXECUTE FUNCTION nova_meta.audit_agent_change()',
            item.tbl||'_audit', item.ops, item.tbl);
    END LOOP;
END
$audits$;
