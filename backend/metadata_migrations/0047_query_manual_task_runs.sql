-- EXECUTE TASK typed in a SQL worksheet runs in the query workload, which may
-- create, change and drop task definitions (0016) and enqueue durable jobs
-- (0005) but could not record the manual graph run itself: every EXECUTE TASK
-- from the worksheet failed with "Could not queue task run". Give the query
-- workload the same manual-run insert the web workload has. Runs are still
-- bound to the calling actor by the policy check, and state changes stay with
-- the scheduler and task workloads.
GRANT INSERT (id,graph_id,trigger_type,state,overlap_policy,wal_marks,execution_user,
              execution_role,execution_session_id,job_id)
    ON nova_meta.config_task_graph_runs TO nova_meta_query;
ALTER POLICY config_task_graph_runs_insert ON nova_meta.config_task_graph_runs
    TO nova_meta_web, nova_meta_query, nova_meta_scheduler, nova_meta_task;
