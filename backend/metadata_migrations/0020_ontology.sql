-- Business ontology revision stores: twelve append-only kinds and one head
-- table that names the current revision of every record.
--
-- A change is a new (id, revision) row; rows are never updated or deleted.
-- Imported rows are kept exactly as they were, including two rows with the
-- same (id, revision) that the source allowed under a lost lease. Runtime
-- rows must be the next revision of an unbranched record, so a second runtime
-- row with the same (id, revision) cannot exist.
DO $tables$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_ontology_domains','config_ontology_concepts',
        'config_ontology_relations','config_ontology_representations','config_ontology_proposals',
        'config_ontology_releases','config_ontology_aliases','config_ontology_derived',
        'config_ontology_compilations','config_ontology_eval_cases','config_ontology_eval_runs',
        'config_ontology_eval_results'] LOOP
        EXECUTE format($ddl$CREATE TABLE nova_meta.%I (
            id varchar(64) NOT NULL CHECK (octet_length(id) BETWEEN 1 AND 64),
            revision bigint NOT NULL CHECK (revision>=1),
            operation_id varchar(32) NOT NULL CHECK (octet_length(operation_id)<=32),
            actor varchar(128) NOT NULL CHECK (octet_length(actor)<=128),
            active_role varchar(128) NOT NULL CHECK (octet_length(active_role)<=128),
            security_context_version bigint NOT NULL,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
            created_at timestamptz NOT NULL,
            provenance varchar(16) NOT NULL DEFAULT 'imported'
                CHECK (provenance IN ('runtime','imported')),
            PRIMARY KEY (id,revision,operation_id)
        )$ddl$, tbl);
        EXECUTE format('CREATE UNIQUE INDEX %I ON nova_meta.%I (id,revision) '
            'WHERE provenance=''runtime''', tbl||'_runtime_revision', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE nova_meta.%I FORCE ROW LEVEL SECURITY', tbl);
    END LOOP;
END
$tables$;

-- Evaluation results of one run, without windowing over every result. A
-- stored column, not an expression index: under forced RLS the planner only
-- uses an index for leakproof conditions, and jsonb ->> is not leakproof.
ALTER TABLE nova_meta.config_ontology_eval_results
    ADD COLUMN run_id text GENERATED ALWAYS AS (payload->>'run_id') STORED;
CREATE INDEX config_ontology_eval_results_run ON nova_meta.config_ontology_eval_results
    (run_id,id,revision);

-- The newest revision of every record and how many rows share it. Reading
-- "current" joins this table instead of scanning each kind; branches>1 marks
-- an imported fork that needs reconciliation. Only the trigger below writes it.
CREATE TABLE nova_meta.config_ontology_heads (
    kind varchar(64) NOT NULL CHECK (kind IN ('config_ontology_domains','config_ontology_concepts',
        'config_ontology_relations','config_ontology_representations','config_ontology_proposals',
        'config_ontology_releases','config_ontology_aliases','config_ontology_derived',
        'config_ontology_compilations','config_ontology_eval_cases','config_ontology_eval_runs',
        'config_ontology_eval_results')),
    id varchar(64) NOT NULL CHECK (octet_length(id) BETWEEN 1 AND 64),
    revision bigint NOT NULL CHECK (revision>=1),
    branches integer NOT NULL CHECK (branches>=1),
    PRIMARY KEY (kind,id)
);
ALTER TABLE nova_meta.config_ontology_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.config_ontology_heads FORCE ROW LEVEL SECURITY;

-- Ontology records are deployment-wide; the actor is provenance, not an
-- ownership predicate. Domain visibility and stewardship are decided by the
-- service (ontology/access.py), and the row guard below repeats the checks
-- that can be decided from stored rows. Access is per workload, through a
-- bound actor context, never anonymously.
CREATE FUNCTION nova_meta.ontology_actor_bound() RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT nova_meta.actor_context_valid() IS TRUE
        AND current_setting('nova.service_identity',true) IS NOT DISTINCT FROM session_user
$body$;
REVOKE ALL ON FUNCTION nova_meta.ontology_actor_bound() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.ontology_actor_bound()
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- Web serves every ontology page, stewardship, releases and evaluation. The
-- agent and automation workloads run Studio tools: they read the ontology,
-- file and withdraw proposals, and publish Semantic Views, which refreshes
-- representations and recompiles the derived layer.
DO $policies$
DECLARE item record; readers text:='nova_meta_web,nova_meta_agent,nova_meta_automation';
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('config_ontology_domains','web'),
        ('config_ontology_concepts','web'),
        ('config_ontology_relations','web'),
        ('config_ontology_representations','web,agent,automation'),
        ('config_ontology_proposals','web,agent,automation'),
        ('config_ontology_releases','web'),
        ('config_ontology_aliases','web'),
        ('config_ontology_derived','web,agent,automation'),
        ('config_ontology_compilations','web,agent,automation'),
        ('config_ontology_eval_cases','web'),
        ('config_ontology_eval_runs','web'),
        ('config_ontology_eval_results','web')
    ) AS t(tbl,inserters) LOOP
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR SELECT TO %s '
            'USING (nova_meta.ontology_actor_bound())', item.tbl||'_read', item.tbl, readers);
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR INSERT TO %s '
            'WITH CHECK (nova_meta.ontology_actor_bound())', item.tbl||'_insert', item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
        EXECUTE format('CREATE POLICY %I ON nova_meta.%I FOR ALL TO nova_meta_owner '
            'USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)) '
            'WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton))',
            item.tbl||'_import', item.tbl);
        EXECUTE format('GRANT SELECT ON nova_meta.%I TO %s', item.tbl, readers);
        EXECUTE format('GRANT INSERT (id,revision,operation_id,payload) ON nova_meta.%I TO %s',
            item.tbl,
            (SELECT string_agg('nova_meta_'||r,',') FROM unnest(string_to_array(item.inserters,',')) r));
    END LOOP;
