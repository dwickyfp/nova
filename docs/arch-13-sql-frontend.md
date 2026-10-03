# Architecture 13: SQL Frontend and Planner

> Nova owns SQL syntax, meaning, policy and execution routing; StarRocks owns physical planning and distributed execution.

## Architecture

```mermaid
flowchart TD
    HTTP[HTTP Query API] --> Lifecycle[QueryService: normalization and hard guard]
    Proxy[MySQL proxy: session commands and variables] --> Lifecycle
    Lifecycle --> Parser[Central ANTLR parser]
    Parser --> AST[Typed AST builder registry]
    AST --> Analysis[Effects and semantic validation]
    Analysis --> Planner[Planner registry]
    Binder[Lazy caller-authorized catalog binder] -. requested metadata .-> Planner
    Capabilities[Cached engine identity and overrides] --> Planner
    Planner --> Rules[Ordered rules: one pass]
    Rules --> Plan[Credential-free execution plan]
    Plan --> Executor[Central executor and confirmation check]
    Executor --> Engine[Late stage authorization, secrets and StarRocks SQL]
    Executor --> Service[Existing Nova services]
    Executor --> Composite[Explicit best effort or engine transaction]
    Engine --> Result[Redacted QueryResult and audit]
    Service --> Result
    Composite --> Result
```

`backend/app/sql_frontend/` contains the request pipeline. `QueryService` retains
normalization, lifecycle, metrics, script sequencing, history and response duties.
It delegates SQL classification, validation, planning and execution.

The service interfaces are synchronous `parse_statement(sql, original_sql=...)`,
asynchronous `SQLPlanner.plan(statement, context)`, and asynchronous
`SQLExecutor.execute(plan, context)`. Execution returns the existing `QueryResult`.

## Implementation

| Package | Responsibility |
| --- | --- |
| `parser.py`, `source.py`, `errors.py` | Case-insensitive ANTLR input, hidden tokens, source spans and sanitized diagnostics |
| `ast/` | Native, stage, task, model, prediction, forecast, security and password-policy nodes |
| `analysis/` | Logical effects and pure validation of supported Nova clauses |
| `binding/` | Injectable catalog protocol and request-local lazy caches |
| `capabilities/` | Conservative engine profiles and optional detection by target |
| `planning/` | Logical intent, exact-type planner registry and execution plan types |
| `rules/` | Explicit registration order; stage lowering descriptors |
| `execution/` | Engine execution, service adapters, stage resolution and composite sequencing |
| `preparation.py` | Guarded preparation for ML training and streaming inputs |
| `security.py` | Grammar-context decoding of managed Ranger operations |

### Source and parsing

Original editor source and normalized source are separate fields. Token offsets
and spans refer to normalized source. Starts are inclusive, ends exclusive;
lines are one-based and columns zero-based. SQL fragments come from source
slices. `getText()` is used only for metadata such as identifiers, never to
reconstruct executable SQL.

Both lexer and parser default error listeners are removed. Errors report a
position and a generic message; rejected tokens and literals are omitted.
Direct execution accepts exactly one complete statement. Script execution keeps
the existing splitter and checks security, syntax, semantic effects, safe action
validation and rule effect bounds for the entire script before the first execution.
Confirmation refusal runs no statements. Confirmed scripts plan and execute each
statement sequentially so CREATE followed by INSERT can observe the new table.
The hard guard still checks the complete input of direct execution before parsing.

The MySQL proxy classifies all stage spans in one tree while substituting
session variables. `parsing_scope()` caches successful trees for that request.
An unchanged statement reuses its tree in query execution. A substituted or
normalized statement gets a new tree for its changed source. The cache is
discarded at request exit and has no process-wide source retention.

AST builders and planners reject duplicate registration. Native context fallback
is restricted to engine statement contexts. Unregistered Nova contexts and
statement types fail closed. Classification checks task creation before native
task submission, and prediction/forecast forms before ordinary queries.
`EXPLAIN` remains an engine request.

