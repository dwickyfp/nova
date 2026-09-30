# StarRocks Patch Agent Guide

Inherit the [root contracts](../../AGENTS.md). Read the
[patch baseline](README.md), [Docker guide](../../docker/AGENTS.md), and
[Ranger security guide](../../backend/app/modules/access_control/AGENTS.md).
Paths below are repository-relative; commands run from the repository root.

## Engine boundary

- The baseline is the pinned StarRocks 4.1.4 source/runtime. Exact upstream
  commit, patch set, build inputs, and image assumptions live in this directory
  and `docker/ranger/starrocks-fe.Dockerfile`, not a copied dependency inventory.
- Preserve active-role propagation for access, row-filter, masking, RPC, and
  task execution paths. Nova's opted-in governed mode rejects zero/multiple
  active roles. Root is an upstream bootstrap exception, not a public user-data
  identity. Do not weaken these checks to make a new FE path work.
- Retain upstream-compatible behavior outside Nova's explicit mode and existing
  task/caller restrictions. Scheduled role snapshots must not resurrect revoked
  assignments or activate every assigned role.
- Java is permitted for upstream engine patches and their compiled FE classes,
  and for build-time parser generation. Nova's backend/request orchestration
  remains Python; do not introduce a Java application service or another engine.
- Keep source/runtime pins, patch order, required runtime libraries, checksums,
  and relevant regressions consistent. Newer upstream docs or another database
  are not evidence that the pinned patched runtime supports a capability.

## Validation

Verify patch application against the clean pinned upstream checkout:

```bash
./patches/starrocks/verify.sh
```

This proves application compatibility only. Compile the changed engine classes
through the existing governed image build, then exercise affected role/policy
paths using the access-control runbook and fixtures:

```bash
docker compose -f docker/docker-compose-engine.yml build starrocks-fe
```

A stock-engine integration pass does not prove patched authorization. Verify
missing/multiple/inactive/revoked roles, row filtering, masking, and task role
inheritance as affected. Run applicable backend/integration gates for changed
consumers and inspect existing acceptance tests/reports for the relevant path.
If changing engine version, also verify grammar pins, capability profiles, FE/BE
compatibility, initialization, and version-specific SQL regressions.

Never claim production rollout, patch compilation, or live acceptance from a
clean `git apply --check` result alone. Documentation-only patch guidance follows
the root documentation-validation route.
