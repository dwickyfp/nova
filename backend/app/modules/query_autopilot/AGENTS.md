# Query Autopilot

Inherit the root, backend, SQL frontend, and access-control guides. Read
[architecture](../../../../docs/arch-14-query-autopilot.md) and
[operations](../../../../docs/30-query-autopilot.md) for the current contract.

- All user-data SQL uses AuthorizedSQL and QueryService with the exact enrolled
  identity. Metadata repository system connections never execute workload data.
- Fingerprinting and snapshot rewriting belong in the central SQL frontend.
  Unclassified SQL cannot become an executable structural recommendation.
- A family shape does not authorize cohort sharing. Keep principal, active
  role, context version, catalog/database and policy/settings scope intact.
- Persist an intent before every engine or payload side effect. Reconcile an
  uncertain submission without resubmitting it. Lease loss interrupts execution
  and must leave a recoverable durable state rather than kill the worker loop.
- Approvals bind an exact candidate version, experiment, evidence, enrollment
  and policy. Experiments increment the candidate version. Do not restore an
  approval after an experiment or material evidence change.
- MV rewrite on the native fixture does not prove Ranger row-filter/mask safety.
  Missing scoped patched-FE acceptance blocks MV success and production apply.
- Keep fixture labels, live measurements, judge opinions and production outcomes
  separate in reports. Missing evidence is not a passing check. An LLM judge has
  no execution authority and receives only a reduced, bounded projection.
- Run the full backend gates plus affected query/proxy/security and real-engine
  trajectories. Tests here include cancellation, stale evidence, uncertain
  application/compensation, lease loss, and recovery between durable writes.