### Effects and plans

`PlanEffects` includes `reads_data`, `writes_data`, `deletes_rows`,
`changes_schema`, `changes_security`, `external_io`, `writes_metadata`,
`updates_rows`, `replaces_data` and `drops_objects`. Destructive confirmation
is a separate `requires_confirmation` property. An INSERT can write data without
acquiring UPDATE/DELETE confirmation behavior. INSERT OVERWRITE replaces data
without requiring additional confirmation. CREATE TASK writes Nova metadata;
its deferred body does not contribute execution effects.

`StatementSemanticsRegistry` owns exact-type analyzer, validator, effects resolver
and confirmation-policy registrations. Native effects dispatch on grammar context
and structural children. Comments and literals never supply statement effects.
Planners return complete plans even when confirmation has not been granted.
Lifecycle and executor checks raise `ConfirmationRequiredError` with typed effects.
The HTTP response keeps legacy fields and adds `error_code`, `statement_kind`,
`effects` and optional `execution_failure`. `destructive` describes statement
policy; `needs_confirmation` describes the current wait for approval. The worksheet
uses the server response and resubmits the immutable SQL/tab/namespace snapshot.
The proxy returns its existing ERR refusal without a new confirmation syntax.

`LogicalPlan` carries the statement and analysis. Lowering produces one of:

- `EngineSqlPlan`: a safe SQL template, effects and unresolved stage descriptors.
- `NovaActionPlan`: a typed operation payload, effects and a private source key.
- `CompositePlan`: ordered steps whose effects and confirmation requirements
  are the union of their children.

Planning can read explicitly requested catalog metadata and populate request-local
validation caches. It does not persist metadata, fetch storage secrets, submit
engine mutations or call action services. The executor enforces confirmation
again after planning. Rules run once in registration order and may preserve or
expand effects and confirmation requirements; they cannot remove either. A rule
adding effects declares `effect_bound` and, if needed, `requires_confirmation_bound`.
Preflight unions the bounds; execution rejects an undeclared effect increase.

Every `CompositePlan` requires an explicit `Atomicity`. `BEST_EFFORT` stops on
the first error result or exception and reports completed indices, failed index,
outcome and possible partial effects. It never claims rollback.

`SINGLE_ENGINE_TRANSACTION` admits proven internal-engine DML in one database.
It rejects DDL, Nova actions, INSERT OVERWRITE, unknown dependencies, reads of
previously modified tables and unsupported partial-column writes. UPDATE/DELETE
require primary-key tables and a known shared-data release supporting them;
repeated INSERT also requires shared-data support. Version overrides cannot
remove these structural restrictions. Metadata is requested only for this validation.

The runner opens a dedicated user connection, selects role/database before BEGIN,
executes all steps on that connection and commits after the last success.
Statement failure or cancellation rolls back on the same connection. A lost COMMIT
response or failed rollback produces `unknown` and discards the connection.
Statements inside a transaction are audited as EXECUTED, with a separate committed,
rolled_back or unknown transaction event. Proxy sessions are never borrowed for a
composite transaction; missing credentials for a dedicated connection cause refusal.
Cross-system coordination and compensation are outside this contract.

### Credentials and stage execution

Plans contain no passwords, resolved storage keys or authenticated connections.
Typed task/model/prediction/security payloads contain their validated definitions.
Security payloads include the decoded operation and identifiers. Source
fragments remain in private request execution state for audit and execution material.
Only plans are suitable for serialization; execution contexts are internal.
Debug representations omit source, encrypted passwords, connections and private
validation caches. Engine plan construction rejects credential-bearing SQL.

