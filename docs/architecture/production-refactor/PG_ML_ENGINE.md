# ML engine metadata port

Revision 0018 prepares `CONFIG_ML_JOBS`, `ML_MODELS`, `ML_MODEL_VERSIONS`,
`ML_MODEL_ALIASES`, `ML_EPHEMERAL_RUNS` and `ML_RUNS` in `nova_meta`, with
`MLMetadataRepository` as the prepared replacement for the `NOVA_SYSTEM` paths
of `MLJobRepository`, `ModelRegistryRepository`, `EphemeralRepository` and the
registry SQL in `MLEngineService`. Running API, SQL, assistant and worker code
still uses `NOVA_SYSTEM`; nothing imports the repository.

Only metadata moves. Model artifacts, prediction results and upload leases stay
in the artifact store; rows name them by URI and SHA-256. The three source
`_PK` tables (`ML_MODELS_PK`, `ML_MODEL_VERSIONS_PK`, `ML_MODEL_ALIASES_PK`)
are intermediate copies made by `migrate_ml_metadata`. They have no PG table: if
one exists at final export, its rows are reconciled into the canonical table
with the `_LEGACY` copies before import (Wave K), never dropped or overwritten.

Revision 0017 is prepared in parallel (Streams). Migrations must be contiguous,
so this branch merges only after 0017 is in place.

## Access

ML metadata is control-plane state reached through the owning service, per
workload and through a bound actor context. Training writes run as the person
who asked for the training; the ML worker validates that person's live session
before it trains. Retention runs as the maintenance service principal.

| Table | Read | Write |
| --- | --- | --- |
| Training jobs | web, query (without the encrypted SQL); ml | web, query queue, cancel or withdraw an unclaimed job; ml claims, heartbeats, finishes, interrupts and prunes |
| Models | web, query, proxy, agent, automation, ml, maintenance | the owner's training through web, query, agent, automation, ml; delete: owner through web |
| Versions | same as models | reserve and publish: the owner's training as above; abandon: also maintenance; delete: model owner through web |
| Aliases | web, query, proxy, agent, automation | owner through web |
| Ephemeral descriptors | web, query, proxy, agent, automation, ml, maintenance | web, query, proxy, agent, automation, ml; maintenance deletes expired rows only |
| Run telemetry | the person it belongs to | web, query, proxy, agent, automation, ml for their own runs |

The proxy never trains: CREATE ML_MODEL requires an API session. Scheduler,
task, ingestion, notebook and outbox have no ML grant. No workload can delete
run telemetry or a job that was interrupted.

## Rules in the new store

- **Jobs are created once and fenced.** A job is created by id for the bound
  person, role and security version; a repeat returns the existing job, and
  another person's repeat conflicts. A claim moves `queued` to `running` and
  takes the next `lease_generation`; heartbeats and outcomes need that
  generation, so a worker that lost its claim cannot heartbeat or finish. The
  waiting API process may only request cancellation or withdraw a job no worker
  claimed. Finished jobs are frozen. The submitting processes cannot read the
  encrypted training SQL back.
- **Reservations need no Redis lock.** Reserving a version locks the owner's
  model row, changes its definition only at the revision it read, and inserts the
  next `TRAINING` version with a reservation token. Concurrent reservations of a
  scope get one model and distinct versions. Runtime models are unique per
  tenant, owner, database, schema and name, comparing a missing database or
  schema as the empty string, as the source does. Imported duplicates are kept;
  the newest one continues its version line.
- **Published versions are provenance.** A version moves once, from `TRAINING`
  to `READY` (with its artifact URI, a lowercase SHA-256 and a non-negative size,
  under its reservation token) or to `FAILED` (with a reason). `READY` and
  `FAILED` rows never change again. The current version only moves forward and
  only to a published version. The retention sweep can abandon a reservation but
  cannot publish.
- **Aliases** name a published version of their owner's model and move only from
  the revision the caller read.
- **Ownership is bound.** A model, version, alias or run record is written for
  the bound person; a model, its versions and the aliases pointing at it are
  deleted only by the model's owner, aliases and versions first.
- **Ephemeral descriptors** keep their scope: a run id that exists in another
  scope conflicts instead of being replaced.
- **Run telemetry** is created once and moves once from `RUNNING` to its
  outcome, as Decision Lab writes it.

Queuing, cancelling and withdrawing jobs, model definition changes and
deletion, version reservations and outcomes, and alias changes write a success
audit row in the same transaction. Claims, heartbeats, ephemeral descriptors
and run telemetry are execution state, as in the source, and are not audited.
Imported rows keep their values and timestamps and are marked `imported`; the
migrator writes only under maintenance.

## Findings

- **F1, execution isolation (R10).** `ML_TRAINING_EXECUTOR` defaults to
  `local`: training, artifact deserialization and registry writes run inside the
  web, query, agent and automation processes. Only the split Compose file sets
  `worker`, and even then agent tools and the feature store train in-process
  because they pass no session. The grants above follow these real callers;
  once training moves to the ML worker, registry writes should narrow to `ml`.
- **F2, legacy inline models.** `ML_MODEL_VERSIONS.model_binary` holds legacy
  model payloads with no digest, and `resolve_alias` still loads them. They are
  imported unchanged and are never written at runtime; quarantine or re-sign
  them under R10 before cutover.
- **F3, source overwrites.** StarRocks primary-key inserts replace whole rows:
  every reservation resets the model's `created_at` and `created_by`, and
  publishing resets the version's `created_at` and drops its reservation token.
  Imported timestamps therefore reflect the last reservation or publish, not
  creation. The new store keeps identity columns immutable.
- **F4, interrupted training.** Source retention deletes interrupted jobs after
  a day, although an interrupted job can leave a `TRAINING` reservation and an
  uploaded artifact. The new store keeps interrupted jobs until a reconciliation
  step, which is not built yet.
- **F5, run telemetry retention.** `ML_RUNS.expires_at` is written but nothing
  reads or prunes the table; it grows without bound. No retention is granted here.
- **F6, model deletion.** `delete_model` removes artifacts and then metadata in
  separate calls without a persisted intent. The caller port needs an intent
  (or outbox) record so a failed metadata delete cannot leave aliases naming
  deleted artifacts.
- **F7, legacy aliases.** Aliases migrated with owner `__legacy__` can be
  resolved but not moved or deleted by anyone except through deleting the model.
- **F8, job identity.** As Wave E notes, the session link that lets the worker
  train lives only in Redis; the job row alone cannot recover it.

## Remaining ML work

- Port the job submitter, worker, registry, ephemeral cache, cleanup sweep,
  Decision Lab and router calls at cutover, with the worker holding the lease
  generation it claimed and the training path holding its reservation token.
- Interrupted-job reconciliation, model-deletion intent, run retention, and
  the import of `_PK`/`_LEGACY` copies, legacy timestamps and inline payloads.

Validation is recorded in
[the ML metadata checkpoint](../../reports/production-refactor/ml-metadata/README.md).
