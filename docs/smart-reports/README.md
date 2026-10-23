# Smart Reports implementation record

Status: **P0–P2 implemented; local acceptance passed**. The delivery branch is
`feat/intelligent-studio-harness-v2`. See the status and test matrix for measured
results, prerequisites and verification limits. Existing unrelated changes are
preserved in the original checkout.

- [Delivery status and recovery](STATUS.md)
- [Current-state audit](AUDIT.md)
- [Implementation plan and acceptance mapping](IMPLEMENTATION_PLAN.md)
- [Architecture and decisions](ARCHITECTURE.md)
- [Test matrix](TEST_MATRIX.md)
- [Intelligence UI and report judge review](UI_JUDGE.md)
- [Demo and operational runbook](RUNBOOK.md)

The production specification supplied for this work is
`NOVA-SMART-REPORT-PRODUCTION-SPEC.md`. Product decisions in the approved user plan
take precedence: Reports replaces the Dashboards navigation, legacy sources stay
available, all dashboards receive replacement drafts, and delivery remains inside
Nova. There is no feature flag. A capability registry describes implemented query
and rendering behavior; it does not enable or disable rollout.
