# Atomic AI configuration and registration intent

Revision `0008_ai_function_definitions.sql` joins the prepared AI registry and
alias stores to the durable queue. `ai_ml.metadata_service.AIConfigurationService`
owns each configuration command within the caller's PG unit of work. It has no
provider or engine network access. The running application still uses its
StarRocks services; this is not the AI runtime cutover.

## Persisted contract

A command locks AI configuration mutations, applies the revision-checked metadata write,
and reconciles every changed function selection before returning. Its savepoint
contains the alias/provider/model change, immutable definition, current pointer,
registration job and outbox event. It never commits the enclosing transaction.
The registry and alias repositories use the same lock before taking row locks,
so composing them directly cannot invert the service lock order.

The snapshot records the selected alias, provider and model revisions, prompt,
model descriptor, effective engine parameters and explicit configured/disabled/
unconfigured state. Contract version 1 identifies the rendering contract the
future participant must implement. Model descriptor revision remains distinct
from its integer metadata revision. The DB returns snapshot JSON as text so
Python never rounds a parameter through a floating-point decode/re-encode.
The SHA-256 digest is checked against the exact stored UTF-8 bytes.

Snapshots omit provider keys, ciphertext, endpoints, arbitrary headers and
model/endpoint overrides in alias parameters. Only temperature, maximum tokens,
top-p and timeout parameters enter the definition. The snapshot guard validates
numeric types and bounds before any intent commits. The original alias parameter
text remains in the configuration table; this filtered snapshot is not a public
secret-readback API. Custom prompts remain private configuration content.

`ai_function_definitions` is append-only for runtime credentials.
`ai_function_heads` advances one version at a time and references the exact
definition ID/type/version/digest tuple. A definition must match the current
source selection when inserted. Deferred checks require its head and job at
commit; the queue's existing constraint requires its outbox notification.

A job of kind `ai.function.reconcile` must use the maintenance queue, declare
an external effect, and carry that definition's digest, version, original actor
and function resource key. One definition permits one such job. Re-enqueue with
the same immutable idempotency contract returns the existing job; a second
independent registration for that definition conflicts.

Deferred guards on runtime provider/model/alias writes compare the final source
selection with its current definition. A direct metadata mutation that forgets
registration intent cannot commit. Provider/model changes reconcile each affected
selected function. Moving an alias to another function reconciles both sides;
deleting the last selected alias records an unconfigured definition. Changes to
an unselected alias do not install it as a different function.

## Scope, permissions and import

Only the web workload with an active administrator role can publish definitions
and heads. Active administrators, the maintenance workload under a SERVICE actor,
and the scoped readonly login can read them. A maintenance login carrying a USER
actor sees no definitions. Stored actor context is provenance, not a substitute
for the future worker's required live authorization/revocation checks.

Privileged import is outside the runtime consistency trigger. The importer must
hold the maintenance/deployment lock, preserve source records, build and verify
all current definitions, and validate the entire dataset before publishing an
epoch. Regular reconciliation skips a never-configured slot; cutover must also
initialize and verify each empty function slot so a stale native function cannot
survive merely because no alias was imported. That import/cutover protocol
remains open. The fixture's privileged row
imports prove representation only; they are not production import acceptance.

The existing registry/alias integration fixture now supplies the service's
registration boundary before commit. Negative tests explicitly omit it and
assert commit refusal. No assertion was removed and no permission was broadened
to accommodate those tests.

## Remaining runtime work

PENDING means the intent is stored. It does not mean StarRocks has installed the
function. A maintenance worker still needs a version-aware renderer, exact
native-function observation, intent/receipt verification and superseded-job
handling. An expired lease must not trigger another DROP/CREATE without proof
that the previous operation cannot still commit. The worker must preserve the
existing resource fence for unknown outcomes.

Configuration audit now joins this transaction through the fixed
[audit triggers in revision 0009](PG_AUDIT.md). Endpoint/key resolution, gateway revocation, engine
cache partitioning, the missing managed `embed` implementation, API/UI revision
tokens, caller ports and all-domain cutover remain open. No live provider or
engine mutation was performed by these storage tests. The owner-waived live
embedding test is separate from the synthetic descriptor/reference checks.
