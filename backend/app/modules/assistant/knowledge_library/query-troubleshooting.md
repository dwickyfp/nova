# Query troubleshooting

Keywords: debug, error, gagal, lambat, penyebab, permission, izin, query, diagnosis.

Begin with the actual SQL, error text, active database/schema, and what the user
expected. A question about a slow query is a troubleshooting task, not a request
to search business entities. Do not fabricate an error or claim a measured speedup.

Use the debug-sql playbook to distinguish parsing, translation, policy, and engine
errors. Preserve Nova @stage references. Never reveal translated storage paths
or injected credentials. Inspect schema with permitted read-only tools when
column or object names need verification. Explain a query plan only after it has
been provided or retrieved; a proposed optimization is not a measured improvement.

If access is denied, explain that execution uses the authenticated user's access.
Do not retry under another identity or propose bypassing protected-object rules.
Do not infer that an object is absent merely because the caller cannot see it.

After inspection, check whether the result answers the original question. Schema
metadata can guide a follow-up query, but does not establish business totals.
If the turn budget prevents completing the investigation, state what was verified
and what remains unknown.

Implementation sources: assistant/tools/query_execute.py, assistant/tools/policy.py,
assistant/service.py, assistant/skill_library/debug-sql.md.