Native sensitive tokens also use unique slots, with source spans pointing into
private runtime state. Their material is restored only after stage authorization
and lowering, so length-changing rewrites and mixed native FILES/stage queries
retain both the rewrite and execution material without serializing secrets.
Missing or duplicated private slots fail closed. After parsing, stage references
become unique slot identifiers in the SQL template with path, scope and access
direction descriptors. Rewrite rules
operate on the template; runtime locates each slot exactly once and rejects missing
or duplicate slots. It never rescans the AST or restores the original stage SQL.
`StageRuntime` resolves scoped
references and checks access for every selected stage before resolving any
storage secret. Credentials and CSV settings remain keyed by reference offset,
so repeated stage names, joins and different scoped connections stay independent.

The existing translator and injector handle FILES(), LIST, COPY and stage
exports. Secret resolution occurs at execution, immediately before preparation
and engine submission. Responses and audit SQL use the redacted form.
Native FILES() behavior is preserved.

Task creation and password-policy actions never become raw engine statements.
Task bodies containing stage references or explicit populated credentials are
rejected before metadata persistence. Task timezone lookup, owner role,
dependency checks and audit actions remain in the existing services/adapters.

### Binding and capabilities

`CatalogProvider` exposes `resolve_table`, `get_columns` and `get_details`.
`Binder` caches each operation separately for one request. Asking for a table
does not fetch columns or details. Missing or inaccessible tables and missing
columns produce semantic errors. Metadata queries use the caller's identity,
active role, authenticated connection and database/catalog context.

