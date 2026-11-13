# Production refactor acceptance

The owner approved the architecture plan on 2026-11-11 and then reduced the
load acceptance requirement to tests that fit the current development device.
This document records that scope. It does not certify an implementation.
The [supplied specification and inventory](source/README.md) are archived with
their original checksums; the accepted changes below override their conflicting
topology and load requirements.

## Deployment contract

- One production Compose file, with environment values inline and shared YAML
  anchors grouped by service privilege. No `.env`, environment-file dependency,
  or Compose override chain in the production entrypoint.
- Git contains a credential-free template. The actual deployment Compose stays
  outside Git, with restricted file permissions and stable, deployment-specific
  secrets. Never emit its expanded configuration to logs or CI artifacts.
- PostgreSQL 17 owns Nova control metadata after verified cutover. StarRocks
  retains user authentication, governed business data, and engine-transactional
  Stream journals. Ranger, Superset and Nova retain separate databases.
- Separate critical and cache Redis; PG owns durable jobs, receipts and outbox.
- One backend image with separate workload processes. All required roles start
  through the normal deployment path, without feature-enable switches.
- OpenSandbox uses the existing Docker runtime. No KVM, Kata, extra VM,
  Kubernetes or separate sandbox host is required by this delivery.

## Approved differences from the enterprise specification

Single-host recovery replaces multi-host HA acceptance. Standby promotion,
quorum availability and RPO=0 on host loss are not claimed. The OpenSandbox
controller remains on the application host; the separate security-host
requirement is not met. Application/worker/kernel containers must not receive
the Docker socket; only the sandbox controller may hold its required access.
Boundary, egress, identity and resource-limit tests remain mandatory. Passing
them is not a claim that hostile code cannot exploit an unknown kernel/runtime
vulnerability.

The owner's later instruction removes the medium PoC load and 24-hour
production-host soak from the completion gate. Record the device, actual load,
duration, throughput and failures of local tests. Production resource settings
must be adjustable in the one Compose file. Do not report the earlier target
(50 clients, 25 queries, 20 notebooks, 10 agent runs) as verified unless measured.

Backups must include an off-host copy and a tested restore. A backup stored
beside the database does not establish recovery from host loss.

## Feature and AI acceptance

Inventory UI, HTTP, SQL/proxy, background jobs and user docs. Every feature must
have successful, invalid-input, authorization and dependency-failure scenarios
where applicable. Explain non-applicable scenarios rather than silently skipping
them. A documented unsupported capability is tested as an explicit refusal;
this refactor does not claim full StarRocks CDC parity.

Use actual providers for chat/tool calling, semantic/hybrid search,
AI SQL, Studio and Dataviz assistant. Run each canonical AI flow at least three
times, retain all attempts, and compare schemas, grounded answers and effects
against known fixtures. Unit mocks establish fault handling, not live-provider
compatibility. Missing providers, MCP endpoints or required connectors block the
corresponding feature evidence. Do not upload private data for testing.

On 2026-11-11 the owner explicitly waived embedding tests ("Abaikan test
embedding"). Record embedding-provider tests as OWNER_WAIVED, never PASS. Do not
install or configure an embedding provider for this QA. Semantic/hybrid paths
that require an embedding provider must report the missing live prerequisite;
the waiver does not establish their live retrieval quality. Other AI capability
and security tests remain in scope.

Completion requires applicable CI, local functional/security/fault checks,
migration rehearsals, verified cutover and current docs, with no known unresolved
defect in the release scope. Zero unknown bugs is not a verifiable guarantee.

## Evidence and safe operation

Each result identifies code SHA, schema/image versions, environment, test IDs,
command, result and evidence. Keep source inventory, executed tests and pending
work separate. An unrun or skipped mandatory case is not a pass.

Reuse the existing development stack. Never seed/reset its data or stop shared
dependencies for chaos tests. Local fixtures use disposable resources within
compatible services; destructive stack tests run only on a disposable CI stack.
Cutover must freeze every writer, validate the final import and revoke stale
writers. After PG accepts writes, recovery uses PG and external receipts rather
than stale StarRocks metadata.
