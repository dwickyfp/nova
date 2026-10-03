# Intelligence learning and operations

This guide describes the implementation under development. The
[delivery matrix](delivery.md) records the required acceptance gates.

## Knowledge and review

Studio writes durable learning work against source messages. Evaluation threads
set `learning_enabled=false`; their messages cannot feed extraction, feedback
promotion or consolidation. A like creates a pending verified-query candidate.
It does not verify a business rule.

Private facts retain their existing memory IDs. Immutable revisions record
supporting evidence, competing statements and review history. Existing memories
start as private hypotheses. Preferences remain available through the existing
memory retrieval path. Published Semantic Views remain authoritative for
business calculations.

Use the memory dialog or the agent's Improve tab to inspect evidence and review
knowledge. Resolving a conflict leaves the selected statement unverified.
Verification can pin a published metric, filter or dimension and its semantic
fingerprint. Reviewers can also verify an evidence-backed domain heuristic;
that heuristic cannot define calculations or authorize business actions.
Verified domain knowledge can reach other accessible agents bound to the same
Semantic View. Retrieval rechecks the source agent and the viewer's current
source access. Sharing requires authorized review and source access. A semantic
change requires revalidation; stale or inaccessible evidence cannot be used to
approve a revision. Context inspection references the selected immutable
knowledge revision rather than creating another fact store.
Knowledge with a semantic reference links to the same Context Graph node used
by the semantic catalog. Retried projections repair missing edges without
duplicating nodes, and reads still reauthorize both endpoints.

Semantic Autopilot produces dataset, relationship, dimension, metric, synonym
and filter proposals through existing Semantic View ownership. Preview,
validation and regression checks precede publication. Publication probes data
access before acquiring the short write lease, then rechecks the candidate,
validation, baseline and originating session before its fenced writes. A
changed base version
requires a new review. Provider unavailability leaves extraction work pending
for a later bounded cycle. Invalid structured provider output also leaves the
source pending; a valid empty array records that no facts qualified. Learning
records public provider/model IDs and available token counts in the existing
source-message journal. Missing usage and billed costs remain unavailable.
The worker rechecks the originating session and role before accepting results.
New learning evidence also records the originating principal, active role,
session, security-context version and source observation time through the common
evidence contract. Public memory evidence omits authentication session IDs while durable provenance
retains the originating execution context. Older evidence retains explicitly unavailable timestamps;
review status does not manufacture a numerical knowledge-confidence score.

Context graphs always report a bounded view: traversal stops at the requested
depth, at most 100 nodes and at most 100 edges. Hidden endpoints do not consume
the visible node limit. The bounded flag does not reveal whether inaccessible
edges exist. Semantic authorization is reused only inside one bounded request
and keys on principal, role, session, security-context version and definition.
New requests reauthorize sources; agent resource restrictions and cycle
deadlines still apply to cache hits.

Each delegated source probe has a ten-second deadline. The complete source
check has a 120-second ceiling, including policy reads and queued batches.
Expiry cancels unfinished probes and returns unavailable access. No successful
partial batch can authorize a Semantic View.

## Monitoring and decisions

Create a monitor in the Semantic View surface. Check its metric, count and
completeness columns, observation period, minimum sample count and baseline.
Manual evaluation uses `POST /intelligence/monitors/{id}/run`. Scheduled work
uses the existing scheduler and worker with an explicit execution binding;
missing credentials or authorization fail closed.

Open News in Studio to inspect the evidence, contribution ranking and timeline.
Arithmetic contribution explains a reconciled change. A coincident deployment
or correlated metric supplies an association, not a causal conclusion. Decision
Lab supports causal effects only for its supported randomized design contract.
Unsupported designs return insufficient evidence. Randomized analysis uses one
outcome metric grouped only by the published assignment unit and treatment arm.
Caller filters, named filters, time selection, HAVING, transformed outcomes,
top-per-group selection and comparison cohorts are rejected before the analysis
query. Repeated units and truncated results cannot produce a supported effect.
A selected responder cohort does not inherit the original random assignment.

Persist decision options before requesting approval. Selection and approval
use explicit operations with expected revisions. Changed inputs or policy make
an earlier approval stale. Decision sharing grants visibility only after the
recipient's data access is re-evaluated; it cannot grant access to source rows.
Lineage connects the originating News revision, investigation, evidence,
decision events and outcomes.

## Outcomes and recovery

Evaluate a selected or approved decision using
`POST /intelligence/decisions/{id}/evaluate-outcome`. An open window stays
pending. A closed window without sufficient complete observations records
missing data. Re-evaluation can record later observations in a new immutable
outcome revision. Identical retries retain the same logical outcome. A complete
outcome creates at most one private candidate for that outcome identity;
contradictory observations require conflict review.

Observation after a decision does not prove that an action occurred or caused
the change. Outcome attribution remains separate from prediction error.
Overlapping decisions limit attribution. Learning candidates pin their source
outcome revision, and retrieval rechecks its prediction, baseline and actual
evidence. Outcomes never silently rewrite verified definitions.

Internal monitoring, consolidation and outcome handlers expose bounded
`run_once` operations. The existing task journals own durable work; Redis owns
leases and wakeups. Retry the original stable operation after interruption.
Do not substitute another principal's credentials or manually copy control-plane
rows to bypass a failed authorization check. Publication and projection recovery
use their recorded durable operations.

## Benchmark separation

`NOVA_INTELLIGENCE_BENCH` contains the observation dataset used by held-out
queries. The development outcome fixture clones observations into
`NOVA_INTELLIGENCE_OUTCOMES` and reveals synthetic future orders only after
decisions and approvals exist. These future orders exercise observed revenue
and learning; they do not represent executed inventory transfers or rollbacks,
or future gross-profit facts. The fixture does not fabricate future order items.

The production-path story test checks pending and missing-data states before
revealing future observations, then evaluates outcomes and private inferred
memory. It verifies the original observation rows against their CSV hashes and
checks that outcome learning did not publish another semantic version. This is
development evidence. Official B0/B1/B2 requires its own frozen implementation,
provider configuration, corpora and observation manifest. Paid runs remain
separate and opt-in; the live runner requires at least three paired repetitions.
Provision the isolated benchmark with an expiring session that remains valid for
the whole reserved run. Session expiry is a failed run; it does not authorize a
replacement identity. Local paired testing uses a 240-minute token lifetime and
14,400-second session TTL for its throwaway identity, then logs out and removes
that identity. Application defaults remain unchanged.
