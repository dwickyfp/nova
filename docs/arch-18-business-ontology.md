# Supernova Business Ontology

The Supernova Business Ontology (SBO) is Nova's shared, governed business
meaning: which business concepts exist, what they mean, how they relate, and
which published Semantic View metric computes each one. It never runs SQL and
never grants data access. Semantic Views, StarRocks and Ranger stay the only
computation and authorization path.

Code: `backend/app/modules/ontology/`. API: `/api/v1/ontology`.
Plan and phase status: [proposal](proposals/supernova-business-ontology.md).

## Model

| Record | Meaning | Changed by |
|---|---|---|
| Domain | A named area of meaning (`Finance`, `Finance.Treasury`) with steward roles and optional visibility roles | `ACCOUNTADMIN` |
| Concept | One business concept in a home domain: kind, slug, label, definition, aliases, unit, grain, time semantics, exclusions, classification | Proposal |
| Relation | A typed link between two concepts (`IS_A`, `HAS_PART`, `DERIVES`, `MEASURES`, `APPLIES_TO`, `EQUIVALENT_TO`, `RELATED_TO`, `AFFECTS_GOAL`) | Proposal |
| Representation | A binding from a `METRIC`/`MEASURE` concept to one public metric of a published Semantic View version, pinned by the metric's claim fingerprint | Proposal, plus drift |
| Proposal | A set of changes, its reviews and its status | Submitter, stewards |
| Release | An immutable manifest of exact revisions | `ACCOUNTADMIN` |
| Alias `current` | The published release readers use | `ACCOUNTADMIN` (publish, rollback) |

Concept ids are opaque and stable. A rename is a new revision of the same id.
`qualified_name` is the domain path plus the slug, for example
`Finance.NetRevenue`. The same name in two domains is two concepts. Within one
domain, a slug, label or alias names at most one approved concept.

No relation type asserts causation. `RELATED_TO` and `AFFECTS_GOAL` mean
relevance. `IS_A`, `HAS_PART` and `DERIVES` must stay acyclic.

## Storage

The seven `NOVA_SYSTEM.CONFIG_ONTOLOGY_*` tables are append-only. Each change
inserts a revision row `(id, revision, operation_id)` with the actor, active
role, security-context version and a JSON payload. Writes take the shared
metadata lease, re-read the newest revision and compare the expected revision.
Saving content that is already current is a no-op. The runtime creates the
tables at startup. `docker/init-nova.sql` and
`backend/migrations/20261106_business_ontology.sql` hold the same DDL.

Nova is single-tenant per deployment, so records carry no tenant id.

## Access

- **Visibility.** A domain is visible when every domain on its path is active
  and either lists no visibility roles or includes the caller's active role (or
  a steward role). `ACCOUNTADMIN` sees all. A hidden concept is filtered out
  before search, counting and traversal. Asking for it by id returns the same
  404 as a missing concept.
- **Stewardship.** A steward is a caller whose single active role is one of
  the domain's `steward_roles`. Role assignment comes from StarRocks. Nova
  keeps no second role store.
- **Data.** Seeing a concept never grants data access. Bindings are listed only
  for Semantic Views the caller can already see.
- **Audit.** Every governed action, and every refusal of one, is written to
  `AUDIT_LOG` with `event_type=ONTOLOGY`.

## Workflow

1. **Propose.** Anyone who can see the affected domains submits changes. The
   operation id makes submission idempotent. A change can name a concept
   created earlier in the same proposal as `@<index>`. Submission validates the
   whole change set against the current state: domains, base revisions, naming,
   relation kinds, cycles, and bindings against the published metric.
   `POST /ontology/bootstrap` drafts one concept and one binding per public
   metric of a published Semantic View.
2. **Review.** Each affected domain needs an approval from one of its
   stewards. The submitter can never review their own proposal. Any rejection
   closes the proposal. A reviewer must be able to see every domain the
   proposal touches, so a cross-domain `EQUIVALENT_TO` is approved by both
   domains' stewards.
3. **Apply.** The final approval applies the proposal. Concepts, relations and
   bindings are written in that order, and each carries the proposal id. If
   application is interrupted, `POST /proposals/{id}/apply` resumes it without
   duplicating revisions.
4. **Release.** `POST /ontology/releases` snapshots active domains, approved
   concepts, active relations and current bindings into a manifest. The
   release is `BLOCKED` when the graph is inconsistent. A binding whose view
   moved is left out with a warning.
5. **Publish.** Publishing moves `current` by compare-and-swap from the
   release's base. It fails if another release was published in between, or if
   a pinned binding went stale. Rollback moves `current` to an earlier
   published release. No revision is ever deleted.

Readers use `scope=current`, the default. Stewards can read `scope=working`,
which is approved meaning that has not been released yet.

## Drift

Publishing a Semantic View version re-checks every validated binding to that
view:
- If the metric's claim fingerprint is unchanged (dataset, expression, unit,
  time dimension, filter, type), the binding is re-pinned to the new version.
- If the claim changed or the metric is gone, the binding becomes `STALE`.

