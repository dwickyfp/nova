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
- If the claim changed only cosmetically, the binding is re-pinned with a
  reason ("Version N renamed or redescribed M; it computes the same thing") and
  its claim fingerprint moves to the new claim. Cosmetic means the meaning
  fingerprint is equal: names are resolved to what they read, so the dataset
  becomes its physical source, the default time dimension becomes its column,
  and expressions are qualified with sources instead of dataset names. A
  renamed time dimension or dataset is cosmetic; a different column, source,
  filter, unit or type is not. Publication audits these re-pins as `REPIN`.
- If the claim changed otherwise, or the metric is gone, the binding becomes
  `STALE`.
- If the claim is unchanged but its meaning is not (the same dataset name now
  reads another table, or the time dimension another column), the binding also
  becomes `STALE`: "Version N changed what M computes".

A stale binding is never usable. Release preparation and publication re-check
freshness themselves, so the result does not depend on the drift hook having
run.

## Derived layer

Code: `backend/app/modules/ontology/derived.py`. API: `GET /ontology/derived`,
`POST /ontology/derived/compile`. Console: the **Derived** tab.

Nova compiles what each published Semantic View already proves into a shared,
deterministic graph. It never needs a person to type it:

| Item | Rule | Base confidence |
|---|---|---|
| `BUSINESS_ENTITY` | A dataset, keyed by its physical table, so `outlets` in three views is one Outlet | 0.9 with a grain, 0.7 without |
| `DIMENSION` `RELATED_TO` its entity | A categorical or date field. Grain keys, join keys, identifier-named fields and numeric amounts are left out | 0.9 |
| `METRIC` that `MEASURES` its base entity | A public metric, with its claim fingerprint | 1.0 |
| `RELATED_TO` between entities, with a role | A many-to-one relationship. The key column names the role (`salesman_id` is the salesman role of Employee) unless it names the target (`outlet_id`) | 0.95, 0.6 if the cardinality is unknown |
| `DERIVES` from an input metric to the metric built from it | Proven on the parsed expression: `SUM(a)/SUM(b)` is built from the metrics `SUM(a)` and `SUM(b)`; `SUM(a - b)` from `SUM(a)` and `SUM(b)`. Only aggregates over the same base dataset match | 0.95 |
| `EQUIVALENT_TO` between metrics | The same claim fingerprint in two views | 0.9 |

Each further view that proves an item adds 0.05, up to 1.0. Every item keeps
its evidence: view id, version and fingerprint, the method, the source object,
and what that view calls and says the item is. Names, meaning and aliases are
read from all evidence in view order, so the result does not depend on which
view compiled last.

**Compilation.** Publishing a view version compiles it after the drift check;
a failure is logged and never undoes the publication. `POST /derived/compile`
recompiles one visible view, or every published view the caller can see, and
is audited as `DERIVE`. A compile replaces only its own view's evidence: an
item no view proves any more becomes `RETIRED`. Writes are one batch under one
lease (`RevisionRepository.save_many`), so a recompile with no change writes
nothing. Records are stored append-only in `CONFIG_ONTOLOGY_DERIVED` and
`CONFIG_ONTOLOGY_COMPILATIONS`.

**Reading.** A reader sees only evidence from views they can see; an item
left without evidence is hidden, its names come only from visible views, and
a relation is shown only when both ends are. A derived metric links to the
approved concept with a validated binding to it, when the reader can see that
concept's domain.

`APPLIES_TO` stays reserved for policies, as the relation rules require.

**Authority.** Derived items are context and candidates. They are never
released, never bound and never override an approved concept. Use in Studio
retrieval is a later phase of the
[auto-ontology proposal](proposals/supernova-auto-ontology.md).

### Promotion

Code: `backend/app/modules/ontology/promotion.py`. API:
`POST /ontology/derived/propose {operation_id, shared_domain_id?}`. Console:
**Propose to stewards** on the Derived tab.

Nova turns what it derived into ordinary proposals with `source=ENGINE`, one
per domain, submitted by the caller. Stewards review them like any proposal,
and the caller can never review their own.

- A view belongs to the domain that holds most approved concepts bound to it.
  A view with no such concept is left out, with a reason ("bootstrap it once").
- Each visible metric that is neither official nor already in an open
  proposal becomes a `METRIC` concept with a binding to its view.
- An entity goes to the one domain whose metrics or related entities use it.
  An entity several domains use goes to `shared_domain_id`, or is left out
  with a reason. An approved entity with the same label is reused.
- `MEASURES`, `DERIVES` and entity-to-entity `RELATED_TO` links are proposed
  when both ends are official or created in the same draft. Each carries a
  justification with its confidence, view, version and source, and the role
  when there is one. A link whose end is approved later is proposed on a later
  run.
- Names never collide with a domain's slugs, labels or aliases, and a draft
  holds at most 200 changes. Rerunning drafts nothing already open, and the
  operation id is derived from the request, domain and content, so a retry
  creates nothing twice. Every run is audited as `PROPOSE_DERIVED`.

