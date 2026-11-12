# Assistant, access-control, Superset, migration and AI usage metadata port

Revision 0024 prepares the 14 remaining small control tables in `nova_meta`:

| Domain | Tables | Repository |
| --- | --- | --- |
| Assistant | `CONFIG_ASSISTANT_THREADS`, `CONFIG_ASSISTANT_MESSAGES`, `CONFIG_ASSISTANT_UI_STATE`, `CONFIG_I18N_MESSAGES` | `assistant/metadata_repository.py` |
| Access control | `CONFIG_SECURITY_ROLE_PROJECTIONS`, `CONFIG_SECURITY_POLICIES`, `CONFIG_DATA_SCOPE_ASSIGNMENTS`, `CONFIG_BUSINESS_POLICIES` | `access_control/metadata_repository.py` |
| Superset | `CONFIG_BI_AUTHOR_ROLES` (plus the new `config_bi_author_role_set`), `CONFIG_DATAVIZ_PUBLICATIONS`, `CONFIG_SEMANTIC_BI_DATASETS` | `superset/metadata_repository.py` |
| Migration | `CONFIG_MIGRATION_SOURCES`, `CONFIG_MIGRATION_JOBS` | `migration/metadata_repository.py` |
| AI functions | `USAGE_LLM_FUNCTIONS` | `llm_functions/metadata_repository.py` |

The application callers now use these PG repositories. The latest audit also
ported BI author-role reads/writes, Dataviz publication and context, Semantic
View dataset mappings, and preview provenance from Assistant messages. Query
history and user last-login readers use the actor-bound audit store. Startup
hooks only probe the installed schema and epoch. Development cutover is still
pending; see the [current delivery report](../../reports/production-refactor/importer-agent-cutover/README.md).

Publication updates compare both the previous status and update timestamp.
An advisory transaction lock prevents two operation IDs from claiming the same
owner/idempotency scope. Terminal outcomes remain immutable, and role/version
changes refuse continuation. Reads of author roles have no process-local cache,
so revocation is reflected on the next identity check.

PG holds Nova's own configuration and journals. Ranger and the patched
StarRocks FE remain the authority for user data: a projection, policy state or
data-scope row records what Nova asked them to do and never grants access.

## Access

Every repository runs in the caller's actor-bound metadata transaction and
re-checks that binding. Access is per workload, through a bound actor, never
anonymous. Tables have forced RLS; the migrator writes only under maintenance.

| Table | Read | Write |
| --- | --- | --- |
| Threads, messages, UI state | web, agent, automation: own rows only; web administrators read for usage monitoring; task service principal reads learning-enabled threads and learning-queued questions | owner through web, agent, automation (delete through web only); task service principal records learning steps and completion |
| Translations | web, agent, automation | same (shared cache) |
| Role projections, policy state, data scopes | web | web in `ACCOUNTADMIN`, `SECURITYADMIN` or `security_admin` |
| Business policy | web, agent, automation | web in `ACCOUNTADMIN`, insert only |
| BI author roles and set revision | web | web in `ACCOUNTADMIN` |
| Dataviz publications | web, agent, automation: own rows; a fixed projection of successful business context by chart | owner through web, agent, automation; never deleted |
| Semantic dataset map | web, agent, automation | web |
| Migration sources | web, ingestion | web, a person (not a service), insert only |
| Migration jobs | web: own jobs; ingestion: all | web queues and deletes own finished jobs; ingestion claims, advances, interrupts and prunes |
| AI function usage | web administrators | web, append only |

Query, proxy, scheduler, ml, notebook, maintenance and outbox have no grant.

## Rules the store enforces

- **Per-principal conversations.** A person reads and writes only their own
  threads, messages and UI state; another person's thread id is a conflict,
  not a read. Turns are append-only: content, role, tokens, stamp and
  attachments never change. Only the owner's feedback on an answer and the
  learning progress of a pending question change later.
- **Idempotent append.** A message is created once by id. An identical retry
  returns the stored turn; a different turn under the same id is a conflict.
  Sequence numbers are allocated under a row lock on the thread, so concurrent
  appends are gap-free and distinct (runtime `(thread_id, seq)` is unique).
