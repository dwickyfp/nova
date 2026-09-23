# Stages, scheduled tasks, and access

Keywords: stage, file, berkas, csv, parquet, task, jadwal, terjadwal, schedule,
role, user, akses, permission, izin, ACCOUNTADMIN.

Use @stage references for files. Load stage-query for file queries and copy-into
for loading or exporting data. Describe storage by its configured connection
name. Do not ask users for storage credentials or replace @stage with provider URLs.

For scheduled work, load create-task. Nova intercepts CREATE TASK and manages task
metadata and execution; it is not ordinary StarRocks CREATE TASK DDL. Use that
playbook for supported scheduling and dependency syntax. A schedule definition
does not prove a task ran successfully. Verify runtime state before reporting success.

Nova authenticates users against StarRocks. Requests execute with the caller's
authorized identity and active role. Semantic models define business meaning;
they do not bypass database access checks. A tool approval is not a database grant.

ACCOUNTADMIN is protected. Never propose dropping, altering, or revoking its
privileges. Account creation should use the create-user playbook with password
placeholders; no real or generated password belongs in assistant output.

Implementation sources: assistant/skill_library/stage-query.md,
assistant/skill_library/create-task.md, assistant/skill_library/accountadmin-guardrail.md,
assistant/security.py, agents/semantic/access.py.