END
$policies$;
CREATE POLICY config_ontology_heads_read ON nova_meta.config_ontology_heads FOR SELECT
    TO nova_meta_web,nova_meta_agent,nova_meta_automation USING (nova_meta.ontology_actor_bound());
-- The owner writes heads only from the head trigger of a bound workload's
-- insert, or for the migrator's import under maintenance.
CREATE POLICY config_ontology_heads_maintain ON nova_meta.config_ontology_heads FOR ALL
    TO nova_meta_owner
    USING ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)
        OR (pg_has_role(session_user,'nova_meta_app','MEMBER') AND nova_meta.ontology_actor_bound()))
    WITH CHECK ((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton)
        OR (pg_has_role(session_user,'nova_meta_app','MEMBER') AND nova_meta.ontology_actor_bound()));
GRANT SELECT ON nova_meta.config_ontology_heads TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- Statement guard: revisions are inserted, never changed. The migrator's
-- import runs only under maintenance; workloads write only in an active epoch.
CREATE FUNCTION nova_meta.ontology_write_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF TG_TABLE_NAME='config_ontology_heads' THEN
        IF TG_OP IN ('DELETE','TRUNCATE') OR current_user<>'nova_meta_owner' THEN
            RAISE EXCEPTION 'Ontology heads are maintained by revisions only' USING ERRCODE='42501';
        END IF;
        RETURN NULL;
    END IF;
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Ontology revisions are immutable' USING ERRCODE='42501';
    END IF;
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        PERFORM pg_advisory_xact_lock(5642823696587772993);
        IF NOT coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),false)
        THEN RAISE EXCEPTION 'Ontology import requires maintenance' USING ERRCODE='55000'; END IF;
    ELSIF current_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation') THEN
        PERFORM pg_advisory_xact_lock_shared(5642823696587772993);
        IF coalesce((SELECT maintenance FROM nova_meta.deployment_state WHERE singleton),true)
        THEN RAISE EXCEPTION 'Metadata writes require an active epoch' USING ERRCODE='25006'; END IF;
    ELSE
        RAISE EXCEPTION 'Ontology metadata mutation is not authorized' USING ERRCODE='42501';
    END IF;
    RETURN NULL;
