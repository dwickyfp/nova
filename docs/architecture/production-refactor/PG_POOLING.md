# PostgreSQL transaction pooling

The metadata boundary is tested through PgBouncer in transaction mode, with one
physical backend serving concurrent application connections. This is foundation
acceptance on the existing disposable PG fixture. Production pooler services,
per-role capacity sizing and the complete Compose deployment remain pending.

The fixture uses [PgBouncer 1.26.1](https://github.com/pgbouncer/pgbouncer/releases/tag/pgbouncer_1_26_1),
with source SHA-256
`00886a2994b4844cd37110970df777772cfc42a74c400522c82fba509a3f42d9`.
`backend/tests/integration/build_metadata_pooler.py` downloads that exact release,
verifies its checksum and builds the binary. CI installs the build dependencies
and supplies the resulting binary to the PG acceptance lane. It does not select
an unpinned package or provision another PostgreSQL server.

## Tested connection contract

- Client connections use `sslmode=verify-full` with an explicit hostname.
  PgBouncer requires TLS from clients and verifies the backend certificate.
  The local fixture certificate is for `localhost`; `hostaddr=127.0.0.1` selects
  the local socket destination without replacing the verified TLS hostname.
- SCRAM authentication uses the fixture role's existing verifier on both sides.
  No forced-user setting collapses workload identity to a more privileged login.
  The temporary verifier file has mode 0600 and is removed during cleanup.
- The fixture uses transaction pooling, one physical server connection, at most
  12 client connections and no reserve pool. These are test limits, not measured
  production capacity. Production caps must account for every role and replica.
- Psycopg uses `prepare_threshold=None`; the fixture disables PgBouncer named
  prepared-statement tracking. Actor and transaction budgets are set inside each
  transaction. No session advisory locks, LISTEN or temporary tables are used.
- The test disables server-reset commands, so passing context-isolation checks
  depends on transaction-local state rather than a reset masking leaked state.

The [PgBouncer configuration contract](https://www.pgbouncer.org/config.html)
allows unknown startup options to be ignored. The fixture explicitly ignores
unsupported options and uses transaction-local settings for statement/lock
deadlines. It also ignores startup search_path; metadata SQL names its schema.
These are pooler transport settings, not feature-enable switches.

## Startup deadline defect and fix

The first functional run showed that actor isolation and transaction deadlines
worked, but a locked migration ledger could block application startup beyond
its budget: the direct-connection startup timeout option was ignored by the
pooler. `MetadataDatabase.open` now bounds the whole validation phase using the
configured acquisition budget, closes the failed pool and raises a redacted
`MetadataTimeout`. Initial connection acquisition retains its separate bounded
pool timeout. This does not silently retry a failed or uncertain write.

The regression prewarms transport, locks the ledger and verifies the validation
timeout, failed readiness and a fresh successful startup after releasing the
lock. Other cases verify two application pools sharing one backend, concurrent
principal/role/version isolation, missing-context denial, statement/lock expiry,
cancellation rollback, and refusal of plaintext, incorrect-hostname and
incorrect-password connections. Logs are scanned for fixture password/verifier
canaries. No development metadata writer or data service is restarted.

This evidence does not prove production grants for every domain, provider QA,
pool exhaustion under production load, backup/PITR or cross-host HA. Those gates
remain separate. No application domain has switched its metadata authority.
