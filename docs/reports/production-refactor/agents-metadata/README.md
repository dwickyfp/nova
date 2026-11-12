# Agent metadata storage checkpoint

Source parent: `c6a45bac`. Revision 0021 and `AgentMetadataRepository`
prepare the 30 agent-owned `NOT_STARTED` tables in PostgreSQL. API, agent and
automation code is not cut over. See
[the contract](../../../architecture/production-refactor/PG_AGENTS.md).

## Results (local device, existing PG17 TLS fixture, no other stack)

Revisions 0017–0020 belong to parallel sessions. For local runs only, this
worktree used temporary placeholders (`PERFORM 1;`, since each revision runs
inside a PL/pgSQL `DO` block) so the migration chain was contiguous; they were
deleted before the commit. Every real-PG run took `/tmp/nova-metadata-pg.lock`
in the same shell command.

- Real PG, agent module: 32 passed, 0 skipped: concurrent agent edits from one
  revision (one success, one conflict) with the loser's snapshot rolled back;
  raw definition change without a new configuration revision refused;
  immutable snapshots/manifests (same ID, other content conflicts; manifest
  needs its version; no update/delete grant); shared agents visible only to the
  active granted role, exposing only the skills and custom tools it names; role
  grants revisioned, ACCOUNTADMIN refused; long skill bodies only as complete
  part sets; idempotent tool registration removed with its MCP server and
  unaudited status bookkeeping; credential-shaped text refused without echo and
  an MCP endpoint with userinfo refused by the database (canary absent from
  errors, logs and rows); event batch plus high-water atomicity, identical
  resend accepted, conflicting sequence reuse refused (also under
  concurrency), events above the mark and marks without events refused at
  commit, frame/sequence mismatch refused; stale producer fenced after a
  takeover (append, heartbeat, finish, direct insert, newer security version);
  terminal runs frozen; interruption and role-change markers closing replay;
  cross-person run isolation; Missions with revision CAS, login/version binding
  and explicit resume; idempotent deliverable receipts; one winner among three
  automation claims, stale generation unable to record, worker unable to change
  definitions, retired Smart/Auto refused; memory projection only with its
  revision; quality revision stores refusing stale revisions, concurrent forks
  and foreign-scope IDs; active verified-query plan unique under four concurrent
  proposals and decided once; rule proposals, research runs, artifacts and
  shares; per-workload grants for every role in `WORKLOAD_ROLES`; unbound actor
  refused; maintenance refusal; import refused outside maintenance, and a
  maintenance import that keeps a completed run with no events, a run whose
  events stop early (replay reports `complete=false`), an imported quality
  fork (readers refuse it; the next runtime revision lands above it), a legacy
  dashboard and a legacy agent with an explicit empty View binding.
- Mutation check: removing the deferred high-water constraint trigger made
  `test_event_batches_and_their_high_water_commit_together` fail ("DID NOT
  RAISE MetadataIntegrityViolation"); restored.
- First attempt failed before any case: the placeholders were plain `SELECT 1;`,
  which PL/pgSQL rejects inside the migrator's `DO` block. Also retained: an
  earlier queued run was stopped because the worktree had no virtual
  environment yet (no PG work executed).
- Full CI real-PG list (20 modules): see "Final real-PG run" below.
- Unit/coverage 10,946 passed (0 skipped); trajectory suite 358; scorecard
  54/54 scenarios, 189/189 checks; `mypy app` clean (703 files); Ruff clean on
  the changed Python files.

## Final real-PG run

All 20 modules in the CI real-PG list, run once inside the lock shell
command (lock taken 18:16:32 WIB): **674 passed, 0 skipped, 0 failed**
(890.77 s). Revisions 0017 (Streams) and 0018 (ML engine) were the real files
from local `feat/production-control-plane` `0555b1ad`; 0019 and 0020 were
temporary placeholders. None of those four files is in this commit. Before the
run, `pgrep -f "tests/integration/test_metadata_"` matched nothing; afterwards
it matched only two other sessions' shells still waiting in the lock loop
(their command lines contain the module name; no other pytest ran).

## Not done here

Durable consent, the import converter (timestamp provenance, JSON text,
fork/duplicate manifests), caller ports and cutover remain open; findings are
listed in the contract. Delivered-commit CI is not checked; nothing is pushed.
