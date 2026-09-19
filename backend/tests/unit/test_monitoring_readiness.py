"""Unit tests for the production readiness audit (monitoring/readiness.py).

The audit's assessment functions are pure over an ``AuditInput`` snapshot, so
these tests pin the findings an operator relies on:

* a 3/3 FE + 3/3 BE cluster passes membership and storage;
* a missing BE, low disk headroom and a bad repository each surface at the
  right status;
* **the external gates are always emitted as REVIEW** — an audit must never
  read "all green" while backup-restore, failure domains and alert delivery are
  unproven;
* the summary verdict precedence is BLOCKED > NEEDS_CHANGE > REVIEW > PASS.
"""

from __future__ import annotations

from app.modules.monitoring.readiness import (
    AuditInput,
    assess_backup,
    assess_membership,
    assess_security,
    assess_storage,
    assess_version_consistency,
    build_report,
    external_gate_findings,
    summarize,
)


def _fe(role, alive=True, join=True, version="4.1.0", journal=100):
    return {
        "Role": role,
        "Alive": str(alive),
        "Join": str(join),
        "Version": version,
        "ReplayedJournalId": journal,
        "Host": f"{role}-{journal}",
        "ClusterId": "12345",
    }


def _be(ip="10.0.0.1", alive=True, version="4.1.0", disk="10.0"):
    return {
        "IP": ip,
        "Alive": str(alive),
        "Version": version,
        "MaxDiskUsedPct": disk,
        "SystemDecommissioned": "false",
        "ClusterDecommissioned": "false",
    }


def _healthy_input() -> AuditInput:
    return AuditInput(
        cluster_id="12345",
        version="4.1.0",
        fe_rows=[
            _fe("LEADER", journal=100),
            _fe("FOLLOWER", journal=100),
            _fe("FOLLOWER", journal=100),
        ],
        be_rows=[_be("10.0.0.1"), _be("10.0.0.2"), _be("10.0.0.3")],
        repository_rows=[{"Name": "backup_repo", "ErrMsg": ""}],
        root_password_rejected=True,
    )


class TestMembership:
    def test_healthy_cluster_passes(self):
        statuses = {f.name: f.status for f in assess_membership(_healthy_input())}
        assert statuses["FE voting group"] == "PASS"
        assert statuses["BE availability"] == "PASS"
        assert statuses["FE journal replay lag"] == "PASS"

    def test_two_fes_is_blocked(self):
        data = _healthy_input()
        data.fe_rows = [_fe("LEADER"), _fe("FOLLOWER")]
        statuses = {f.name: f.status for f in assess_membership(data)}
        assert statuses["FE voting group"] == "BLOCKED"

    def test_no_leader_is_blocked(self):
        data = _healthy_input()
        data.fe_rows = [
            _fe("FOLLOWER", journal=1),
            _fe("FOLLOWER", journal=1),
            _fe("FOLLOWER", journal=1),
        ]
        statuses = {f.name: f.status for f in assess_membership(data)}
        assert statuses["FE Leader election"] == "BLOCKED"

    def test_fewer_than_three_bes_is_blocked(self):
        data = _healthy_input()
        data.be_rows = [_be("10.0.0.1"), _be("10.0.0.2")]
        statuses = {f.name: f.status for f in assess_membership(data)}
        assert statuses["BE availability"] == "BLOCKED"

    def test_decommissioned_be_is_not_counted(self):
        data = _healthy_input()
        data.be_rows = [
            _be("10.0.0.1"),
            _be("10.0.0.2"),
            {**_be("10.0.0.3"), "SystemDecommissioned": "true"},
        ]
        statuses = {f.name: f.status for f in assess_membership(data)}
        assert statuses["BE availability"] == "BLOCKED"

    def test_high_journal_lag_needs_change(self):
        data = _healthy_input()
        data.fe_rows = [
            _fe("LEADER", journal=5000),
            _fe("FOLLOWER", journal=100),
            _fe("FOLLOWER", journal=100),
        ]
        statuses = {f.name: f.status for f in assess_membership(data)}
        assert statuses["FE journal replay lag"] == "NEEDS_CHANGE"