Columns use documented `COLUMN_TYPE`, `ORDINAL_POSITION`, `IS_NULLABLE`,
`COLUMN_DEFAULT` and `GENERATION_EXPRESSION` fields. Results retain ordinal
order. Details use the source of `SHOW CREATE TABLE` to retain key type, primary
keys and the full partition clause where available. The binder preserves the
resolved table/view type. See [StarRocks columns metadata](https://docs.starrocks.io/docs/sql-reference/information_schema/columns/).

`RelationBinder.bind_relation()` derives ordered output columns from table/view
metadata, aliases, computed expressions with aliases, wildcard, qualified wildcard,
subquery and CTE (including column alias lists). Duplicate names are retained.
Complex expressions have `UnknownType`; undetermined output forms such as recursive
CTE, set operations or USING join wildcard shape are refused conservatively.
Stage schemas use an authorized DESC FILES execution helper; credentials remain
inside that helper and only typed columns reach the planner.

`SqlType` preserves raw engine type and normalized parameters for numeric/string
types, ARRAY, MAP and STRUCT. Unknown future types remain unknown. Query assignment
permits explicit compatibility categories; safe schema widening is a separate
conservative comparison. Name mapping uses case-insensitive column identifiers,
rejects missing/extra/duplicate names and follows target order rather than source
ordinal. `SchemaDelta` describes additive candidates and unsafe reasons; it never
executes ALTER. Generated/key/partition/auto-increment/hidden constraints and missing
metadata prevent a safe mutation decision. No synthetic schema version is invented.

Production resolves `EngineIdentity` using CURRENT_VERSION(), splitting release,
prerelease and build (including StarRocks's hexadecimal build suffix). VERSION()
continues to be a user compatibility function. Deployment mode comes from explicit
configuration or the FE `run_mode` Value column using a system connection.
Unknown mode never enables shared-data transaction features.

Capabilities resolve in order: conservative defaults, detected release profile,
operator overrides. Unknown override names and non-boolean values are rejected.
MERGE remains disabled in upstream 4.1.4. Cache keys contain host/port, deployment
configuration identity, deployment mode and overrides, never credentials. A
single-flight lock per target/configuration prevents concurrent duplicate probes;
successful identity lasts 15 minutes and
failure retries after 30 seconds. The combined probe budget is two seconds.
Startup warms the cache. `reload_nova_app_config()` clears config/capability caches;
`capability_provider.invalidate(target)` supports an engine switch. Detection
failure returns conservative capabilities so ordinary SQL can proceed.
Native queries, including SELECT 1, perform zero binding and no capability network
calls once the cache is warm.

## Integration Points

The HTTP query routes and MySQL proxy preserve response shapes, identity, active
role, tenant context, row limits and audit behavior. EXPLAIN uses the same
frontend, stage preparation and engine adapter. ML SQL preparation uses the
shared frontend without routing through QueryService. Assistant SQL validation
uses parsing and pure validation without a catalog or engine connection.

The worksheet opens confirmation only from server `needs_confirmation` and
resubmits the captured SQL, tab and namespace. Cancel submits nothing. On narrow
viewports a pending confirmation closes the assistant sheet so it cannot cover
the dialog or trap its keyboard controls; the desktop assistant stays open.

Ranger-enabled planning decodes role creation/deletion, membership, hierarchy,
supported table grants, role listings and access listings from grammar contexts.
Unsupported managed governance forms are rejected; they are never forwarded as
native grants. Native user-account operations retain their prior routing.
The ACCOUNTADMIN/root/UDF/egress guard runs before parsing and Ranger actions.
Ranger-disabled security SQL keeps native execution.

Compatibility APIs remain in `modules/query/dialect/parser.py`, the compact ML
and task parsers, and the old security statement router for existing callers.
They are not production feature detectors. The stage parser's legacy short
circuits do not apply to central SQL parsing. The semantic-expression parser
retains its separate expression entrypoint and compatibility listener.

## Configuration

The frontend requires no database migration or new runtime dependency. It uses
the committed Python parser generated by ANTLR 4.13.2 from the pinned StarRocks
4.1.4 grammar. Java is used only for regeneration.

QueryService accepts injected AST builders, planner, catalog-provider factory and
capability resolver. Production defaults select the registries and caller-authorized
StarRocks catalog. The executor's payload-type handler registry accepts extensions
without changes to adapter routing. Generic debug logging reports planner/rule names,
effects, binding request counts, capability source and atomicity; it omits SQL and
credentials and introduces no unbounded metric labels.

```yaml
sql_frontend:
  engine:
    deployment_mode: shared_data # optional; omit to introspect
    feature_overrides:
      native_merge: false
```

## Developer Extension Workflow

1. Add syntax inside a marked Nova grammar region, then run
   `bash backend/scripts/generate_grammar.sh`. Add parser corpus cases for
   comments, literals, spans and rejected forms.
2. Add a typed Statement subclass and register an AST builder for its context.
   Slice SQL fragments from normalized source. Do not add a feature detector
   to QueryService.
3. Register `StatementSemantics` with an analyzer, validator, effects resolver and
   confirmation policy. Mark a validator preflight-safe only when it needs no
   table binding or execution; request-local validation avoids decoding it twice.
4. Implement a planner and register its exact statement type. Request only the
   catalog metadata needed through `context.binder`; choose lowering from
   `context.capabilities`. Keep action execution and secrets out of planning.
5. Return safe engine templates, a typed action payload or an explicit composite. Declare
   all effects and any separate confirmation requirement. Register an action
   handler only when an existing service must execute the operation.
6. Add a rule only if lowering requires one; register it in explicit order and
   declare any effect bounds. Preserve stage slots and do not reuse transaction
   intent after changing SQL unless the new dependencies are proven.
7. Test native preservation, planner purity, binding caches, capabilities,
   composite ordering/failure, redaction and the real consumer path.

`test_sql_frontend_planning.py` demonstrates capability-dependent composite
lowering with a dummy statement and explicit binding. The consumer test
`test_extension_runs_through_unmodified_query_service_with_injected_catalog`
runs that extension through QueryService without adding a branch to its SQL flow.
`test_sql_frontend_readiness.py` registers a test mutation's analyzer, validator,
effects, planner, relation binding, rule and payload handler, proves confirmation
for a SELECT-prefixed mutation, and executes through the production adapters.

MERGE, ALL BY NAME and schema evolution have no syntax or implementation here.
Their future planners can use these registration, binding, capability and
composite interfaces. Physical optimization remains StarRocks's responsibility.

Query Autopilot uses the central frontend for canonical fingerprints, typed snapshot mappings, and governed maintenance. See [Query Autopilot architecture](arch-14-query-autopilot.md).
