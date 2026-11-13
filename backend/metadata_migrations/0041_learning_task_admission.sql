-- Reconciliation may reconstruct private learning work from a pending source.
-- The task login receives no general SQL/Python definition authority.
GRANT INSERT (id,name,database_name,schema_name,definition,schedule_kind,schedule_expr,
    timezone,when_expr,overlap_policy,owner_role,created_by,handler,handler_config,
    lifecycle_state,when_ast) ON nova_meta.config_tasks TO nova_meta_task;

CREATE POLICY config_tasks_learning_admission ON nova_meta.config_tasks
    FOR INSERT TO nova_meta_task WITH CHECK (
        nova_meta.task_actor_bound()
        AND current_setting('nova.principal_type',true)='SERVICE'
        AND current_setting('nova.principal',true)='nova-task_orchestration'
        AND current_setting('nova.active_role',true)='NOVA_WORKER'
        AND handler='intelligence.consolidate'
        AND name ~ '^intelligence_learning_[0-9a-f]{32}$'
        AND schedule_kind='manual' AND schedule_expr IS NULL AND timezone='UTC'
        AND database_name IS NULL AND schema_name IS NULL AND definition=''
        AND when_expr IS NULL AND when_ast IS NULL
        AND overlap_policy='skip' AND lifecycle_state='STARTED'
        AND handler_config->>'version'='1'
        AND EXISTS (
            SELECT 1 FROM nova_meta.config_assistant_messages m
            JOIN nova_meta.config_assistant_threads t
                ON t.thread_id=m.thread_id AND t.user_name=m.user_name
            WHERE m.message_id=handler_config->>'source_message_id'
                AND m.thread_id=handler_config->>'source_thread_id'
                AND m.agent_id=handler_config->>'agent_id'
                AND m.user_name=config_tasks.created_by
                AND m.security_context->>'active_role'=config_tasks.owner_role
                AND m.security_context=handler_config->'scope'
                AND m.role='user' AND m.learning_state='pending' AND t.learning_enabled
        )
    );