A stale binding is never usable. Release preparation and publication re-check
freshness themselves, so the result does not depend on the drift hook having
run.

## Studio grounding

Every `semantic_query` resolves the business terms in its question before it
plans (`backend/app/modules/ontology/resolver.py` and `grounding.py`).

**Resolution.** A visible, approved `METRIC` or `MEASURE` concept is named when
one of these occurs in the question as whole words:
- its qualified name;
- its slug, also split at capitals (`NetRevenue` → "net revenue");
- its label;
- one of its aliases.

The longest name wins over a name inside it. Matching is lexical. Embedding
similarity never decides what an official concept is. A question in another
language matches through aliases, which stewards add.

**Effect on the query.**

| Situation | Result |
|---|---|
| No published release, or no metric concept named | Nothing changes |
| A name belongs to visible concepts in several domains, and the question names none of their domains | `CLARIFICATION_REQUIRED`: the turn ends as a question naming each choice; nothing is planned or run |
| An approved concept has no binding the user and agent can use | The turn stops with `NO_AUTHORIZED_REPRESENTATION` and names the concept. It is never answered with a metric of a similar name |
| The named metrics are computed in different views | `CLARIFICATION_REQUIRED`: ask for them one at a time |
| Grounded | The binding's view is used, the planner receives the concepts and their metrics, and a plan that omits a required metric is rejected |

A binding fits an agent's loaded view only when the metric's claim fingerprint
there equals the validated one. An agent pinned to an older view version
therefore still uses the approved definition, or refuses.

If the ontology cannot be read, the query fails closed with
`ONTOLOGY_UNAVAILABLE`. Missing ontology tables mean no release was ever
published, so nothing changes.

**Provenance.** A grounded result records `business_context` (release id,
manifest hash, concept ids and revisions, metric, binding) in its evidence. It
also records the `concept_grounding` Quality Lab fact, and a
`BusinessContextAnchor` on the Mission.

## Release pins

The ontology release used is chosen in this order:
1. **Released agents.** A new agent version records the current ontology
   release in its manifest (`dependencies.ontology_release`). Loading the
   manifest and authorizing a Mission release pin both require that release to
   still be published with the same manifest hash.
2. **Draft agents.** A turn uses one release for all its queries
   (`LoopContext.ontology_release_id`).
3. **Continued Missions.** A continued Mission keeps the release of its latest
   business anchor. Mission resume re-checks every anchored release.

## Tools

- `ontology_resolve` returns the approved concepts a text names, or the choices
  for an ambiguous term.
- `ontology_explain` returns one concept's definition, its bindings and its
  related concepts.

Both are read-only, need consent, and are bundled per agent. Bindings are
limited to the agent's Semantic Views and to views the caller can see.
`GET /ontology/resolve?q=` exposes resolution over HTTP.

## Console page

Console → AI & ML → **Business Ontology** (`/business-ontology`,
`frontend/src/features/ontology/`). The page has four tabs:

- **Concepts.** Shows the published release by default, or the latest
  approved definitions. Opening a concept shows:
  - its definition and facts;
  - the metrics that compute it, with whether each binding is in use;
  - related concepts and its version history.

  From there a reader can propose a new concept (optionally bound to a
  published metric), a revision, a link or a deprecation. A deprecation also
  retires the concept's links and revokes its bindings. **Draft from a
  Semantic View** runs the bootstrap.
- **Proposals.** Grouped as in review, approved, applied and closed. Each
  proposal shows its changes in words, the domains it touches, which domains
  still wait for a steward, and its reviews. A steward of an affected domain
  approves or rejects with a rationale. The submitter can withdraw but never
  review. An approved proposal whose application stopped can be finished.
- **Releases.** Shows the release Studio uses and the release history. For
  `ACCOUNTADMIN` it also offers preparing a release (issues and warnings
  included), publishing it, and switching back to an earlier one.
- **Domains.** Lists paths, steward and reader roles, and status.
  `ACCOUNTADMIN` creates and edits domains.

Query keys carry the security epoch, so a role switch never shows cached
meaning.

## Business Event Hub

Newsroom Stories and monitor NewsItems keep their own contracts and proofs.
Each one also becomes a `BusinessSignal`
(`backend/app/modules/intelligence/business_events.py`), and related signals
form a `BusinessIncident`. Both are stored in
`NOVA_SYSTEM.CONFIG_INTELLIGENCE_SIGNALS` and `_INCIDENTS`, using the same
shared revision tables as the ontology.

**What a signal stores.** Only routing metadata:
- the source type, id, revision and proof digest;
- the semantic metric;
- the bound concept (`BOUND`, `ONTOLOGY_UNBOUND` or `NO_RELEASE`);
- a population fingerprint (never the filter values);
- the window, direction, severity and detection time.

Measured values, segment names and narrative stay in the source.

**Binding.** A signal is bound to a concept only when the published release
has a validated binding to the same view and metric, with the same claim
fingerprint as the definition the detection used.

**Grouping into incidents.** A new signal joins an open incident when all of
these hold:
- the population fingerprint is the same;
- the windows overlap by at least half of the shorter one;
- the concepts are the same or linked by an active relation in the published
  release (for unbound signals: the same view and metric).