- **Bound consent context.** A stamped turn must name the bound principal and
  active role. UI state is saved in the active role and security version of the
  session and must reference the owner's own answer; another role neither reads
  nor overwrites it. It changes only from the revision read.
- **Governed configuration.** Role projections, policy state and data scopes
  change from the revision read (CAS, `409` on conflict), name the acting
  principal and role, and write a success audit row in the same transaction.
  `ACCOUNTADMIN` projections are refused. A data-scope id must be the source
  digest of principal, role and dimension; values are a JSON string array and
  are kept out of audit rows.
- **Business policy.** Every save is an immutable revision following the one in
  force; concurrent saves from the same revision have one winner. Imported
  revisions with the same number are kept and refused on read, as the source
  refuses them.
- **Author roles.** The set changes as a whole from the set revision read; a
  role row changes only in the transaction that advanced that revision.
- **Publication journal.** An operation is journaled once by id and continued
  only by its owner in the role and session version that started it, from the
  status last read. `SUCCEEDED` and `FAILED_FINAL` are frozen. Dashboards follow
  roles in Superset (`DASHBOARD_RBAC`); nothing here shares one with a person.
- **Credentials.** A migration source stores only a secret-store reference
  (`aws://…`, `aws-sm://…`, `secretsmanager://…`, `aws_secrets_manager://…`
  or a Secrets Manager ARN) or none. Anything else is refused and the error does
  not echo the value. Job requests and results with a credential-named member
  (`password`, `secret`, `token`, `api_key`, `private_key`, `credential`) are
  refused. Source audit rows carry the id only.
- **Leased migration jobs.** A claim takes the next lease generation; heartbeat,
  progress and outcome require it. Terminal jobs are frozen; only finished jobs
  are deleted, and the worker prunes finished read jobs after a day.
- **Append-only usage.** Each flushed total is a new record with server time and
  a new `(recorded_at, record_id)`; no workload changes or deletes one. Imported
  rows keep their legacy `usage_id`, duplicates included.

Audit covers governed changes only (access-control rows, business policy, BI
author roles and set, migration source registration). Conversations, the
translation cache, the journal, the dataset map, job progress and usage are not
audited, as in the source.

## Findings not fixed in storage

1. `liked_answers` (ontology learning) reads answers across people. Only web
   administrators and the task learning path (pending questions) read across
   people here, so that caller needs an approved identity at the port.
2. `AgentRepository.migrate_legacy_skill_authors` rewrites `agent_id` on other
   people's threads and messages. No workload may do that here; the rebinding
   belongs to the import.
3. The source job-status route reads a migration job by id with no owner check.
   The store limits web to the owner; the caller port must use that transaction.
4. Migration source registration has no role gate in the router and accepts
   bare secret names. The store refuses bare names; callers must normalize them
   to `aws://…`. Imported bare references are kept as they were.
5. Projection and policy `message` columns hold exception text from Ranger and
   StarRocks. Redaction stays with the caller.
6. `UsageMeter.flush` runs in the web process without an actor. It needs an
   explicit service principal at cutover. Usage partitioning and retention are
   not designed yet.
7. AI usage monitoring authorizes by its own `READ_ROLES`; the store admits
   `ACCOUNTADMIN`, `SECURITYADMIN`, `user_admin` and `security_admin`. The two
   must agree at the port.
8. The Semantic View dataset map has no steward check in storage; authorization
   stays in the semantic publish service.
9. `dataviz_published_contexts` returns context for any chart id it is given.
   Callers must pass only charts Superset authorized for the viewer.
10. `CONFIG_I18N_MESSAGES` is absent from the inspected live schema; fresh-install
    behavior is verified, import is not needed unless it appears.
11. Timestamp provenance and typed import are packaged and passed two complete
    interrupted/resumed rehearsals. Final export/import and epoch activation
    remain pending; see the current delivery report.

Validation is recorded in
[the checkpoint](../../reports/production-refactor/assistant-policy-bi-metadata/README.md).


The caller tests establish PG storage and authorization behavior. Superset
external effects still need crash/reconciliation acceptance, including uncertain
dataset creation and reconciliation between the Ranger author policy and the
PG author set. A PG transaction does not make those two systems atomic.
