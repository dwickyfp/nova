# Credential-safe metadata logging

The local credential scan found the synthetic invalid-integer canary in a
PostgreSQL ERROR message. Nova's API exception was already sanitized. Earlier
provider-only scans did not include this marker; those results do not establish
that the complete database log was free of credential-shaped values. The failed
scan is retained in the AI configuration evidence.

PostgreSQL's parameter-length settings control parameter detail, not values
embedded in a primary error message. Its logging thresholds are described in
the [PostgreSQL 17 logging reference](https://www.postgresql.org/docs/17/runtime-config-logging.html).

The identity provisioner now assigns all 15 metadata logins a fixed policy:
server messages start at FATAL, error verbosity is terse, and statement,
parameter, duration, sampling and debug-query logging are suppressed. These
are role defaults, so database background processes retain the operator's
server policy. The privileged provisioning transaction uses the same policy
before processing password verifiers.

Ordinary metadata SQL errors are reported by Nova using a fixed category and
validated SQLSTATE. No SQL text, bind value, driver diagnostic or exception
traceback enters that log record. This loses PostgreSQL's detailed statement
diagnostics for these accounts; monitoring must use the sanitized application
event and workload/request telemetry. It does not replace governed audit events.

Bootstrap verifies the exact role defaults and refuses unexpected parameter SET
grants, including inherited grants. Revision 0009 expands admission to every
privileged or unknown parameter SET grant and every ALTER SYSTEM grant, protecting
audit/constraint triggers as well as logging. It does not silently repair a pre-existing
deployment. Readiness checks the effective settings and privileges of the
connected backend, including origin replication mode so its normal triggers run.
Revoking SET privileges does not reset a backend's already changed mode.
Every unit of work repeats that check inside its transaction,
before sending actor or domain values; a transaction pooler may select a
different backend after readiness. Policy drift refuses admission.

The local suite tests all logins, denied attempts to enable unsafe logging,
provisioning drift, readiness drift, runtime privilege drift and diagnostic
redaction. CI also scans its PostgreSQL server logs for synthetic credential
markers and encrypted envelopes, fails on an empty capture or any match, and
publishes only counts. Raw server logs are not uploaded. The scanner has
regressions for values embedded in a primary error message.

This policy covers prepared PostgreSQL metadata accounts. Production Compose
wiring, other dependency logs, provider responses, queue/artifact scans and the
full release canary matrix remain separate acceptance work. Existing local
logs containing the synthetic marker have not been purged.
