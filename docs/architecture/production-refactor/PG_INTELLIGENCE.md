# Intelligence metadata port

Revisions 0022 and 0025 prepare all 36 tables owned by `intelligence` in
`nova_meta`, with `IntelligenceMetadataRepository`
(`backend/app/modules/intelligence/metadata_repository.py`) as the prepared
replacement for their `NOVA_SYSTEM` paths in the Semantic View service,
`IntelligenceRepository`, the Business Event revision store, News feedback,
`SearchService`, `EntityRegistry`, `FeatureStoreService`, the legacy semantic
migration and the agent semantic usage log. Running API, agent, task and
maintenance code still uses `NOVA_SYSTEM`; nothing imports the repository.

Revision 0022:

- Semantic Views: `CONFIG_SEMANTIC_VIEWS`, `CONFIG_SEMANTIC_VIEW_VERSIONS`.
- The fourteen `ENGINE_TABLES` revision stores (context nodes and edges,
  monitors, observations, news, investigations, decisions, decision events and
  outcomes, actions, action events, comparisons, editions, stories) and the three
  Business Event stores (`CONFIG_INTELLIGENCE_SIGNALS`, `_INCIDENTS`,
  `_INVESTIGATION_UPDATES`).
- `CONFIG_INTELLIGENCE_STORY_FEEDBACK`.
- AI Search: `CONFIG_AI_SEARCH_INDEXES`, `_VERSIONS`, `_SYNC_STATE`,
  `AI_SEARCH_QUERY_LOG`, `AI_SEARCH_EVAL_RUNS`.

Revision 0025:

- `CONFIG_ENTITIES`.
- The feature store: `CONFIG_FEATURE_VIEWS`, `_VIEW_VERSIONS`, `_GROUPS`,
  `_GROUP_VERSIONS`, `_TRAINING_SETS`, `_ML_LINKS`.
- The legacy agent semantic models (`CONFIG_SEMANTIC_MODELS`,
  `CONFIG_SEMANTIC_MODEL_VERSIONS`, `CONFIG_SEMANTIC_VERIFIED_QUERIES`) and
  `AUDIT_SEMANTIC_QUERY_USAGE`.

## Workloads and access

Callers were traced to four process roles: the API (`web`), the Studio agent
loop and its tools (`agent`), Studio automations (`automation`), scheduled
Intelligence handlers run by the task worker (`task`: monitors, outcomes,
consolidation, News editions) and the maintenance worker (`maintenance`: the
Search build poller and the News proof refresher). No SQL-proxy or query path
touches these tables. Every other role in `WORKLOAD_ROLES` has no grant.

| Tables | Read | Write |
| --- | --- | --- |
| Semantic Views and versions | web, agent, automation, task, maintenance | web, agent |
| Scoped revision stores (12) | web, agent, automation, task; rows of the bound principal and role | web, agent, automation, task (append only) |
| Editions, stories | web, agent, task, maintenance | web, task (append only) |
| Business Event stores | web, agent, automation, task | same (append only) |
| Story feedback | web, own rows | web, own rows |
| Search indexes | web, agent, maintenance | web |
| Search versions | web, agent, maintenance | web creates and activates; maintenance creates automatic versions and owns build claims/results |
| Search sync state | web, agent, maintenance | created by web/maintenance; progress by maintenance |
| Search query log / evaluations | web(/agent), own rows | as the caller |
| Entities | web, agent, maintenance | web |
| Feature Views, Groups and their versions | web | web |
| Training sets / model links | web; training sets own rows only | web, as the caller |
| Legacy semantic models and versions | web, maintenance | none (import only) |
| Legacy verified queries | web, maintenance; agent own rows | agent: usage counters only |
| Semantic query usage | web, agent; own rows of the active role | web, agent, as the caller |

