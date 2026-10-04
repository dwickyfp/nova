"""Frozen release diagnosis, safe semantic explanations, and reviewed draft recovery."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.agents import doctor, quality, quality_application
from app.modules.agents.semantic.ir import SemanticModelIR
from app.modules.agents.versions import configuration
from app.modules.intelligence.contracts import fingerprint
from app.modules.intelligence.semantic_views import semantic_view_service
from tests.unit.test_agent_release_quality import USER
from tests.unit.test_quality_lab_boundaries import metadata as metadata

AGENT = {
    "agent_id": "agent",
    "owner_name": "alice",
    "name": "Sales",
    "config_revision": "current-base",
    "instructions_response": "Use governed evidence.",
    "instructions_orchestration": "",
    "semantic_view_ids": [],
    "default_tools": [],
}
CASE = {
    "id": "case",
    "revision": 1,
    "name": "Complete task",
    "prompt": "What is revenue?",
    "mandatory": True,
    "critical": True,
    "source": "manual",
    "production_match": "exact_prompt",
    "assertions": [
        {"scorer": "task_completeness", "required": True, "expected": {"completed": True}}
    ],
}


def run(identifier, status="pass", *, version=None, date="2026-10-01T00:00:00+00:00"):
    return {
        "id": identifier,
        "agent_id": "agent",
        "revision": 2,
        "version_id": version or identifier,
        "manifest_id": f"manifest-{identifier}",
        "manifest_fingerprint": "digest",
        "status": "passed" if status == "pass" else "failed",
        "source": "evaluation",
        "promotion_eligible": status == "pass",
        "scorer_set_version": "nova-agent-scorers:1",
        "cases": [deepcopy(CASE)],
        "case_fingerprint": fingerprint([CASE]),
        "gates": {},
        "created_at": date,
        "results": [
            {
                "case_id": "case",
                "case_revision": 1,
                "status": "passed" if status == "pass" else "failed",
                "trace_id": f"trace-{identifier}",
                "duration_ms": 10 if status == "pass" else 15,
                "trace": {"counts": {"provider_calls": 2 if status == "pass" else 4}},
                "scores": [
                    {
                        "scorer": "task_completeness",
                        "scorer_version": "1",
                        "required": True,
                        "status": status,
                        "detail": "Recorded task evidence",
                        "failure_taxonomy": None if status == "pass" else "INCOMPLETE_ANSWER",
                    }
                ],
            }
        ],
    }


def test_history_identifies_first_bad_evaluated_release_and_measures_regression():
    good = run("good")
    bad = run("bad", "fail", date="2026-10-02T00:00:00+00:00")
    current = run("current", "unavailable", date="2026-10-03T00:00:00+00:00")
    result = doctor.diagnose_history([current, good, bad], current_version_id="current")
    assert result["known_good"]["version_id"] == "good"
    assert result["first_bad"]["version_id"] == "bad"
    assert result["current"]["version_id"] == "current"
    finding = result["regressions"][0]
    assert finding["after"] == "unavailable" and not finding["hypothesis"]
    assert finding["before_trace_id"] == "trace-good"
    assert finding["after_trace_id"] == "trace-current"
    assert {item["name"]: item["delta"] for item in finding["measurements"]} == {
        "duration_ms": 5,
        "provider_calls": 2,
    }


@pytest.mark.parametrize(
    "change",
    [
        "case_revision",
        "scorer_version",
        "gate",
        "dataset",
        "corrupt_digest",
        "missing_scores",
        "production",
        "missing_dataset",
    ],
)
def test_incompatible_evidence_never_establishes_known_good_baseline(change):
    left, right = run("left"), run("right", "fail", date="2026-10-02T00:00:00+00:00")
    if change == "case_revision":
        right["cases"][0]["revision"] = right["results"][0]["case_revision"] = 2
        right["case_fingerprint"] = fingerprint(right["cases"])
    elif change == "scorer_version":
        right["results"][0]["scores"][0]["scorer_version"] = "2"
    elif change == "gate":
        right["gates"] = {"other_cases": "all"}
    elif change == "dataset":
        right["cases"][0]["prompt"] = "A different objective"
        right["case_fingerprint"] = fingerprint(right["cases"])
    elif change == "corrupt_digest":
        right["case_fingerprint"] = "corrupt"
    elif change == "missing_scores":
        right["results"][0]["scores"] = []
    elif change == "production":
        right["source"] = "production"
    else:
        left["cases"] = right["cases"] = []
    assert not doctor.compatible(left, right)
    assert doctor.regressions(left, right) == []
    assert doctor.diagnose_history([left, right])["known_good"] is None


def test_first_bad_is_bounded_by_selected_current_release():
    good = run("good")
    current = run("current", date="2026-10-02T00:00:00+00:00")
    future = run("future", "fail", date="2026-10-03T00:00:00+00:00")
    result = doctor.diagnose_history([future, good, current], current_version_id="current")
    assert result["first_bad"] is None
    assert (
        doctor.diagnose_history([good], current_version_id="unevaluated")["requirements"][0]["code"]
        == "CURRENT_RELEASE_UNEVALUATED"
    )


def test_selected_evaluation_is_not_replaced_by_later_run_of_same_release():
    good = run("good")
    selected = run("selected", "fail", version="candidate", date="2026-10-02T00:00:00+00:00")
    later = run("later", version="candidate", date="2026-10-03T00:00:00+00:00")
    report = doctor.diagnose_history(
        [good, selected, later], current_version_id="candidate", current_run_id="selected"
    )
    assert report["current"]["id"] == "selected"
    assert report["regressions"][0]["after"] == "fail"


def test_duplicate_scores_and_absent_scorer_versions_are_not_compatible():
    duplicate = run("duplicate")
    duplicate["results"][0]["scores"].append(deepcopy(duplicate["results"][0]["scores"][0]))
    assert doctor.evaluation_identity(duplicate) is None
    missing = run("missing")
    missing["results"][0]["scores"][0].pop("scorer_version")
    assert doctor.evaluation_identity(missing) is None


def test_configured_performance_gates_compare_recorded_counts():
    left, right = run("left"), run("right", "fail")
    for item, status, count in [(left, "pass", 2), (right, "fail", 4)]:
        item["gates"] = {"performance": "required", "count_budgets": {"provider_calls": 3}}
        item["results"][0]["budget_scores"] = [
            {
                "scorer": "efficiency",
                "scorer_version": "1",
                "status": status,
                "failure_taxonomy": "INEFFICIENT_EXECUTION" if status == "fail" else None,
            }
        ]
        item["results"][0]["trace"]["counts"]["provider_calls"] = count
    regressions = doctor.regressions(left, right)
    assert {item["scorer"] for item in regressions} == {"task_completeness", "efficiency"}


def manifests(monkeypatch, old_run, current_run, *, semantics=None):
    before_config, after_config = configuration(AGENT), configuration(AGENT)
    before_config["instructions_response"] = "Known good configured instructions"
    entries = {}
    for record, config, refs in [
        (old_run, before_config, (semantics or [])[:1]),
        (current_run, after_config, (semantics or [])[1:]),
    ]:
        dependencies = {
            "configuration": config,
            "compiled_prompt": "PRIVATE COMPILED PROMPT",
            "semantic_views": refs,
        }
        manifest = {
            "id": record["manifest_id"],
            "version_id": record["version_id"],
            "dependencies": dependencies,
            "fingerprint": fingerprint(dependencies),
        }
        record["manifest_fingerprint"] = manifest["fingerprint"]
        entries[manifest["id"]] = manifest
    monkeypatch.setattr(
        doctor,
        "get_manifest",
        AsyncMock(side_effect=lambda _agent, _owner, manifest_id: entries[manifest_id]),
    )


@pytest.mark.asyncio
async def test_report_resolves_active_manifest_and_keeps_private_prompt_out(monkeypatch):
    good, current = run("good"), run("current", "fail", date="2026-10-02T00:00:00+00:00")
    manifests(monkeypatch, good, current)
    result = await doctor.report(
        {**AGENT, "release_manifest_id": current["manifest_id"]}, USER, [good, current]
    )
    assert result["current"]["version_id"] == "current"
    assert result["patches"][0]["kind"] == "restore_configuration"
    assert result["patches"][0]["fields"] == ["instructions_response"]
    assert result["regression_candidates"][0]["case_revision"] == 1
    assert "PRIVATE COMPILED PROMPT" not in str(result)
    assert CASE["prompt"] not in str(result)
    assert "Known good configured instructions" not in str(result)


@pytest.mark.asyncio
async def test_semantic_alias_and_definition_changes_are_authorized_hypotheses(monkeypatch):
    good, current = run("good"), run("current", "fail", date="2026-10-02T00:00:00+00:00")
    before = {
        "name": "Sales",
        "version": "1",
        "datasets": [],
        "metrics": [
            {
                "name": "revenue",
                "expression": "SUM(sales.revenue)",
                "base_dataset": "sales",
                "synonyms": ["sales"],
            },
            {
                "name": "margin",
                "expression": "SUM(sales.margin)",
                "base_dataset": "sales",
                "synonyms": ["profit"],
            },
        ],
    }
    after = deepcopy(before)
    after["metrics"][1]["synonyms"] = ["SALES"]
    after["metrics"][1]["expression"] = "SUM(sales.changed_margin)"
    refs = [
        {
            "view_id": "sales",
            "version": number,
            "fingerprint": SemanticModelIR.from_ossie(definition).fingerprint,
        }
        for number, definition in [(1, before), (2, after)]
    ]
    manifests(monkeypatch, good, current, semantics=refs)
    authorized = AsyncMock(
        side_effect=lambda _id, number, _fingerprint, user, agent_id: {
            "owner_name": "alice",
            "definition": before if number == 1 else after,
        }
    )
    monkeypatch.setattr(semantic_view_service, "get_version_for_agent", authorized)
    result = await doctor.report(AGENT, USER, [good, current], current_run=current)
    assert {item["category"] for item in result["findings"]} == {
        "SEMANTIC_ALIAS_COLLISION",
        "SEMANTIC_DEFINITION_CHANGE",
    }
    assert all(item["hypothesis"] for item in result["findings"])
    assert all(call.kwargs["agent_id"] == "agent" for call in authorized.await_args_list)
    assert all(call.args[3] is USER for call in authorized.await_args_list)
    semantic_patch = next(item for item in result["patches"] if item["kind"] == "semantic_changes")
    assert semantic_patch["changes"] == [
        {"kind": "synonyms", "name": "revenue", "synonyms": []},
        {"kind": "synonyms", "name": "margin", "synonyms": []},
    ]
    assert "SUM(sales.changed_margin)" not in str(result)
    authorized.side_effect = HTTPException(404, "private semantic detail")
    denied = await doctor.report(AGENT, USER, [good, current], current_run=current)
    assert denied["findings"] == []
    assert any(item["code"] == "SEMANTIC_DEPENDENCY_UNAVAILABLE" for item in denied["requirements"])
    assert "private semantic detail" not in str(denied)


@pytest.fixture
def drafts(monkeypatch):
    from app.modules.agents import resources

    versions = {}
    monkeypatch.setattr(
        quality_application.agent_repository, "get_agent", AsyncMock(return_value=deepcopy(AGENT))
    )
    monkeypatch.setattr(quality_application, "validate_publication_dependencies", AsyncMock())
    monkeypatch.setattr(resources, "validate_resources", AsyncMock())

    async def get(_agent, _owner, version_id):
        return deepcopy(versions.get(version_id))

    async def store(candidate, *, label, version_id):
        snapshot = configuration(candidate)
        if version_id in versions:
            assert versions[version_id]["configuration"] == snapshot
        else:
            versions[version_id] = {
                "version_id": version_id,
                "configuration": snapshot,
                "label": label,
            }
        return deepcopy(versions[version_id])

    monkeypatch.setattr(quality_application.agent_versions, "get", AsyncMock(side_effect=get))
    monkeypatch.setattr(quality_application.agent_versions, "store", AsyncMock(side_effect=store))
    return versions


async def proposal():
    patch = doctor.instruction_patch(AGENT, ["INCOMPLETE_ANSWER"])
    return await quality.save_record(
        "proposals",
        {
            "id": "proposal",
            "agent_id": "agent",
            "status": "proposed",
            "patches": [patch.model_dump(mode="json", exclude_none=True)],
        },
        USER,
    )


@pytest.mark.asyncio
async def test_review_creates_stable_draft_and_retries_recover_same_reference(metadata, drafts):
    record = await proposal()
    request = quality.ProposalReview(expected_revision=record["revision"], resolution="accepted")
    left, right = await asyncio.gather(
        *[quality_application.review_and_apply(AGENT, "proposal", request, USER) for _ in range(2)]
    )
    assert left == right
    assert left["application"]["status"] == "applied"
    assert len(drafts) == 1
    draft = drafts[left["application"]["version_id"]]
    assert "Complete the requested task" in draft["configuration"]["instructions_response"]
    assert draft["configuration"]["policy"] == "auto_read_only"
    assert quality_application.agent_repository.get_agent.return_value == AGENT
    with pytest.raises(HTTPException, match="different inputs"):
        await quality_application.review_and_apply(
            AGENT, "proposal", request.model_copy(update={"resolution": "rejected"}), USER
        )


@pytest.mark.asyncio
async def test_crash_after_draft_write_recovers_without_duplicate(metadata, drafts, monkeypatch):
    record = await proposal()
    request = quality.ProposalReview(expected_revision=record["revision"], resolution="accepted")
    original = quality._save_record_locked
    fail = True

    async def save(kind, record, user, revision, lock):
        nonlocal fail
        if fail and (record.get("application") or {}).get("status") == "applied":
            fail = False
            raise HTTPException(503, "Metadata write interrupted")
        return await original(kind, record, user, revision, lock)

    monkeypatch.setattr(quality, "_save_record_locked", save)
    with pytest.raises(HTTPException, match="interrupted"):
        await quality_application.review_and_apply(AGENT, "proposal", request, USER)
    assert len(drafts) == 1
    pending = (await quality.records("proposals", "agent", USER, "proposal"))[0]
    assert pending["application"]["status"] == "pending"
    recovered = await quality_application.review_and_apply(AGENT, "proposal", request, USER)
    assert recovered["application"]["status"] == "applied" and len(drafts) == 1


@pytest.mark.asyncio
async def test_stale_base_and_foreign_scope_refuse_before_creating_draft(metadata, drafts):
    record = await proposal()
    request = quality.ProposalReview(expected_revision=record["revision"], resolution="accepted")
    quality_application.agent_repository.get_agent.return_value = {
        **AGENT,
        "config_revision": "new-base",
    }
    with pytest.raises(HTTPException, match="base changed"):
        await quality_application.review_and_apply(AGENT, "proposal", request, USER)
    assert drafts == {}
    assert (await quality.records("proposals", "agent", USER))[0]["status"] == "proposed"
    with pytest.raises(HTTPException, match="not found"):
        await quality_application.review_and_apply(
            AGENT, "proposal", request, {**USER, "active_role": "ADMIN"}
        )


@pytest.mark.asyncio
async def test_restore_patch_uses_exact_owned_version_and_preserves_other_fields(metadata, drafts):
    source = configuration(AGENT)
    source["instructions_response"] = "Reviewed source evidence instructions"
    source["policy"] = "ask_every_tool"
    drafts["good-source"] = {"version_id": "good-source", "configuration": source}
    patch = doctor.RemediationPatch(
        id="restore",
        kind="restore_configuration",
        description="Restore response requirements",
        base_revision="current-base",
        source_version_id="good-source",
        source_configuration_fingerprint=fingerprint(source),
        fields=["instructions_response"],
    )
    record = await quality.save_record(
        "proposals",
        {
            "id": "restore",
            "agent_id": "agent",
            "status": "proposed",
            "patches": [patch.model_dump(mode="json", exclude_none=True)],
        },
        USER,
    )
    applied = await quality_application.review_and_apply(
        AGENT,
        "restore",
        quality.ProposalReview(expected_revision=record["revision"], resolution="accepted"),
        USER,
    )
    draft = drafts[applied["application"]["version_id"]]["configuration"]
    assert draft["instructions_response"] == source["instructions_response"]
    assert draft["policy"] == "auto_read_only"
    assert applied["application"]["base_revision"] == "current-base"


@pytest.mark.asyncio
async def test_restore_patch_refuses_changed_source_and_current_resources(drafts, monkeypatch):
    from app.modules.agents import resources

    patch = doctor.RemediationPatch(
        id="restore",
        kind="restore_configuration",
        description="Restore response requirements",
        base_revision="current-base",
        source_version_id="good-source",
        source_configuration_fingerprint="wrong-fingerprint",
        fields=["instructions_response"],
    )
    drafts["good-source"] = {"configuration": configuration(AGENT), "version_id": "good-source"}
    with pytest.raises(HTTPException, match="source is unavailable or changed"):
        await quality_application.draft_for_patch(AGENT, patch, "new-draft", USER)
    monkeypatch.setattr(
        resources,
        "validate_resources",
        AsyncMock(side_effect=HTTPException(404, "Resource unavailable")),
    )
    instruction = doctor.instruction_patch(AGENT, ["INCOMPLETE_ANSWER"])
    with pytest.raises(HTTPException, match="Resource unavailable"):
        await quality_application.draft_for_patch(AGENT, instruction, "new-draft", USER)
    assert "new-draft" not in drafts


@pytest.mark.asyncio
async def test_reviewed_regression_candidate_becomes_pinned_case_once(metadata, drafts):
    source = await quality.save_record("cases", {**deepcopy(CASE), "agent_id": "agent"}, USER)
    frozen_run = run("frozen", "fail")
    frozen_run["cases"] = [source]
    frozen_run["case_fingerprint"] = fingerprint([source])
    frozen_run["revision"] = 0
    frozen = await quality.save_record("runs", frozen_run, USER)
    candidates = doctor.regression_candidates(frozen)
    base = await proposal()
    record = await quality.save_record(
        "proposals", {**base, "regression_candidates": candidates}, USER, base["revision"]
    )
    request = quality.ProposalReview(
        expected_revision=record["revision"],
        resolution="accepted",
        regression_case_ids=[candidates[0]["id"]],
    )
    applied = await quality_application.review_and_apply(AGENT, "proposal", request, USER)
    replay = await quality_application.review_and_apply(AGENT, "proposal", request, USER)
    assert replay == applied
    cases = await quality.records("cases", "agent", USER)
    new = next(item for item in cases if item["source"] == "regression")
    assert new["assertions"] == source["assertions"]
    assert new["source_case"]["case_revision"] == source["revision"]
    assert len(cases) == 2


@pytest.mark.asyncio
async def test_semantic_application_reuses_existing_owner_proposal_after_crash(
    metadata, drafts, monkeypatch
):
    from app.modules.intelligence import autopilot
    from tests.unit.test_usage_autopilot_control import setup as proposal_setup

    body, _, published, targets = proposal_setup(monkeypatch)

    patch = doctor.RemediationPatch(
        id="semantic-patch",
        kind="semantic_changes",
        description="Remove ambiguous alias",
        base_revision="current-base",
        semantic=body.base,
        changes=[change.model_dump(mode="json") for change in body.changes],
    )
    record = await quality.save_record(
        "proposals",
        {
            "id": "semantic",
            "agent_id": "agent",
            "status": "proposed",
            "patches": [patch.model_dump(mode="json", exclude_none=True)],
        },
        USER,
    )
    request = quality.ProposalReview(expected_revision=record["revision"], resolution="accepted")
    authorized = AsyncMock(return_value={})
    monkeypatch.setattr(semantic_view_service, "get_version_for_agent", authorized)
    original = quality._save_record_locked
    interrupted = True

    async def save(kind, record, user, revision, lock):
        nonlocal interrupted
        if interrupted and (record.get("application") or {}).get("status") == "applied":
            interrupted = False
            raise HTTPException(503, "Linkage interrupted")
        return await original(kind, record, user, revision, lock)

    monkeypatch.setattr(quality, "_save_record_locked", save)
    with pytest.raises(HTTPException, match="interrupted"):
        await quality_application.review_and_apply(AGENT, "semantic", request, USER)
    pending = (await quality.records("proposals", "agent", USER, "semantic"))[0]
    operation_id = pending["application"]["operation_id"]
    changed = patch.model_copy(update={
        "changes": [{"kind": "synonyms", "name": "order_count", "synonyms": ["changed"]}],
    })
    with pytest.raises(HTTPException, match="Operation inputs changed"):
        await quality_application.semantic_for_patch(AGENT, changed, operation_id, USER)
    semantic_view_service._source_access.return_value = False
    with pytest.raises(HTTPException, match="source is unavailable"):
        await quality_application.review_and_apply(AGENT, "semantic", request, USER)
    semantic_view_service._source_access.return_value = True
    recovered = await quality_application.review_and_apply(AGENT, "semantic", request, USER)
    assert recovered["application"]["kind"] == "semantic_proposal"
    assert list(targets) == [recovered["application"]["proposal_id"]]
    autopilot.rule_proposal_repository.create.assert_awaited_once()
    for call in autopilot.rule_proposal_repository.get.await_args_list:
        assert call.kwargs == {"owner_name": "alice", "role_name": "ANALYST"}
    authorized.assert_awaited_with("sales", 2, body.base.fingerprint, USER, agent_id="agent")
    published.assert_not_awaited()
    assert drafts == {}


@pytest.mark.asyncio
async def test_semantic_pending_application_refuses_stale_version_and_revoked_access(
    metadata, drafts, monkeypatch
):
    from app.modules.intelligence import autopilot

    patch = doctor.RemediationPatch(
        id="semantic-patch",
        kind="semantic_changes",
        description="Remove ambiguous alias",
        base_revision="current-base",
        semantic={"view_id": "sales", "version": 2, "fingerprint": "semantic-fp"},
        changes=[{"kind": "synonyms", "name": "revenue", "synonyms": []}],
    )
    record = await quality.save_record(
        "proposals",
        {
            "id": "semantic",
            "agent_id": "agent",
            "status": "proposed",
            "patches": [patch.model_dump(mode="json", exclude_none=True)],
        },
        USER,
    )
    request = quality.ProposalReview(expected_revision=record["revision"], resolution="accepted")
    owner = AsyncMock(return_value={"active_version": 3})
    monkeypatch.setattr(semantic_view_service, "_owned", owner)
    monkeypatch.setattr(autopilot, "propose", AsyncMock())
    with pytest.raises(HTTPException, match="base changed"):
        await quality_application.review_and_apply(AGENT, "semantic", request, USER)
    owner.return_value = {"active_version": 2}
    monkeypatch.setattr(
        semantic_view_service,
        "get_version_for_agent",
        AsyncMock(side_effect=HTTPException(404, "Semantic View unavailable")),
    )
    with pytest.raises(HTTPException, match="unavailable"):
        await quality_application.review_and_apply(AGENT, "semantic", request, USER)
    autopilot.propose.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_legacy_feedback_is_enriched_once_before_review(metadata, monkeypatch):
    trace = run("run", "fail")
    legacy = await quality.feedback_proposal("agent", "message", USER, trace)
    assert "patches" not in legacy
    diagnosis = {
        "patches": [
            doctor.instruction_patch(AGENT, ["INCOMPLETE_ANSWER"]).model_dump(
                mode="json", exclude_none=True
            )
        ],
        "regression_candidates": [],
    }
    report = AsyncMock(return_value=diagnosis)
    monkeypatch.setattr(doctor, "report", report)
    enriched = await quality.feedback_proposal("agent", "message", USER, trace, agent=AGENT)
    replay = await quality.feedback_proposal("agent", "message", USER, trace, agent=AGENT)
    assert enriched == replay and enriched["revision"] == legacy["revision"] + 1
    report.assert_awaited_once()