END
$body$;

-- Whether the bound active role stewards one of these domains, as
-- access.stewards decides it: the domain is ACTIVE and lists the role.
CREATE FUNCTION nova_meta.ontology_role_stewards(domain_ids jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path=pg_catalog AS $body$
    SELECT jsonb_typeof(domain_ids)='array' AND EXISTS (
        SELECT 1 FROM nova_meta.config_ontology_heads h
        JOIN nova_meta.config_ontology_domains d ON d.id=h.id AND d.revision=h.revision
        WHERE h.kind='config_ontology_domains' AND h.branches=1
          AND h.id IN (SELECT jsonb_array_elements_text(domain_ids))
          AND d.payload->>'status'='ACTIVE'
          AND jsonb_typeof(d.payload->'steward_roles')='array'
          AND d.payload->'steward_roles' ? current_setting('nova.active_role',true))
$body$;
REVOKE ALL ON FUNCTION nova_meta.ontology_role_stewards(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nova_meta.ontology_role_stewards(jsonb)
    TO nova_meta_web,nova_meta_agent,nova_meta_automation;

-- Row guard for runtime revisions. Imported rows are kept as they are.
CREATE FUNCTION nova_meta.ontology_row_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
DECLARE
    head_revision bigint; head_branches integer; prev jsonb; status text; proposal jsonb;
    proposal_heads integer; principal text; role text; admin boolean;
    added jsonb; review jsonb; prev_reviews integer; decided text; covered boolean;
    target jsonb; target_heads integer; alias jsonb;
BEGIN
    IF current_user='nova_meta_owner' AND session_user='nova_meta_migrator' THEN
        NEW.provenance:='imported';
        RETURN NEW;
    END IF;
    principal:=current_setting('nova.principal',true);
    role:=current_setting('nova.active_role',true);
    admin:=upper(role)='ACCOUNTADMIN';
    IF NEW.operation_id='' OR NEW.payload->>'id' IS DISTINCT FROM NEW.id
       OR NEW.payload->'revision' IS DISTINCT FROM to_jsonb(NEW.revision) THEN
        RAISE EXCEPTION 'Invalid ontology revision' USING ERRCODE='23514';
    END IF;
    SELECT h.revision,h.branches INTO head_revision,head_branches
        FROM nova_meta.config_ontology_heads h WHERE h.kind=TG_TABLE_NAME AND h.id=NEW.id;
    IF head_branches>1 THEN
        RAISE EXCEPTION 'Concurrent revisions require reconciliation' USING ERRCODE='40001';
    END IF;
    IF NEW.revision<>coalesce(head_revision,0)+1 THEN
        RAISE EXCEPTION 'Record changed; reload before editing' USING ERRCODE='40001';
    END IF;
    NEW.provenance:='runtime';
    NEW.actor:=principal;
    NEW.active_role:=role;
    NEW.security_context_version:=current_setting('nova.security_context_version',true)::bigint;
    NEW.created_at:=clock_timestamp();
    status:=coalesce(NEW.payload->>'status','');

    -- One branch per kind: each reads only its own table.
    IF TG_TABLE_NAME='config_ontology_domains' THEN
        IF NOT admin THEN
            RAISE EXCEPTION 'ACCOUNTADMIN must be the active role' USING ERRCODE='42501';
        END IF;
        IF status NOT IN ('ACTIVE','RETIRED')
           OR jsonb_typeof(NEW.payload->'steward_roles') IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.payload->'steward_roles')<1
           OR NEW.payload->>'parent_id'=NEW.id
           OR (NEW.payload->>'parent_id' IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM nova_meta.config_ontology_heads h
                WHERE h.kind='config_ontology_domains' AND h.id=NEW.payload->>'parent_id')) THEN
            RAISE EXCEPTION 'Invalid ontology domain' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME IN ('config_ontology_concepts','config_ontology_relations',
                            'config_ontology_representations') THEN
        IF (TG_TABLE_NAME='config_ontology_concepts' AND status NOT IN ('APPROVED','DEPRECATED'))
           OR (TG_TABLE_NAME='config_ontology_relations' AND status NOT IN ('ACTIVE','RETIRED'))
           OR (TG_TABLE_NAME='config_ontology_representations'
               AND status NOT IN ('VALIDATED','STALE','REVOKED')) THEN
            RAISE EXCEPTION 'Invalid ontology definition' USING ERRCODE='23514';
        END IF;
        IF head_revision IS NOT NULL THEN
            IF TG_TABLE_NAME='config_ontology_concepts' THEN
                SELECT r.payload INTO prev FROM nova_meta.config_ontology_concepts r
                    WHERE r.id=NEW.id AND r.revision=head_revision;
            ELSIF TG_TABLE_NAME='config_ontology_relations' THEN
                SELECT r.payload INTO prev FROM nova_meta.config_ontology_relations r
                    WHERE r.id=NEW.id AND r.revision=head_revision;
            ELSE
                SELECT r.payload INTO prev FROM nova_meta.config_ontology_representations r
                    WHERE r.id=NEW.id AND r.revision=head_revision;
            END IF;
        END IF;
        -- A Semantic View publication re-pins or stales a binding under the
        -- proposal that created it; every other change applies an approved
        -- proposal, by a steward of one of its domains or ACCOUNTADMIN.
        IF TG_TABLE_NAME='config_ontology_representations' AND prev IS NOT NULL
           AND prev->>'proposal_id' IS NOT DISTINCT FROM NEW.payload->>'proposal_id' THEN
            IF prev->'concept_id' IS DISTINCT FROM NEW.payload->'concept_id' THEN
                RAISE EXCEPTION 'A binding keeps its concept' USING ERRCODE='23514';
            END IF;
        ELSE
            SELECT count(*),min(p.payload::text)::jsonb INTO proposal_heads,proposal
                FROM nova_meta.config_ontology_heads h
                JOIN nova_meta.config_ontology_proposals p ON p.id=h.id AND p.revision=h.revision
                WHERE h.kind='config_ontology_proposals' AND h.id=NEW.payload->>'proposal_id';
            IF proposal_heads<>1 OR proposal->>'status' IS DISTINCT FROM 'APPROVED' THEN
                RAISE EXCEPTION 'Only an approved proposal can be applied' USING ERRCODE='40001';
            END IF;
            IF NOT admin AND NOT nova_meta.ontology_role_stewards(proposal->'domain_ids') THEN
                RAISE EXCEPTION 'Only a steward of this proposal may apply it' USING ERRCODE='42501';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='config_ontology_proposals' THEN
        IF status NOT IN ('PENDING','APPROVED','APPLIED','REJECTED','WITHDRAWN')
           OR jsonb_typeof(NEW.payload->'domain_ids') IS DISTINCT FROM 'array'
           OR jsonb_array_length(NEW.payload->'domain_ids')<1
           OR jsonb_typeof(coalesce(NEW.payload->'reviews','[]'::jsonb)) IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'Invalid ontology proposal' USING ERRCODE='23514';
        END IF;
        IF head_revision IS NULL THEN
            IF status<>'PENDING' OR jsonb_array_length(coalesce(NEW.payload->'reviews','[]'::jsonb))<>0
            THEN RAISE EXCEPTION 'A proposal starts pending' USING ERRCODE='23514'; END IF;
            IF NEW.payload->>'submitter' IS DISTINCT FROM principal THEN
                RAISE EXCEPTION 'The submitter is the caller' USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END IF;
        SELECT r.payload INTO prev FROM nova_meta.config_ontology_proposals r
            WHERE r.id=NEW.id AND r.revision=head_revision;
        IF (NEW.payload-ARRAY['reviews','status','actor','updated_at','revision','created_at'])
           IS DISTINCT FROM (prev-ARRAY['reviews','status','actor','updated_at','revision','created_at'])
        THEN RAISE EXCEPTION 'A proposal keeps its content' USING ERRCODE='23514'; END IF;
        prev_reviews:=jsonb_array_length(coalesce(prev->'reviews','[]'::jsonb));
        added:=coalesce(NEW.payload->'reviews','[]'::jsonb);
        IF jsonb_array_length(added)<prev_reviews OR (
            SELECT coalesce(jsonb_agg(e ORDER BY o),'[]'::jsonb)
            FROM jsonb_array_elements(added) WITH ORDINALITY t(e,o) WHERE o<=prev_reviews
        ) IS DISTINCT FROM coalesce(prev->'reviews','[]'::jsonb) THEN
            RAISE EXCEPTION 'Reviews are append-only' USING ERRCODE='23514';
        END IF;
        IF jsonb_array_length(added)=prev_reviews THEN
            -- No review: withdraw (the submitter) or finish applying.
            IF NOT ((prev->>'status' IN ('PENDING','APPROVED') AND status='WITHDRAWN')
                    OR (prev->>'status'='APPROVED' AND status='APPLIED')) THEN
                RAISE EXCEPTION 'Invalid proposal transition' USING ERRCODE='40001';
            END IF;
            IF status='WITHDRAWN' AND NEW.payload->>'submitter' IS DISTINCT FROM principal THEN
                RAISE EXCEPTION 'Only the submitter can withdraw a proposal' USING ERRCODE='42501';
            END IF;
            IF status='APPLIED' AND NOT admin
               AND NOT nova_meta.ontology_role_stewards(NEW.payload->'domain_ids') THEN
                RAISE EXCEPTION 'Only a steward of this proposal may apply it' USING ERRCODE='42501';
            END IF;
        ELSIF jsonb_array_length(added)=prev_reviews+1 THEN
            review:=added->prev_reviews;
            IF prev->>'status'<>'PENDING' THEN
                RAISE EXCEPTION 'Only a pending proposal is reviewed' USING ERRCODE='40001';
            END IF;
            IF review->>'principal' IS DISTINCT FROM principal
               OR review->>'active_role' IS DISTINCT FROM role
               OR principal=NEW.payload->>'submitter' THEN
                RAISE EXCEPTION 'The submitter cannot review their own proposal'
                    USING ERRCODE='42501';
            END IF;
            IF jsonb_typeof(review->'domain_ids') IS DISTINCT FROM 'array'
               OR jsonb_array_length(review->'domain_ids')<1
               OR coalesce(review->>'decision','') NOT IN ('APPROVE','REJECT') THEN
                RAISE EXCEPTION 'Invalid ontology review' USING ERRCODE='23514';
            END IF;
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(review->'domain_ids') d(v)
                       WHERE NOT (NEW.payload->'domain_ids' @> jsonb_build_array(d.v))
                          OR NOT nova_meta.ontology_role_stewards(jsonb_build_array(d.v))) THEN
                RAISE EXCEPTION 'The active role does not steward a domain of this proposal'
                    USING ERRCODE='42501';
            END IF;
            SELECT NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(NEW.payload->'domain_ids') d(v)
                WHERE NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(added) r(v)
                    WHERE r.v->>'decision'='APPROVE' AND r.v->'domain_ids' @> jsonb_build_array(d.v)))
                INTO covered;
            decided:=CASE WHEN review->>'decision'='REJECT' THEN 'REJECTED'
                          WHEN covered THEN 'APPROVED' ELSE 'PENDING' END;
            IF status IS DISTINCT FROM decided THEN
                RAISE EXCEPTION 'Invalid proposal transition' USING ERRCODE='23514';
            END IF;
        ELSE
            RAISE EXCEPTION 'One review per revision' USING ERRCODE='23514';
        END IF;
    ELSIF TG_TABLE_NAME='config_ontology_releases' THEN
        IF NOT admin THEN
            RAISE EXCEPTION 'ACCOUNTADMIN must be the active role' USING ERRCODE='42501';
        END IF;
        IF status NOT IN ('PREPARED','BLOCKED','PUBLISHED') THEN
            RAISE EXCEPTION 'Invalid ontology release' USING ERRCODE='23514';
        END IF;
        IF head_revision IS NULL THEN
            IF status='PUBLISHED' THEN
                RAISE EXCEPTION 'A release is prepared before it is published' USING ERRCODE='23514';
            END IF;
        ELSE
            SELECT r.payload INTO prev FROM nova_meta.config_ontology_releases r
                WHERE r.id=NEW.id AND r.revision=head_revision;
            IF prev->>'status'<>'PREPARED' OR status<>'PUBLISHED'
               OR (NEW.payload-ARRAY['status','published_at','actor','updated_at','revision','created_at'])
                  IS DISTINCT FROM
                  (prev-ARRAY['status','published_at','actor','updated_at','revision','created_at'])
            THEN RAISE EXCEPTION 'A release changes only by being published' USING ERRCODE='40001';
            END IF;
            -- Publication moves the current alias first, then marks the release.
            SELECT a.payload INTO alias FROM nova_meta.config_ontology_heads h
                JOIN nova_meta.config_ontology_aliases a ON a.id=h.id AND a.revision=h.revision
                WHERE h.kind='config_ontology_aliases' AND h.id='current' AND h.branches=1;
            IF alias->>'release_id' IS DISTINCT FROM NEW.id THEN
                RAISE EXCEPTION 'Only the current release can be marked published'
                    USING ERRCODE='40001';
            END IF;
        END IF;
    ELSIF TG_TABLE_NAME='config_ontology_aliases' THEN
        IF NOT admin THEN
            RAISE EXCEPTION 'ACCOUNTADMIN must be the active role' USING ERRCODE='42501';
        END IF;
        IF head_revision IS NOT NULL THEN
            SELECT r.payload INTO prev FROM nova_meta.config_ontology_aliases r
                WHERE r.id=NEW.id AND r.revision=head_revision;
        END IF;
        SELECT count(*),min(r.payload::text)::jsonb INTO target_heads,target
            FROM nova_meta.config_ontology_heads h
            JOIN nova_meta.config_ontology_releases r ON r.id=h.id AND r.revision=h.revision
            WHERE h.kind='config_ontology_releases' AND h.id=NEW.payload->>'release_id';
        IF target_heads<>1 OR coalesce(target->>'status','') NOT IN ('PREPARED','PUBLISHED')
           OR NEW.payload->>'previous_release_id' IS DISTINCT FROM prev->>'release_id'
           OR (target->>'status'='PREPARED'
               AND target->>'base_release_id' IS DISTINCT FROM prev->>'release_id') THEN
            RAISE EXCEPTION 'Another release was published; prepare again' USING ERRCODE='40001';
        END IF;
    END IF;
    RETURN NEW;
