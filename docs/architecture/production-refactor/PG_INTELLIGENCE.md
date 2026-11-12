# Intelligence metadata port

Revision 0022 prepares 25 of the 36 tables owned by `intelligence` in
`nova_meta`, with `IntelligenceMetadataRepository`
(`backend/app/modules/intelligence/metadata_repository.py`) as the prepared
replacement for their `NOVA_SYSTEM` paths in the Semantic View service,
`IntelligenceRepository`, the Business Event revision store, News feedback and
`SearchService`. Running API, agent, task and maintenance code still uses
`NOVA_SYSTEM`; nothing imports the repository.

Prepared here:

- Semantic Views: `CONFIG_SEMANTIC_VIEWS`, `CONFIG_SEMANTIC_VIEW_VERSIONS`.
- The fourteen `ENGINE_TABLES` revision stores (context nodes and edges,
  monitors, observations, news, investigations, decisions, decision events and
  outcomes, actions, action events, comparisons, editions, stories) and the three
  Business Event stores (`CONFIG_INTELLIGENCE_SIGNALS`, `_INCIDENTS`,
  `_INVESTIGATION_UPDATES`).
- `CONFIG_INTELLIGENCE_STORY_FEEDBACK`.
- AI Search: `CONFIG_AI_SEARCH_INDEXES`, `_VERSIONS`, `_SYNC_STATE`,
  `AI_SEARCH_QUERY_LOG`, `AI_SEARCH_EVAL_RUNS`.

Not prepared yet (still `NOT_STARTED`): the feature store (`CONFIG_FEATURE_VIEWS`,
`_VIEW_VERSIONS`, `_GROUPS`, `_GROUP_VERSIONS`, `_TRAINING_SETS`, `_ML_LINKS`),
`CONFIG_ENTITIES`, the agent semantic models (`CONFIG_SEMANTIC_MODELS`,
`_MODEL_VERSIONS`, `_VERIFIED_QUERIES`) and `AUDIT_SEMANTIC_QUERY_USAGE`.

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

Success audit rows are written for Semantic View and version changes and for
Search index creation, version creation and activation. Build progress,
feedback, query logs and the revision stores (append-only history that records
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
- Semantic View and Search timestamps: runtime rows use database time; imported
  `created_at`/`updated_at` keep source engine time, whose UTC meaning must be
  verified by the import transform.

## Remaining work

- Prepare the eleven tables listed above.
- Wire the repository into the Semantic View service, `IntelligenceRepository`,
  the Business Event store, News feedback, `SearchService` and the maintenance
  worker at cutover, removing the Redis write locks that this store replaces.
- Import conversions and conflict manifests (branches, duplicate query-log ids,
  legacy statuses and visibility), and the Semantic View/Search caller ports.

Validation is recorded in
[the Intelligence metadata checkpoint](../../reports/production-refactor/intelligence-metadata/README.md).
