"""Production readiness audit — read-only findings against a running cluster.

Nova is a management console over an already-deployed StarRocks cluster. This
module answers the operator's question *"is this cluster actually production
grade right now?"* without ever mutating it — no node is added, dropped, moved
or restarted, and no application data is touched.

Attribution: the finding taxonomy and thresholds are adapted from the
production acceptance automation in
``abdull93/StarRocks-Production-automation`` (``sr/production_acceptance.py``),
rewritten against Nova's system-pool reads instead of SSH. The key idea carried
over verbatim is the **status vocabulary**:

- ``PASS``          — proven good from inside the engine.
- ``NEEDS_CHANGE``  — a real gap the operator can fix (capacity, repository).
- ``BLOCKED``       — a condition that makes production operation unsafe.
- ``REVIEW``        — cannot be proven from inside the engine (external gates:
  ESXi failure domains, firewall, alert delivery); surfaced, never silently
  passed.

The last category matters: an audit that reports "all green" by omitting what it
cannot see is worse than no audit. These gates are always emitted so a reader
knows what remains unproven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Valid finding statuses, highest-severity first for summarisation.
STATUSES = ("BLOCKED", "NEEDS_CHANGE", "REVIEW", "PASS")


@dataclass
class Finding:
    """One audit observation, tied to a category and (optionally) a node."""

    category: str
    name: str
    status: str
    detail: str
    node: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unknown finding status: {self.status!r}")

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "category": self.category,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.node:
            out["node"] = self.node
        return out


@dataclass
class AuditInput:
    """Raw observations gathered by the service, evaluated by pure functions.

    Keeping this a plain dataclass (no DB handle) is what makes the assessment
    logic unit-testable without an engine.
    """

    cluster_id: str | None = None
    version: str | None = None

    fe_rows: list[dict] = field(default_factory=list)
    be_rows: list[dict] = field(default_factory=list)
    repository_rows: list[dict] = field(default_factory=list)
    root_password_rejected: bool | None = None

    engine_reachable: bool = True


# ── Pure validators (no I/O) ─────────────────────────────────────────


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "alive", "ok"}


def _voters(rows: list[dict]) -> list[dict]:
    return [r for r in rows if str(r.get("Role", "")).upper() in {"LEADER", "FOLLOWER"}]


def assess_membership(data: AuditInput) -> list[Finding]:
    """FE quorum, leader election, journal lag and BE availability."""
    findings: list[Finding] = []

    voters = _voters(data.fe_rows)
    leaders = [r for r in voters if str(r.get("Role", "")).upper() == "LEADER"]
    followers = [r for r in voters if str(r.get("Role", "")).upper() == "FOLLOWER"]
    alive_voters = [
        r
        for r in voters
        if _truthy(r.get("Alive")) and _truthy(r.get("Join"))
    ]

    if len(voters) < 3:
        findings.append(
            Finding(
                "membership",
                "FE voting group",
                "BLOCKED",
                f"Only {len(voters)} voting FE(s); a 3-FE candidate/leader topology "
                "is the minimum for metadata fault tolerance.",
            )
        )
    elif len(leaders) != 1:
        findings.append(
            Finding(
                "membership",
                "FE Leader election",
                "BLOCKED",
                f"Expected exactly one Leader, found {len(leaders)}.",
            )
        )
    elif len(alive_voters) < len(voters):
        findings.append(
            Finding(
                "membership",
                "FE liveness",
                "BLOCKED",
                f"{len(alive_voters)}/{len(voters)} voting FEs Alive and Joined; "
                "quorum is degraded.",
            )
        )
    else:
        findings.append(
            Finding(
                "membership",
                "FE voting group",
                "PASS",
                f"{len(voters)} voters, 1 Leader, {len(followers)} Followers, all Alive.",
            )
        )

    # Journal replay lag: the metadata staleness bound for a failover.
    journals: list[int] = []
    for r in voters:
        raw_journal = r.get("ReplayedJournalId")
        if raw_journal is None:
            continue
        try:
            journals.append(int(raw_journal))
        except (TypeError, ValueError):
            continue
    if len(journals) >= 2 and journals:
        lag = max(journals) - min(journals)
        if lag > 1000:
            findings.append(
                Finding(
                    "membership",
                    "FE journal replay lag",
                    "NEEDS_CHANGE",
                    f"Max replay lag is {lag} (>1000); a failover would lose metadata.",
                )
            )
        else:
            findings.append(
                Finding(
                    "membership",
                    "FE journal replay lag",
                    "PASS",
                    f"Max replay lag is {lag} (<=1000).",
                )
            )

    # BE availability.
    alive_be = [
        r
        for r in data.be_rows
        if _truthy(r.get("Alive"))
        and not _truthy(r.get("SystemDecommissioned"))
        and not _truthy(r.get("ClusterDecommissioned"))
    ]
    if len(alive_be) < 3:
        findings.append(
            Finding(
                "membership",
                "BE availability",
                "BLOCKED",
                f"Only {len(alive_be)} Alive, non-decommissioned BE(s); "
                "replication_num=3 cannot place a full replica set.",
            )
        )
    else:
        findings.append(
            Finding(
                "membership",
                "BE availability",
                "PASS",
                f"{len(alive_be)} Alive, non-decommissioned BE(s).",
            )
        )

    return findings


def assess_version_consistency(data: AuditInput) -> list[Finding]:
    """Every node should run the same build; mixed builds break rollouts."""
    out: list[Finding] = []
    for label, rows in (("FE", data.fe_rows), ("BE", data.be_rows)):
        versions = {
            str(r.get("Version")).strip()
            for r in rows
            if str(r.get("Version", "")).strip()
        }
        if not versions:
            out.append(
                Finding(
                    "version",
                    f"{label} build uniformity",
                    "REVIEW",
                    "No version information reported by the engine.",
                )
            )
        elif len(versions) == 1:
            out.append(
                Finding(
                    "version",
                    f"{label} build uniformity",
                    "PASS",
                    f"All {label} nodes report {next(iter(versions))}.",
                )
            )
        else:
            out.append(
                Finding(
                    "version",
                    f"{label} build uniformity",
                    "NEEDS_CHANGE",
                    f"Mixed {label} builds: {', '.join(sorted(versions))}.",
                )
            )
    return out


def assess_backup(data: AuditInput) -> list[Finding]:
    """A configured, healthy repository is the floor for backup readiness.

    Presence of a repository does **not** prove a restore works — that is a
    separate REVIEW gate, because this module cannot read backup logs.
    """
    rows = data.repository_rows
    if not rows:
        return [
            Finding(
                "backup",
                "Backup repository",
                "NEEDS_CHANGE",
                "No StarRocks backup repository is configured; "
                "production backup/restore is not established.",
            )
        ]
    bad = [r for r in rows if str(r.get("ErrMsg", "") or "").strip()]
    if bad:
        names = ", ".join(
            str(r.get("RepoName") or r.get("Name") or "unnamed") for r in bad
        )
        return [
            Finding(
                "backup",
                "Backup repository",
                "NEEDS_CHANGE",
                f"Repository error reported for: {names}.",
            )
        ]
    names = ", ".join(
        str(r.get("RepoName") or r.get("Name") or "unnamed") for r in rows
    )
    return [
        Finding(
            "backup",
            "Backup repository",
            "PASS",
            f"Configured repository/repositories: {names}.",
        ),
        Finding(
            "backup",
            "Restore exercise",
            "REVIEW",
            "A configured repository does not prove restore. Record a "
            "representative successful backup AND restore with RPO/RTO evidence.",
        ),
    ]


def assess_storage(data: AuditInput) -> list[Finding]:
    """BE disk headroom, with soft/hard thresholds.

    Conservative percentage-headroom audit, not a per-disk placement decision.
    """
    used: list[tuple[str, float]] = []
    for r in data.be_rows:
        if _truthy(r.get("SystemDecommissioned")) or _truthy(r.get("ClusterDecommissioned")):
            continue
        raw = r.get("MaxDiskUsedPct", r.get("UsedPct"))
        if raw is None:
            continue
        try:
            used.append((str(r.get("IP") or r.get("Host") or "?"), float(str(raw).rstrip("%"))))
        except (TypeError, ValueError):
            continue

    if not used:
        return [
            Finding(
                "storage",
                "BE disk utilization",
                "REVIEW",
                "No disk utilization reported by SHOW BACKENDS.",
            )
        ]

    worst_ip, worst = max(used, key=lambda x: x[1])
    detail = "; ".join(f"{ip}={pct:.1f}%" for ip, pct in used)
    if worst >= 95:
        status = "BLOCKED"
    elif worst >= 90:
        status = "NEEDS_CHANGE"
    else:
        status = "PASS"
    return [
        Finding(
            "storage",
            "BE disk utilization",
            status,
            f"Worst {worst_ip}={worst:.1f}% (soft=90%, hard=95%). {detail}.",
        )
    ]


def assess_security(data: AuditInput) -> list[Finding]:
    """The engine's ``root`` account must not accept an empty password.

    Nova itself never exposes ``root`` (Docker-internal only, AGENTS.md §5).
    This is a check on the underlying cluster, reported because a cluster that
    still accepts an empty root password is not production grade.
    """
    if data.root_password_rejected is None:
        return [
            Finding(
                "security",
                "Root password policy",
                "REVIEW",
                "Could not prove empty-password rejection for the root account.",
            )
        ]
    return [
        Finding(
            "security",
            "Root password policy",
            "PASS" if data.root_password_rejected else "BLOCKED",
            "root rejects an empty password."
            if data.root_password_rejected
            else "StarRocks root still accepts an empty password.",
        )
    ]


def external_gate_findings(metrics_ready: bool = True) -> list[Finding]:
    """Gates Nova cannot prove from inside the engine.

    Always emitted as ``REVIEW``. The lab's acceptance run makes the same
    distinction; omitting them would let an audit read as fully green while
    real production prerequisites are unverified.
    """
    prefix = (
        "StarRocks metrics are readable, but "
        if metrics_ready
        else "Metrics were not fully verified; "
    )
    return [
        Finding(
            "monitoring",
            "External alert delivery",
            "REVIEW",
            prefix
            + "collection, retention and alert routing to an on-call channel "
            "must be proven separately.",
        ),
        Finding(
            "infrastructure",
            "Failure domains / anti-affinity",
            "REVIEW",
            "Physical host, rack, datastore and power separation cannot be "
            "proven from inside the engine.",
        ),
        Finding(
            "network-security",
            "Firewall / VLAN / fabric policy",
            "REVIEW",
            "Upstream ACLs, VLANs, MTU and approved exposure cannot be "
            "certified by a SQL read.",
        ),
        Finding(
            "capacity",
            "Capacity / load test",
            "REVIEW",
            "Sizing must be proven with representative ingest and query "
            "concurrency, retention, compaction and growth tests.",
        ),
        Finding(
            "operations",
            "HA client endpoint",
            "REVIEW",
            "Client/load-balancer failover, reconnect/retry and removal of "
            "frontend single points of failure need an application-path test.",
        ),
        Finding(
            "operations",
            "Runbooks / change approval",
            "REVIEW",
            "Backup recovery, node replacement, incident handling, patching "
            "and rollback runbooks require operator/change-control approval.",
        ),
    ]


def summarize(findings: list[Finding]) -> dict[str, Any]:
    """Roll findings up into a single verdict.

    Precedence: any BLOCKED fails the audit; then NEEDS_CHANGE; then REVIEW
    keeps it pending; only an all-PASS set is a pass.
    """
    counts = {s: sum(1 for f in findings if f.status == s) for s in STATUSES}
    if counts["BLOCKED"]:
        result = "PRODUCTION_ACCEPTANCE_BLOCKED"
    elif counts["NEEDS_CHANGE"]:
        result = "PRODUCTION_ACCEPTANCE_NEEDS_CHANGE"
    elif counts["REVIEW"]:
        result = "PRODUCTION_ACCEPTANCE_PENDING"
    else:
        result = "PRODUCTION_ACCEPTANCE_PASSED"
    return {"counts": counts, "result": result}


def build_report(data: AuditInput) -> dict[str, Any]:
    """Full pure report: every finding plus the summary and metadata."""
    findings: list[Finding] = []
    findings += assess_membership(data)
    findings += assess_version_consistency(data)
    findings += assess_storage(data)
    findings += assess_backup(data)
    findings += assess_security(data)
    findings += external_gate_findings(metrics_ready=data.engine_reachable)

    summary = summarize(findings)
    return {
        "metadata": {
            "cluster_id": data.cluster_id,
            "version": data.version,
            "generated_at": datetime.now(UTC).isoformat(),
            "engine_reachable": data.engine_reachable,
        },
        "findings": [f.as_dict() for f in findings],
        **summary,
    }
