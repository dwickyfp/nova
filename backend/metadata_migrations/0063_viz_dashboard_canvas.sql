-- Dataviz dashboards, schema 3: the free-form canvas (ADR
-- docs/architecture/native-dataviz/ADR-001-free-canvas.md).
--
-- New dashboard revisions store a schema-3 definition. Existing rows are not
-- touched: immutable schema-2 revisions keep their definition and digest and
-- are upcast when they are read. Datasets and charts stay schema 2.
ALTER TABLE nova_meta.viz_revisions
    DROP CONSTRAINT viz_revisions_schema_version_check;

ALTER TABLE nova_meta.viz_revisions
    ADD CONSTRAINT viz_revisions_schema_version_check
        CHECK (schema_version IN (2, 3)
               AND schema_version = COALESCE((definition->>'schema_version')::integer, 2));
