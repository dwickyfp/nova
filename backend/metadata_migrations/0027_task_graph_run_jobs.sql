-- Each runtime graph run names the durable `task.graph` job that delivers it.
-- The run and its job (plus the job's outbox wakeup) commit in one transaction,
-- so a worker that claims the job resolves its run from PG alone; Redis only
-- carries the wakeup. Imported history keeps a null job.
ALTER TABLE nova_meta.config_task_graph_runs
    ADD COLUMN job_id uuid UNIQUE REFERENCES nova_meta.job_queue (id);

GRANT INSERT (job_id) ON nova_meta.config_task_graph_runs
    TO nova_meta_web, nova_meta_scheduler, nova_meta_task;

-- The job reference is fixed when the run is created.
CREATE FUNCTION nova_meta.task_graph_run_job_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $body$
BEGIN
    IF NEW.job_id IS DISTINCT FROM OLD.job_id THEN
        RAISE EXCEPTION 'A graph run keeps its job' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$body$;
CREATE TRIGGER config_task_graph_runs_job_guard BEFORE UPDATE OF job_id
    ON nova_meta.config_task_graph_runs FOR EACH ROW
    EXECUTE FUNCTION nova_meta.task_graph_run_job_guard();