END
$body$;

-- Keeps config_ontology_heads equal to the newest revision of each record.
-- It runs as the owner so no workload can write heads directly; it accepts
-- only rows that passed the statement and row guards above.
CREATE FUNCTION nova_meta.ontology_advance_head() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF TG_TABLE_SCHEMA<>'nova_meta' OR TG_OP<>'INSERT' OR TG_TABLE_NAME NOT IN (
        'config_ontology_domains','config_ontology_concepts','config_ontology_relations',
        'config_ontology_representations','config_ontology_proposals','config_ontology_releases',
        'config_ontology_aliases','config_ontology_derived','config_ontology_compilations',
        'config_ontology_eval_cases','config_ontology_eval_runs','config_ontology_eval_results')
       OR NOT (session_user='nova_meta_migrator'
               OR (session_user IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
                   AND nova_meta.ontology_actor_bound())) THEN
        RAISE EXCEPTION 'Invalid ontology head source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.config_ontology_heads AS h (kind,id,revision,branches)
    VALUES (TG_TABLE_NAME,NEW.id,NEW.revision,1)
    ON CONFLICT (kind,id) DO UPDATE SET
        revision=greatest(h.revision,EXCLUDED.revision),
        branches=CASE WHEN EXCLUDED.revision>h.revision THEN 1
                      WHEN EXCLUDED.revision=h.revision THEN h.branches+1
                      ELSE h.branches END;
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.ontology_advance_head() FROM PUBLIC;