class TestVersionAndStorage:
    def test_mixed_versions_needs_change(self):
        data = _healthy_input()
        data.be_rows = [
            _be("10.0.0.1", version="4.1.0"),
            _be("10.0.0.2", version="4.0.0"),
            _be("10.0.0.3", version="4.1.0"),
        ]
        findings = assess_version_consistency(data)
        be = next(f for f in findings if f.name.startswith("BE"))
        assert be.status == "NEEDS_CHANGE"

    def test_disk_above_95_is_blocked(self):
        data = _healthy_input()
        data.be_rows = [_be(disk="96.0"), _be("10.0.0.2"), _be("10.0.0.3")]
        findings = assess_storage(data)
        assert findings[0].status == "BLOCKED"

    def test_disk_between_90_and_95_needs_change(self):
        data = _healthy_input()
        data.be_rows = [_be(disk="91.0"), _be("10.0.0.2"), _be("10.0.0.3")]
        findings = assess_storage(data)
        assert findings[0].status == "NEEDS_CHANGE"

    def test_healthy_disks_pass(self):
        findings = assess_storage(_healthy_input())
        assert findings[0].status == "PASS"


class TestBackupAndSecurity:
    def test_no_repository_needs_change(self):
        data = _healthy_input()
        data.repository_rows = []
        findings = assess_backup(data)
        assert findings[0].status == "NEEDS_CHANGE"

    def test_repository_with_error_needs_change(self):
        data = _healthy_input()
        data.repository_rows = [{"Name": "r", "ErrMsg": "S3 bucket not found"}]
        findings = assess_backup(data)
        assert findings[0].status == "NEEDS_CHANGE"

    def test_healthy_repository_but_restore_is_review(self):
        findings = assess_backup(_healthy_input())
        by_name = {f.name: f.status for f in findings}
        assert by_name["Backup repository"] == "PASS"
        assert by_name["Restore exercise"] == "REVIEW"

    def test_empty_root_password_rejected_passes(self):
        assert assess_security(_healthy_input())[0].status == "PASS"

    def test_empty_root_password_accepted_is_blocked(self):
        data = _healthy_input()
        data.root_password_rejected = False
        assert assess_security(data)[0].status == "BLOCKED"

    def test_unknown_root_probe_is_review(self):
        data = _healthy_input()
        data.root_password_rejected = None
        assert assess_security(data)[0].status == "REVIEW"


class TestExternalGates:
    def test_external_gates_are_always_review(self):
        findings = external_gate_findings()
        assert findings, "external gates must always be emitted"
        assert all(f.status == "REVIEW" for f in findings)

    def test_external_gates_cover_the_expected_categories(self):
        categories = {f.category for f in external_gate_findings()}
        assert {
            "monitoring",
            "infrastructure",
            "network-security",
            "capacity",
            "operations",
        } <= categories


class TestSummary:
    def _finding(self, status):
        from app.modules.monitoring.readiness import Finding

        return Finding("x", "n", status, "d")

    def test_blocked_beats_all(self):
        result = summarize(
            [self._finding("PASS"), self._finding("BLOCKED"), self._finding("REVIEW")]
        )
        assert result["result"] == "PRODUCTION_ACCEPTANCE_BLOCKED"

    def test_needs_change_after_blocked(self):
        result = summarize([self._finding("PASS"), self._finding("NEEDS_CHANGE")])
        assert result["result"] == "PRODUCTION_ACCEPTANCE_NEEDS_CHANGE"

    def test_review_makes_pending(self):
        result = summarize([self._finding("PASS"), self._finding("REVIEW")])
        assert result["result"] == "PRODUCTION_ACCEPTANCE_PENDING"

    def test_all_pass_is_passed(self):
        result = summarize([self._finding("PASS"), self._finding("PASS")])
        assert result["result"] == "PRODUCTION_ACCEPTANCE_PASSED"


class TestBuildReport:
    def test_healthy_cluster_is_pending_due_to_external_gates(self):
        # Even a healthy engine cannot be "PASSED" because external gates are
        # always REVIEW — this is the point of the audit, not a bug.
        report = build_report(_healthy_input())
        assert report["result"] == "PRODUCTION_ACCEPTANCE_PENDING"
        assert report["metadata"]["cluster_id"] == "12345"

    def test_report_findings_have_valid_statuses(self):
        report = build_report(_healthy_input())
        assert all(
            f["status"] in {"PASS", "NEEDS_CHANGE", "BLOCKED", "REVIEW"}
            for f in report["findings"]
        )

    def test_unreachable_engine_marks_metrics_gate_review(self):
        data = _healthy_input()
        data.engine_reachable = False
        report = build_report(data)
        gate = next(
            f for f in report["findings"] if f["name"] == "External alert delivery"
        )
        assert gate["status"] == "REVIEW"

    def test_report_is_deterministic_in_finding_order(self):
        first = [f["name"] for f in build_report(_healthy_input())["findings"]]
        second = [f["name"] for f in build_report(_healthy_input())["findings"]]
        assert first == second
