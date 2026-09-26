# Studio Agent: Configuration History and Resource Bindings

> Agent owners can compare and publish saved configurations, and constrain search and feature tools to explicit resources.

## Concept/Overview

This change implements configuration history and explicit resource bindings for Nova Studio. It does not change JEV decisions, Nove Assistant discovery, or the shared assistant loop.

An active configuration continues serving requests while its owner edits and saves a draft. Saving a draft does not activate it. Publication checks the active revision and current resource availability before replacing the active configuration. Restoring an older configuration creates a new revision and preserves prior snapshots.

History captures agent settings and resource references. It does not freeze shared skill bodies, custom tool definitions, Semantic View contents, search index contents, Feature Group contents, or global Decision settings. These dependencies continue using their current definitions. Configuration history therefore does not guarantee reproduction of an earlier model response.

## Operations

The owner-only API exposes:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/agents/{id}/versions?offset=0` | Paginated snapshot summaries and active revision |
| `GET /api/v1/agents/{id}/versions/{version_id}` | One saved configuration |
| `POST /api/v1/agents/{id}/versions` | Save a draft using the editor's expected active revision |
| `POST /api/v1/agents/{id}/versions/{version_id}/publish` | Validate and activate a saved configuration using an expected revision |

The existing update endpoint remains compatible and also records configuration history. Draft saving and publication are audited. Publication records both its source snapshot and resulting active revision.

Each Studio agent can bind Search indexes with mandatory scalar filters and Feature Groups. A tool request must satisfy both the configured binding and the caller's existing authorization. Search requests inherit fixed filters; conflicting values or types are rejected before query execution. Unbound indexes and Feature Groups are rejected. An empty binding list grants no access through these Studio tools.

Semantic View binding remains in the existing Tools configuration. The agent catalog reports only bound resources currently accessible to the caller. This does not enumerate arbitrary databases or read their data.

## Nova UI

The existing Workspace visual direction is retained: restrained styling, familiar controls, and no decorative animation (antislop E1/R1/M1).

1. Edit the agent's Configuration, including the new Resources tab.
2. Choose **Save draft**. The active agent remains unchanged.
3. The History comparison opens, showing changed fields against the active configuration.
4. Choose **Publish selected version** to activate it.
5. Use the top-right **History** dropdown to inspect or restore another saved configuration.

The comparison modal uses a version list and adjacent field differences on desktop. At narrow widths, the version list and differences stack. Header and action controls remain outside the scrolling comparison. Long values wrap. Loading, empty, failure, retry, and stale-revision states are explicit. Unsaved local edits disable publication; background refresh does not silently discard those edits. A revision conflict keeps the comparison open and offers reloading the active configuration.

## Implementation Notes

`agent_repository.ensure_schema()` adds `resource_bindings` and `config_revision` to the existing agent table and creates `NOVA_SYSTEM.CONFIG_AGENT_VERSIONS`. All persistent state remains in StarRocks. Restart the backend to run its existing schema initialization path.

Snapshots are saved before active configuration mutation. Failure to save history prevents publication. A conditional update compares the prior revision so concurrent publications cannot silently overwrite each other. Every successful publication receives a fresh revision, including restoration to an earlier configuration. A failed concurrent publication can leave a saved, inactive snapshot; only the active record's revision identifies the published configuration.

Operational revision metadata and default empty bindings are excluded from existing role-verification fingerprints. This preserves verification for unchanged legacy agents while a nonempty binding change requires verification of its new resource scope.

Publication revalidates Semantic Views, Search indexes, Feature Groups, tools, skills, and explicitly configured provider/model availability. This is configuration validation, not a live provider inference or a draft conversation evaluation.

## Validation

Executed locally on 2026-09-26:

| Check | Result |
| --- | --- |
| Agent, shared-agent RBAC, Studio, intelligence, Feature Store unit regressions and `tests/eval` | 633 passed |
| Focused versions/resources, resource trajectories, verified access, business catalog rerun after catalog availability adjustment | 50 passed |
| Existing harness scorecard, `python -m tests.eval.report` | 48 scenarios; 165/165 checks passed |
| Chromium: history, configuration, access, agent API, Studio chat | 46 passed across 5 files |
| Frontend production build | Passed |
| Ruff for changed backend modules and new tests | Passed |
| `git diff --check` | Passed |

Backend coverage includes immutable snapshots, owner isolation, draft/publish/restore, stale-editor rejection, failed persistence, compare-and-swap conflicts, credential rejection, dependency validation, typed fixed filters, unauthorized resources, and backward-compatible access verification. Three scripted shared-loop trajectories verify allowed search and rejection of a foreign index or conflicting mandatory filter, including consent and termination behavior.

Browser coverage includes pagination, comparison retry, resource-list retry, empty states, typed filters, unsaved-edit protection, and conflict recovery. The long comparison is tested at 320px and 1280px in light and dark themes. Tests assert bounded layout, visible actions, Escape dismissal, and text contrast of at least 4.5:1 for the tested paragraphs, headings, and comparison values. Mobile dark and desktop light screenshots were also visually inspected.

### Antislop delivery gate

| Area | Evidence / disposition |
| --- | --- |
| Visual hierarchy | Existing Nova header, shadcn controls, one publication action; no new decorative assets or gradients |
| Copy | Concrete Save draft, History, Publish selected version, retry and conflict messages; scope limitation visible in the modal |
| Accessibility | Labeled controls, existing accessible dialog/select primitives, keyboard dismissal, tested text contrast |
| Responsive layout | 320px/1280px light/dark browser checks; wrapped differences and internal scrolling |
| Interaction states | Loading, empty, error/retry, pending publication, stale revision and unsaved edits tested |
| Comments and scope | Comments explain constraints; no shared engine or JEV behavior changes |

## Limitations

The broad regression suites mock database and provider dependencies. Chromium tests exercise real UI components against mocked API responses. The additional live StarRocks save/publish/restore check below exercises real persistence and audit writes. A live schema migration and live model quality benchmark were not run for this change.

Existing Studio agents that use AI Search or Feature Lookup must now explicitly bind their permitted resources. No resources are automatically granted during migration. History begins when a configuration is first saved or updated after this feature; older edits cannot be reconstructed.

## Save error correction — 2026-09-26

The running backend reported HTTP 500 after saving a draft. Its traceback pointed to the audit insert, after both configuration snapshots had been stored. The live `AUDIT_LOG.decision` column is `VARCHAR(32)`, but Save draft wrote a 36-character UUID there and Publish wrote a longer JSON object. StarRocks rejected the insert. This matches its documented handling of [values that exceed their destination column size](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/INSERT/).

Version identifiers now go into the existing audit text payload (`sql_text`) as JSON. The security-decision field stays unset. No column expansion, truncation, or audit suppression is needed. Previously saved drafts remain available in History.

Two new regression cases invoke the real audit writer and enforce the actual decision-column limit. Both failed before the fix and passed after it. The focused versions, audit-redaction, repository, and Semantic View binding suite passed all 100 tests.

An opt-in integration test runs the actual FastAPI routes through ASGI with a synthetic test identity, real StarRocks repositories, and real audit inserts. It verifies Save draft returns 201 without changing the active agent, Publish and Restore return 200, all four snapshots are retained, and all three audit payloads preserve complete version IDs. It passed against the local engine. Only its temporary agent and version rows are cleaned up; its audit records remain. No existing agent configuration or user session is used.

Run this integration check against a locally configured StarRocks instance with:

```sh
NOVA_RUN_LIVE_AGENT_VERSIONS=1 .venv/bin/python -m pytest tests/integration/test_agent_versions_live.py -q
```

Antislop review for this correction: no UI changes, no new visual assets, and no redundant code comments. The fix is confined to the two version audit calls, their regression tests, and this report.