All tables use forced RLS through a bound actor (`intelligence_actor_bound`), a
statement write guard (active epoch for workloads; the migrator acting as owner
only under maintenance) and row guards. Whether a caller may see a Semantic View
(visibility, source and entity access), a story (News access proofs, kept in
Redis) or a decision stays the owning service's and the engine's check: PG does
not shortcut it. Two shared lookups the services authorize themselves — a
decision named with its owner, and an action for its reviewer — go through one
fixed `SECURITY DEFINER` function that returns only the two newest revisions of
that id, so a branch is still detectable (Wave E's "narrow service policy").

## Gaps closed in the new store

- **Semantic View versions (Wave H).** MAX+1 allocation runs in the inserting
  transaction; a writer that read an older latest version (or names a gap) gets
  a conflict. Definition and fingerprint are immutable on every version.
- **Publication.** A version is published only from `VALIDATED` with a passing
  report; the view pointer moves with a `metadata_revision` CAS plus a
  null-safe expected active version (`IS NOT DISTINCT FROM`, the source `<=>`),
  and can only point to a published version. Published versions keep their
  validation proof and activation time and can only be retired. Versions are
  deleted only after their view (DROP). Every view change advances
  `metadata_revision`; News settings record who changed them.
- **Revision stores (Wave E).** The source key `(id, revision, operation_id)`
  is kept, so imported same-revision branches survive for reconciliation;
  runtime revisions are unique per id, must extend the newest revision by
  exactly one, are refused on top of a branch, and must match their payload
  (`id`, `revision`, scope or actor). A scoped record cannot change principal,
  role or security version, so a guessed id cannot overwrite another scope's
  record; this is checked against every revision of that id, not only the rows
  the writer can read. Rows record `written_by` (the bound principal), because a
  reviewer or a News execution account may append under the record owner's
  scope. History is append-only for every workload.
- **Business Events (Wave H).** Deployment-wide: the actor, role and security
  version of a new revision must be the bound writer's.
- **Search builds.** Versions and their sync state are created once (MAX+1). A
  build claim — including a takeover of a lapsed build by the poller — advances
  `build_generation`; progress and results require the current generation while
  building, so a builder that lost its claim can neither record progress nor
  finish. Failed versions are terminal; activation is the API's, builds the
  maintenance worker's. Index and embedding payloads stay in the engine.
- **Query log.** The source is a `DUPLICATE KEY` table; each row gets a
  `record_id` and keeps its source id.

- **Entities (0025).** Registered once per scoped name, owned by the creator;
  identity, relation and keys never change, and an entity is only retired, under
  a revision CAS.
- **Feature store (0025).** View and group versions are allocated MAX+1 in the
  transaction; view versions finish once (`READY` or terminal `FAILED`) and only
  built versions are served; group members are immutable and must pin built
  versions of views over the group's entity. The source's "pinned by a group"
  check was an unlocked read; now a group version share-locks each pinned view
  row and DROP locks the row before checking every group version, so a drop and
  a new pin never both succeed. Versions go only with their view or group.
  Training sets belong to their creator; a model can only be linked to the
  caller's own training set at its group version.
- **Legacy semantic models (0025).** No runtime writer creates or edits them
  (`CONFIG_SEMANTIC_MODELS` is historical and `save_version` has no caller);
  they are kept read-only for the Semantic View migration. Verified-query
  content is immutable; the agent's usage counters move atomically, only
  forward, never with more successes than uses, and only on the caller's rows.
- **Semantic usage (0025).** Append-only, written as the bound caller (owner,
  role and security version), read back per owner and active role.

Success audit rows are written for Semantic View and version changes and for
Search index creation, version creation and activation, entity registration and retirement, Feature View and
Group definitions, versions and activation, training sets and model links.
Build progress,
feedback, query and usage logs, verified-query counters and the revision stores (append-only history that records
its writer) are not audited, as in the source.

## Findings not fixable in storage

- A scoped revision's owner scope cannot be bound to the writer: reviewers append
  to the requester's action and News handlers write under the execution account
  with `security_context_version=1`. PG enforces scope continuity and records the
  writer; the services must keep constructing scopes from validated sessions.
- The owner read policy uses principal and active role, not the security
  version, because scheduled handlers read records stored under an earlier
  version; the version stays a repository query parameter, as in the source.
- Editions and stories are readable by any bound reader of the allowed
  workloads; reader authorization remains the News access-proof check.
- Imported revision branches block further writes to that id until an operator
  reconciles them; the import must emit a redacted conflict manifest (not built).
- Search source fingerprinting and builds still read user data through the
  system adapter (R15); this port changes only control rows.
- Feature materialization and Search builds read user data through the system
  adapter (R15); only their control rows move here.
- The one-time legacy semantic migration runs at API startup today (Wave H says
  it must not run implicitly on each worker); at cutover it should become an
  explicit maintenance or operator job, which is why `maintenance` can read the
  legacy rows.
- Legacy usage rows may lack role and security version; they stay readable only
  to the importer path, not to the per-role usage reader.
- Semantic View and Search timestamps: runtime rows use database time; imported
  `created_at`/`updated_at` keep source engine time, whose UTC meaning must be
  verified by the import transform.

## Remaining work

- Wire the repository into the Semantic View service, `IntelligenceRepository`,
  the Business Event store, News feedback, `SearchService`, `EntityRegistry`,
  `FeatureStoreService`, the legacy semantic migration, the agent semantic usage
  path and the maintenance worker at cutover, removing the Redis write locks that this store replaces.
- Import conversions and conflict manifests (branches, duplicate query-log ids,
  legacy statuses and visibility, interrupted feature builds), and the Semantic View/Search caller ports.

Validation is recorded in
[the Intelligence metadata checkpoint](../../reports/production-refactor/intelligence-metadata/README.md).