## Meaning checks

Code: `backend/app/modules/ontology/lint.py`. API: `GET /ontology/lint`.
Console: the **Checks** tab.

Deterministic rules compare what stewards approved with the formula Studio
runs. They never call a model, and only check concepts and views the caller can
see:

| Rule | Severity | Finds |
|---|---|---|
| `definition_formula` | error | A definition names another metric whose inputs the formula only partly uses: "Omzet bersih dikurangi harga pokok" over `SUM(net_amount)` leaves out the returned value net revenue subtracts |
| `adjustment_ignored` | warning | A metric sums a column that another metric of the same view corrects with a returns or cancellation column, and neither its formula nor its wording mentions them |
| `ambiguous_name` | warning | One name belongs to approved concepts in two domains, so the resolver asks |
| `unbound_metric` | warning | An approved metric no validated binding computes |

Mentions are found with the resolver's whole-word matching; inputs come from
the derived `DERIVES` graph, and columns from the parsed expression of each
outermost aggregate. Release preparation adds every finding as a warning
("Meaning check (rule) concept: …"). Findings never block a release or change
meaning: a steward decides whether the words or the formula are wrong.

## Names from Nova

Code: `backend/app/modules/ontology/enrichment.py`. API:
`POST /ontology/enrich {operation_id, domain_id?}`. Console: **Suggest names**
on the Checks tab.

The resolver only matches names a concept carries, so every everyday word a
concept lacks is a question Studio cannot ground. Nova proposes them:

- Relation roles from the derived layer (`driver`, `salesman`) become aliases of
  the entity, with no model involved.
- One structured call per batch of 15 concepts asks the default model for
  everyday Indonesian and English words, a definition where only a placeholder
  exists, and current names that could mean another concept. It receives
  names, definitions, units and formulas, never data values. Batches run three
  at a time; unreadable output is repaired once; a batch the provider cannot
  answer is reported, and only a run where no batch answered fails (503).
- A validator keeps a word only if it is 1-4 words and at most 40 characters,
  not a number or a generic word, states no cause, is not already a name of a
  visible concept, a name an open proposal gives, or a view synonym of another
  official metric, and was not suggested for two concepts. Validation runs in
  batch order, so a word one batch keeps is taken for the next.

What survives becomes one `ENGINE` proposal per domain of `update` changes,
listing each word with its source and each ambiguity note. Concepts an open
proposal already changes are left alone. Runs are audited as `ENRICH`. A
steward still judges meaning the validator cannot, such as a related but
different measure, and rejects or resubmits without the word.

## Measured accuracy

Code: `backend/app/modules/ontology/evaluation/`, `backend/app/common/statistics.py`.
API: `GET /ontology/metrics`; `GET|POST /ontology/evaluation/…` (ACCOUNTADMIN).
Console: the **Overview** tab, the page's default, and the **Evaluation** tab.
Design and decisions: [Phase A](proposals/supernova-phase-a-measurement.md).

**Gold sets.** `POST /evaluation/gold` builds a versioned set from published
Semantic Views, with no domain-specific code.

- **Answerable cases** are governed plans: each metric gets up to N templates
  (total, breakdown, top 5, monthly trend, a real filter value read from the
  data) over absolute periods inside the observed data window.
  - The compiler that serves Studio writes their SQL. It is executed again, as
    the evaluating user, on every run.
  - Breakdowns skip keys, coordinates and amounts, and a count of distinct X is
    never broken down by X.
- **Questions.** A model writes each question in business words, without the
  technical names.
- **Unanswerable cases (about 15%)** are measures, breakdowns or periods outside
  the catalog. A role a measure has no path to ("missed stops per salesman") is
  also unanswerable.
- **Ambiguous cases (about 10%)** are names two domains share, from the meaning
  check, or vague words that fit several measures.
- **Admission.** A judge model reviews every case, and any external reviewer
  (`POST /evaluation/cases/{set}/reviews`) can add a verdict. A case is active
  only while every reviewer says yes.

**Answer runs.** `POST /evaluation/answers {set_name, mode}` answers the active
cases the way Studio does:

- `ground` with the pinned release;
- `planning_catalog` and `generate_plan(business_concepts=…)`;
- `missing_metrics` and `validate_plan`;
- compile, then execute as the caller.

Mode `without_ontology` skips grounding and the hints. This is the baseline, and
it adds no branch to production code. Results match Genie benchmarks: order-free
unless the plan orders, numbers to 4 significant digits, extra columns allowed,
an empty result never matches.

Each case records its outcome (`correct`, `wrong`, `answered`, `abstained`,
`clarified`, `failed`), a TrustSQL reliability score, grounding status, plan,
SQL fingerprint, row counts, tokens and latency. Result rows and measure values
are never stored. A filtered question keeps the category value it asks about.