-- Governed changes write a success audit row in the same transaction. The
-- derived layer, compilations and evaluation rows are compiler and scoring
-- output, as in the source, and are not audited.
CREATE FUNCTION nova_meta.audit_ontology_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog SET row_security=on AS $body$
BEGIN
    IF session_user='nova_meta_migrator' THEN RETURN NULL; END IF;
    IF session_user NOT IN ('nova_meta_web','nova_meta_agent','nova_meta_automation')
       OR NOT nova_meta.actor_context_valid() OR TG_TABLE_SCHEMA<>'nova_meta' OR TG_OP<>'INSERT'
       OR TG_TABLE_NAME NOT IN ('config_ontology_domains','config_ontology_concepts',
           'config_ontology_relations','config_ontology_representations',
           'config_ontology_proposals','config_ontology_releases','config_ontology_aliases') THEN
        RAISE EXCEPTION 'Invalid ontology audit source' USING ERRCODE='42501';
    END IF;
    INSERT INTO nova_meta.audit_log (query_id,event_type,object_type,object_name,action,status,
        decision,metadata_table,metadata_key,metadata_revision)
    VALUES (gen_random_uuid()::text,'metadata',TG_TABLE_NAME,NEW.id,
        CASE WHEN NEW.revision=1 THEN 'CREATE' ELSE 'UPDATE' END,'SUCCESS','ALLOW',TG_TABLE_NAME,
        jsonb_build_object('id',NEW.id,'revision',NEW.revision),NEW.revision);
    RETURN NULL;
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.audit_ontology_change() FROM PUBLIC;

