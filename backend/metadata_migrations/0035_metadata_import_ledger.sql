-- Import receipts commit with their rows. No application role can read or write
-- this ledger; it is available only to the one-shot migrator during cutover.
CREATE TABLE nova_meta.metadata_import_runs (
    id uuid PRIMARY KEY,
    snapshot_id uuid NOT NULL UNIQUE,
    export_sha256 text NOT NULL CHECK (export_sha256 ~ '^[0-9a-f]{64}$'),
    contract_sha256 text NOT NULL CHECK (contract_sha256 ~ '^[0-9a-f]{64}$'),
    provenance_sha256 text NOT NULL CHECK (provenance_sha256 ~ '^[0-9a-f]{64}$'),
    ledger_sha256 text NOT NULL CHECK (ledger_sha256 ~ '^[0-9a-f]{64}$'),
    schema_version integer NOT NULL CHECK (schema_version>0),
    freeze_operation_id uuid NOT NULL REFERENCES nova_meta.cutover_operations(operation_id),
    status text NOT NULL DEFAULT 'IMPORTING' CHECK (status IN ('IMPORTING','VALIDATED')),
    validation_sha256 text CHECK (validation_sha256 ~ '^[0-9a-f]{64}$'),
    validation_document bytea,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    validated_at timestamptz,
    CHECK ((status='VALIDATED')=(validation_sha256 IS NOT NULL AND validated_at IS NOT NULL
        AND validation_document IS NOT NULL)),
    CHECK (validation_sha256=encode(sha256(validation_document),'hex'))
);
CREATE TABLE nova_meta.metadata_import_chunks (
    run_id uuid NOT NULL REFERENCES nova_meta.metadata_import_runs(id),
    source_table text NOT NULL CHECK (source_table ~ '^[A-Z][A-Z0-9_]{0,62}$'),
    chunk_index integer NOT NULL CHECK (chunk_index>=0),
    chunk_sha256 text NOT NULL CHECK (chunk_sha256 ~ '^[0-9a-f]{64}$'),
    row_count bigint NOT NULL CHECK (row_count>=0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id,source_table,chunk_index)
);
REVOKE ALL ON nova_meta.metadata_import_runs,nova_meta.metadata_import_chunks FROM PUBLIC;
ALTER TABLE nova_meta.metadata_import_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.metadata_import_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.metadata_import_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE nova_meta.metadata_import_chunks FORCE ROW LEVEL SECURITY;
CREATE POLICY metadata_import_runs_owner ON nova_meta.metadata_import_runs TO nova_meta_owner
    USING (session_user='nova_meta_migrator') WITH CHECK (session_user='nova_meta_migrator');
CREATE POLICY metadata_import_chunks_owner ON nova_meta.metadata_import_chunks TO nova_meta_owner
    USING (session_user='nova_meta_migrator') WITH CHECK (session_user='nova_meta_migrator');
CREATE FUNCTION nova_meta.metadata_import_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF current_user<>'nova_meta_owner' OR session_user<>'nova_meta_migrator' THEN
        RAISE EXCEPTION 'Import requires the migrator' USING ERRCODE='42501';
    END IF;
    PERFORM pg_advisory_xact_lock(5642823696587772993);
    IF NOT EXISTS (SELECT 1 FROM nova_meta.deployment_state
        WHERE singleton AND maintenance AND metadata_epoch IS NULL) THEN
        RAISE EXCEPTION 'Import requires a frozen unpublished epoch' USING ERRCODE='55000';
    END IF;
    IF TG_OP IN ('DELETE','TRUNCATE') OR
        (TG_TABLE_NAME='metadata_import_chunks' AND TG_OP='UPDATE') THEN
        RAISE EXCEPTION 'Import receipts are immutable' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END
$body$;
CREATE TRIGGER metadata_import_runs_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.metadata_import_runs FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.metadata_import_guard();
CREATE TRIGGER metadata_import_chunks_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
    ON nova_meta.metadata_import_chunks FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.metadata_import_guard();

CREATE FUNCTION nova_meta.metadata_import_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $body$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.status<>'IMPORTING' OR NEW.validation_sha256 IS NOT NULL
           OR NEW.validated_at IS NOT NULL OR NEW.validation_document IS NOT NULL THEN
            RAISE EXCEPTION 'An import starts without validation' USING ERRCODE='23514';
        END IF;
    ELSIF OLD.status<>'IMPORTING' OR NEW.status<>'VALIDATED'
          OR (to_jsonb(NEW)-ARRAY['status','validation_sha256','validation_document','validated_at'])
             IS DISTINCT FROM
             (to_jsonb(OLD)-ARRAY['status','validation_sha256','validation_document','validated_at']) THEN
        RAISE EXCEPTION 'An import keeps its identity and validates once' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER metadata_import_run_identity BEFORE INSERT OR UPDATE
    ON nova_meta.metadata_import_runs FOR EACH ROW EXECUTE FUNCTION nova_meta.metadata_import_run_guard();