The incident records the reason as a rule (`same_concept`,
`related_concepts` with the relation id, `same_metric`). Its causal status
is always `unknown`. A closed incident never takes new signals.

**Replay safety.** Signal ids derive from the source and its proof digest, and
an incident id from the signal that opened it.
- Every newsroom cycle records the signals of its whole edition, so a cycle
  interrupted after its stories were saved is completed by the next one.
- Every monitor run records its detection again.
- Recording never fails the edition or the detection.

**Reading.** `GET /intelligence/incidents` and
`GET /intelligence/incidents/{id}` sit behind the News entitlement. Every
source is proven again for the reader: stories through the newsroom reader
proof, NewsItems through the caller's own scope. Only proven signals are
shown, and severity, window and grouping are computed from them. Concept names
appear only for concepts the reader's role may see. An incident with no
provable signal answers the same 404 as a missing one.

**Lifecycle.** `POST /intelligence/incidents/{id}/transition` moves an
incident through `OPEN`, `TRIAGED`, `INVESTIGATING`, `DECISION_PENDING`,
`ACTION_IN_PROGRESS`, `OBSERVING`, `RESOLVED` and `DISMISSED`. Each move needs
a reason, `expected_revision` and an `operation_id`, and only allowed moves
are accepted. Every read and move is audited, refusals included.

## News to Studio investigation

**Handoff.** In a News story, **Investigate in Studio** calls
`POST /intelligence/stories/{id}/investigate`
(`backend/app/modules/intelligence/investigations.py`). The server:
1. proves the story again for the caller;
2. records its signal and incident;
3. creates, or finds again, a Smart thread and an investigating Mission. Their
   ids derive from the story, its proof digest and the caller's principal and
   role, so a second click or a retry opens the same Mission, and each reader
   gets their own;
4. anchors the Mission to the incident by reference (`Mission.incident_anchor`).
   The headline is never passed on as trusted context;
5. moves an `OPEN` or `TRIAGED` incident to `INVESTIGATING`.

Studio opens that thread with a prepared prompt and the Mission selected.
Resuming the Mission later proves the story again, so a reader who lost
access cannot continue.

**Tools.** The Smart root and any agent that bundles them can use:
- `incident_context`: reads the incident (`LoopContext.incident_id`) with
  every source proven again for the caller.
- `investigation_update`: posts a finding. Each claim cites the `evidence_id`
  of a verified result table from the current run, and anything else is
  refused (`UNVERIFIED_EVIDENCE`). A claim's causal status is `arithmetic`,
  `association` or `unknown`; an agent can never assert `supported_effect`.
  Only digests of the cited evidence are stored.

Both need consent. The update tool writes a reviewable proposal
(`session_change`), never a published fact.

**Review and timeline.** Findings are stored in
`CONFIG_INTELLIGENCE_INVESTIGATION_UPDATES` as `PENDING_REVIEW`. Their figures
were computed under the author's data policy, so:
- a finding is shown only to readers whose active role is the author's and
  who can prove every signal the author could see;
- another reader in that role, never the author, approves or rejects it
  (`POST /intelligence/incidents/{id}/updates/{update_id}/review`).

The story panel's **Related signals and findings** section
(`GET /intelligence/stories/{id}/incident`) shows:
- related signals as concepts and grouping rules;
- the correlation caveat;
- reviewed findings with their causal labels;
- findings waiting for review, which can be reviewed there;
- the incident's transitions.

The original story is never changed.

## Learning without mutation

**Agent proposals.** `knowledge_propose` turns a user's correction or teaching
into an ontology proposal with source `AGENT`, submitted for the caller. It can:
- add other names (aliases) to a concept;
- revise a definition;
- propose a new concept in a domain the caller can see.

It needs consent (`session_change`), is bundleable, and is a Smart root tool.
Nothing changes until a steward of the domain approves the proposal and a
release is published. The caller can never approve it, because the caller is
the submitter. Agents are told never to claim the definition already changed.

**News feedback.** Likes and dislikes reorder only that reader's newspaper.
They never create a proposal or touch a definition.

**Business significance.** News ranking adds a deterministic boost with
visible reasons (`business_events.significance`):

| Feature | Weight | Reason shown |
|---|---|---|
| The story's concept has an active `AFFECTS_GOAL` link to a goal the reader can see | 0.75 | "Affects goal Finance.GrowthTarget" |
| Other stories in the reader's own newspaper are signals of the same incident | 0.5 | "Moved with 1 related story here" |
| The metric has an official definition the reader can see | 0.25 | "Official definition: Finance.NetRevenue" |

The weights are starting hypotheses, to be calibrated against reviewed News
labels; they are not learned values. Every feature reads only what the reader
already sees, so the ranking cannot reveal hidden signals or concepts. When the
reader's own preferences give no reason, the first significance reason
replaces "Largest move in this edition". If significance cannot be computed,
News ranks as before.

## Not yet built

- The operational object and action runtime (Phase 7). It is deferred until
  the News and Studio loop has been run on real data.

See the [proposal](proposals/supernova-business-ontology.md).