DO $triggers$
DECLARE tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['config_ontology_domains','config_ontology_concepts',
        'config_ontology_relations','config_ontology_representations','config_ontology_proposals',
        'config_ontology_releases','config_ontology_aliases','config_ontology_derived',
        'config_ontology_compilations','config_ontology_eval_cases','config_ontology_eval_runs',
        'config_ontology_eval_results'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE '
            'ON nova_meta.%I FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.ontology_write_guard()',
            tbl||'_write_guard', tbl);
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT ON nova_meta.%I FOR EACH ROW '
            'EXECUTE FUNCTION nova_meta.ontology_row_guard()', tbl||'_row_guard', tbl);
        EXECUTE format('CREATE TRIGGER %I AFTER INSERT ON nova_meta.%I FOR EACH ROW '
            'EXECUTE FUNCTION nova_meta.ontology_advance_head()', tbl||'_head', tbl);
    END LOOP;
    FOREACH tbl IN ARRAY ARRAY['config_ontology_domains','config_ontology_concepts',
        'config_ontology_relations','config_ontology_representations','config_ontology_proposals',
        'config_ontology_releases','config_ontology_aliases'] LOOP
        EXECUTE format('CREATE TRIGGER %I AFTER INSERT ON nova_meta.%I FOR EACH ROW '
            'EXECUTE FUNCTION nova_meta.audit_ontology_change()', tbl||'_audit', tbl);
    END LOOP;
END
$triggers$;
CREATE TRIGGER config_ontology_heads_write_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.config_ontology_heads FOR EACH STATEMENT
    EXECUTE FUNCTION nova_meta.ontology_write_guard();
