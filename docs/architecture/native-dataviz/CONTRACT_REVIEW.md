# Native document contract review

These contracts are prepared in the Viz domain. The active Superset-backed
product does not yet use them. Model validation is not query execution or
renderer support.

## Persisted definitions

| Definition | Implemented invariants | Further integration |
| --- | --- | --- |
| Dataset | Typed table/view/SQL/semantic source, stable field IDs, schema fingerprint, typed parameters, metric and relationship references; object inspection/create API derives schema and identity server-side | Data tab, previews/repair, SQL output schemas, semantic authoring and sensitive-input engine protection |
| Chart | Nineteen visual identifiers with discriminated encodings; pinned dataset revision; measure aggregation and unit checks | Native compiler, query orchestration, semantic golden results and renderer registry |
| Presentation | Decimal axis bounds and reference annotations; bounded Top N; explicit table-calculation addressing, partitions and ordering; typed tooltip and overflow policy | Numerical execution and visual behavior for each setting |
| Dashboard | Stable pages and widget IDs; canonical non-overlapping 12-column layout; independent chart instances; explicit filter scopes and mappings | Shared GridStack canvas, editors and publication/sharing flows |
| Widget instance | Typed surface/padding/border and chart overrides; local immutable filter context bound to the exact chart dataset revision | Renderer composition and query-state combination with authorized viewer filters |
| Mutation | Operation UUID, expected revision, typed atomic changes; no client actor fields | Single service admission for HTTP and assistant tools, authenticated session revalidation |

Table calculations must reference an encoded measure. Addressing and partition
fields cover the chart's dimensions exactly; ordering lists each addressing
dimension once. Moving averages require an explicit bounded window. Annotations
and Top N cannot attach a different aggregation or axis merely because the field
ID exists in the dataset. Decimal bounds remain decimal strings, including values
beyond JavaScript's exact integer range.

Dashboard controls cannot reuse a field identifier as a parameter identifier.
Defaults are checked against the parameter's declared type, enum, multiplicity
and null policy. Sensitive parameters cannot persist defaults or allowed values.
Renderer callbacks, arbitrary HTML, CSS and unknown object keys are rejected.

## Generated browser boundary

`backend/scripts/generate_viz_contracts.py` produces JSON Schema and TypeScript
declarations. CI runs its `--check` mode. Zod validates wire shape in the browser;
cross-document bindings, relational constraints, permissions and custom semantic
validators remain authoritative on the server. Browser validation does not
authorize a save or replace a commit receipt.

The official GridStack React adapter has passed StrictMode lifecycle, saved
geometry restoration, independent host isolation and React text-safety tests.
It is not yet a finished native dashboard canvas. Both product surfaces must
mount the same editor; presentation density follows available panel width.
Existing Nova tokens and the handoff's component composition remain the design
authority. No second palette or navigation shell has been added.

## Dataset execution findings for the next package

* QueryService already retains caller, active role, login and security-context
  version, and reports engine column type descriptors. Its existing Viz caller
  still infers types from sample rows; the native schema must not use that probe
  as authoritative metadata.
* The central SQL frontend already owns parsed source spans, effects, catalog
  binding and relation binding. Its relation binder rejects some output forms,
  including set operations and recursive queries; native authoring must either
  extend that owner with engine evidence or return an explicit unsupported
  diagnostic. A second SQL parser is not an acceptable shortcut.
* QueryService now accepts immutable typed named values through a registered
  central AST/planner and the authenticated driver's binding argument. The live
  Alice/Bob checks retain Ranger row filters and masks and exclude inputs from
  Nova history. The dataset service and UI do not yet call this entrypoint.
  Engine-side audit/profile protection for sensitive values remains open.
* Catalog securable IDs are stable descriptive identities derived from names;
  they are not proof of immutable engine table identity across drop/recreate.
  The shared source provider now records engine-owned IDs and schema fingerprints
  and refuses replacement during metadata observation. Save/execution consumers
  must revalidate those observations without silently rebinding a saved dataset.
  The pinned [4.1.4 table schema](https://github.com/StarRocks/starrocks/blob/4.1.4/fe/fe-core/src/main/java/com/starrocks/catalog/system/information/TablesConfigSystemTable.java)
  declares `TABLE_ID` in `information_schema.tables_config`. The applied FE patch
  exposes the existing view ID after authorization. Table/view, empty-schema,
  rename, view replacement and denied/revoked-role behavior passed live checks.
  Unsupported external identities are refused; table/view evidence does not
  establish connector support.
* Personal workspaces use the existing private-namespace permission function
  through migration 0049. Named workspace foreign keys remain enforced.
  Repository tests verify isolation even when an explicit document grant exists;
  the prepared service resolves creation through `SpaceService`. Product route
  and UI integration remain pending.

The contracts remain open for the remaining specified semantics and UI
composition. The delivery record does not mark WP01 or WP02 complete.
