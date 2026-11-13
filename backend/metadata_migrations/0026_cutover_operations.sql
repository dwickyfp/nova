-- Append-only journal of operator cutover commands (freeze, publish epoch,
-- unfreeze). Each row commits in the same transaction as the deployment-state
-- change it records, or records a refusal that changed nothing.
CREATE TABLE nova_meta.cutover_operations (
    operation_id uuid PRIMARY KEY,
    sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    operation text NOT NULL CHECK (operation IN ('freeze', 'publish_epoch', 'unfreeze')),
    outcome text NOT NULL CHECK (outcome IN ('committed', 'refused')),
    refusal text CHECK (refusal ~ '^[a-z_]+$' AND char_length(refusal) <= 64),
    operator text NOT NULL
        CHECK (operator ~ '^[A-Za-z0-9._@-]+$' AND char_length(operator) <= 128),
    reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 500),
    app_version text NOT NULL CHECK (app_version ~ '^[0-9a-f]{40}$'),
    session_login text NOT NULL DEFAULT session_user,
    schema_version bigint NOT NULL CHECK (schema_version > 0),
    epoch_before uuid,
    epoch_after uuid,
    maintenance_before boolean NOT NULL,
    maintenance_after boolean NOT NULL,
    freeze_operation_id uuid REFERENCES nova_meta.cutover_operations (operation_id),
    manifest_sha256 text CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((outcome = 'refused') = (refusal IS NOT NULL)),
    CHECK (outcome = 'committed' OR (
        epoch_after IS NOT DISTINCT FROM epoch_before
        AND maintenance_after = maintenance_before
    )),
    CHECK (operation <> 'publish_epoch' OR outcome = 'refused' OR (
        manifest_sha256 IS NOT NULL AND freeze_operation_id IS NOT NULL
        AND epoch_before IS NULL AND epoch_after IS NOT NULL
        AND maintenance_after
    )),
    CHECK (operation <> 'freeze' OR outcome = 'refused' OR maintenance_after),
    CHECK (operation <> 'unfreeze' OR outcome = 'refused' OR (
        NOT maintenance_after AND epoch_after IS NOT NULL
    ))
);

CREATE FUNCTION nova_meta.cutover_operations_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    RAISE EXCEPTION 'Cutover operations are append-only' USING ERRCODE = '42501';
END
$body$;
REVOKE ALL ON FUNCTION nova_meta.cutover_operations_append_only() FROM PUBLIC;

CREATE TRIGGER cutover_operations_append_only
    BEFORE UPDATE OR DELETE ON nova_meta.cutover_operations
    FOR EACH ROW EXECUTE FUNCTION nova_meta.cutover_operations_append_only();
CREATE TRIGGER cutover_operations_no_truncate
    BEFORE TRUNCATE ON nova_meta.cutover_operations
    FOR EACH STATEMENT EXECUTE FUNCTION nova_meta.cutover_operations_append_only();

REVOKE ALL ON nova_meta.cutover_operations FROM PUBLIC;
GRANT SELECT ON nova_meta.cutover_operations TO nova_meta_readonly;
