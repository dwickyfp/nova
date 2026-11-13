-- Monitoring reads only lease presence/deadline; holder and fencing token stay private.
GRANT SELECT (name, expires_at) ON nova_meta.scheduler_leases TO nova_meta_web;
CREATE POLICY scheduler_monitor_actor ON nova_meta.scheduler_leases AS RESTRICTIVE
    FOR SELECT TO nova_meta_web USING (nova_meta.actor_context_valid());
CREATE POLICY scheduler_monitor_read ON nova_meta.scheduler_leases
    FOR SELECT TO nova_meta_web USING (
        name = 'task.scheduler'
        AND current_setting('nova.principal_type', true) = 'USER'
        AND current_setting('nova.active_role', true) IN
            ('ACCOUNTADMIN', 'SECURITYADMIN', 'user_admin', 'security_admin')
    );