**Claim audits.** `POST /evaluation/claims` samples up to N claims per stratum:

- entity, dimension, metric;
- metric to entity, entity link, entity role;
- built from, same formula;
- official alias.

A judge decides each claim against the excerpt of the published definition it
rests on. The summary is a stratified estimate weighted by stratum size, with a
95% interval, plus the ECE, Brier and reliability bins of the stored confidence.

**Runs and agreement.**

- Runs advance in resumable steps (`POST /evaluation/runs/{id}/step`) in the
  caller's live session. Only their starter can advance them.
  - A provider timeout keeps a run open for the next step.
  - Any other failure marks it `FAILED`, and `…/resume` reopens it where it
    stopped.
- External reviews of results (`…/reviews`) give Cohen's κ against the model
  judge.

**Overview.** `GET /ontology/metrics` returns only stored measurements and live
counts, each a proportion with its Wilson interval:

- answer accuracy with and without the ontology, and the paired effect
  (McNemar);
- reliability, wrong answers and refusals;
- claim accuracy by kind, and calibration;
- coverage by domain, stale bindings, meaning checks, the acceptance of Nova's
  proposals, the gold set, and judge agreement.

Anything not yet measured is listed as such, never shown as a default.

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
| Grounded | The binding's view is used, the planner receives the concepts and their metrics, and a plan that omits a required metric is rejected unless a planned metric explains the mention (below) |

**Mentions a planned metric explains.** A word can name a concept without
asking for it: "gross sales before *discount*" names Discount amount while
asking for Gross revenue. The resolver records which words named each concept.
`grounding_gaps` releases a required concept when either:

- its metric is an input of a planned metric, proven on the parsed expressions;
  or
- those words appear, as whole words, in the planned metric's description,
  synonyms, name or official definition.

Substitution stays refused, because a different metric's own words do not name
the concept asked for. A question that asks for two metrics still needs both.
Measured on the SerbaData gold set, this raised accuracy with the ontology from
80.5% to 93.5% (McNemar p=0.0004); see the
[Phase A checkpoint](testing/supernova/phase-a-checkpoint.md).

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
`frontend/src/features/ontology/`). The page has eight tabs; it opens on Overview:

- **Overview.** The [measured accuracy](#measured-accuracy) of the ontology:
  key numbers with their intervals and n, the effect against the baseline,
  where answers end up, calibration, claim accuracy by kind, coverage by domain
  and health. Unmeasured numbers say so.
- **Concepts.** Shows the published release by default, or the latest
  approved definitions. Opening a concept shows:
  - its definition and facts;
  - the metrics that compute it, with whether each binding is in use;
  - related concepts and its version history.

  From there a reader can propose a new concept (optionally bound to a
  published metric), a revision, a link or a deprecation. A deprecation also
  retires the concept's links and revokes its bindings. **Draft from a
  Semantic View** runs the bootstrap.
- **Derived.** The entities Nova derived, with their roles, dimensions,
  metrics, related entities and evidence. **Compile now** recompiles the
  visible views; **Propose to stewards** drafts the per-domain proposals
  described under [Promotion](#promotion) and lists what it left out and why.
- **Checks.** The [meaning checks](#meaning-checks) with their evidence, and
  **Suggest names**, which runs [Names from Nova](#names-from-nova) and lists
  the proposals sent and the words the validator dropped.
- **Proposals.** Grouped as in review, approved, applied and closed. Each
  proposal shows who or what drafted it, its changes in words (with each
  link's justification and each binding's view name), the domains it touches, which domains
  still wait for a steward, and its reviews. A steward of an affected domain
  approves or rejects with a rationale. The submitter can withdraw but never
  review. An approved proposal whose application stopped can be finished.
- **Evaluation.** For ACCOUNTADMIN: the runs, their results per question or
  claim, the gold set with its reviews, and starting or continuing a run.
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
`POST /intelligence/stories/{id}/investigate` with the reader's default agent as
`{"agent_id": "..."}`
(`backend/app/modules/intelligence/investigations.py`). The caller must own the
agent, or it must be shared with their active role, and pass the agent access
check. Without an agent the button is disabled ("Create an agent to
investigate"). The server:
1. proves the story again for the caller;
2. records its signal and incident;
3. creates, or finds again, a thread with that agent and an investigating
   Mission. Their ids derive from the story, its proof digest, the caller's
   principal and role, and the agent, so a second click or a retry opens the
   same Mission, and each reader gets their own;
4. anchors the Mission to the incident by reference (`Mission.incident_anchor`).
   The headline is never passed on as trusted context;
5. moves an `OPEN` or `TRIAGED` incident to `INVESTIGATING`.

Studio opens that thread with a prepared prompt and the Mission selected.
Resuming the Mission later proves the story again, so a reader who lost
access cannot continue.

**Tools.** An agent that bundles them can use:
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

It needs consent (`session_change`) and is bundleable.
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
