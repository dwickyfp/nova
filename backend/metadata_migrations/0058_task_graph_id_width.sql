-- A task graph id is the root task's qualified name, database.schema.name
-- (task_graph_id). A database or schema name may be 128 octets and a task name
-- 256, so the id may be 514 octets. Revision 0016 kept graph ids in 64 octets:
-- a task in a database with a longer name could be created but never queued
-- ("Could not queue task run"), and a pipe in such a database could not be
-- created at all. Widen both graph id columns; run ids stay UUIDs.
ALTER TABLE nova_meta.config_task_edges
    DROP CONSTRAINT config_task_edges_graph_id_check,
    ALTER COLUMN graph_id TYPE varchar(514),
    ADD CONSTRAINT config_task_edges_graph_id_check CHECK (octet_length(graph_id)<=514);

ALTER TABLE nova_meta.config_task_graph_runs
    DROP CONSTRAINT config_task_graph_runs_graph_id_check,
    ALTER COLUMN graph_id TYPE varchar(514),
    ADD CONSTRAINT config_task_graph_runs_graph_id_check CHECK (octet_length(graph_id)<=514);
