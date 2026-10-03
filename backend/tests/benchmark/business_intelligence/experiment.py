"""Paired Studio experiment with frozen evaluator inputs and resumable learning work."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from app.core.redis import session_store
from app.modules.assistant.repository import assistant_repository
from app.modules.intelligence.contracts import Scope
from app.modules.task_orchestration.internal_handlers import InternalTaskConfiguration, run_once
from tests.benchmark.business_intelligence.bootstrap import bootstrap
from tests.benchmark.business_intelligence.client import StudioClient
from tests.benchmark.business_intelligence.evaluate import evaluate
from tests.benchmark.business_intelligence.freeze import read_frozen_corpora, verify
from tests.benchmark.business_intelligence.gold.cases import Case
from tests.benchmark.business_intelligence.learning_corpus import RULES
from tests.benchmark.business_intelligence.model import teaching_changes
from tests.benchmark.business_intelligence.statistics import paired_improvement, summarize


def save(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    temporary.replace(path)


async def consolidate(client: StudioClient, resources: dict) -> list[dict]:
    """Use durable source-message work and the worker's bounded handler."""
    identity = await client.request("GET", "auth/me")
    session = await session_store.get(identity["session_id"])
    if not session:
        raise RuntimeError("Learning session expired")
    user = {**session, "session_id": identity["session_id"]}
    results = []
    for agent_id in resources["agents"].values():
        await client.request("POST", f"agents/{agent_id}/knowledge/consolidate")
        sources = await assistant_repository.pending_learning(
            user_name=identity["username"],
            agent_id=agent_id,
            limit=100,
        )
        for source in sources:
            config = InternalTaskConfiguration(
                scope=Scope.from_user(user),
                agent_id=agent_id,
                source_thread_id=source["thread_id"],
                source_message_id=source["message_id"],
            )
            result = await run_once("intelligence.consolidate", config, user)
            results.append({"source_message_id": source["message_id"], **result})
    return results


async def run_experiment(
    client: StudioClient,
    *,
    root: Path,
    frozen: Path,
    destination: Path,
    dataset: dict,
    provider: dict,
    execute_gold,
    repetitions: int,
    verify_data,
) -> dict:
    if repetitions < (3 if provider["mode"] == "live" else 1):
        raise ValueError("Live evidence requires at least three paired repetitions")
    if destination.exists():
        raise ValueError("Use a new output directory; evaluation runs cannot be overwritten")
    manifest = json.loads((frozen / "manifest.json").read_text())
    verify(root, manifest, await verify_data(), provider)
    corpora = read_frozen_corpora(frozen, manifest)
    destination.mkdir(parents=True)
    resources = await bootstrap(client, namespace="bi_" + uuid4().hex[:16])
    save(destination / "resources.json", resources)
    report = {
        "manifest_sha256": manifest["sha256"],
        "provider": provider,
        "repetitions": repetitions,
        "status": "running",
        "stages": {},
        "evidence_type": "scripted_pipeline" if provider["mode"] == "scripted" else "live_provider",
        "empirical_llm_improvement": None,
        "manual_review_required": True,
    }
    save(destination / "report.json", report)

    async def holdout(stage: str, split: str) -> list[dict]:
        verify(root, manifest, await verify_data(), provider)
        rows = []
        traces = []
        for repetition in range(repetitions):
            for value in corpora[split]:
                case = Case(**value)
                turn = await client.turn(
                    resources["agents"]["Executive"], case.question, learning=False
                )
                row = await evaluate(case, turn, execute_gold, repetition=repetition)
                rows.append(row)
                traces.append(
                    {
                        "case_id": case.id,
                        "repetition": repetition,
                        "thread_id": turn.thread_id,
                        "message_id": turn.message_id,
                        "content": turn.message.get("content"),
                        "steps": turn.message.get("steps", []),
                    }
                )
                save(destination / f"{stage}.json", rows)
                save(destination / f"{stage}-traces.json", traces)
        # Evaluation threads must not produce durable learning candidates.
        pending = await assistant_repository.pending_learning(
            user_name=resources["principal"], limit=100
        )
        forbidden = {row["thread_id"] for row in rows}
        if any(row["thread_id"] in forbidden for row in pending):
            raise RuntimeError("Evaluation isolation failed")
        report["stages"][stage] = summarize(rows)
        save(destination / "report.json", report)
        return rows

    try:
        before = await holdout("B0", "holdout_a")
        teaching = []
        published = False
        for interaction in corpora["learning"]:
            if interaction["episode"] > 4 and not published:
                await consolidate(client, resources)
                published_view = await client.review_changes(
                    resources["view_id"], resources["agents"]["Finance"], teaching_changes()
                )
                version = next(
                    row
                    for row in published_view["versions"]
                    if row["version"] == published_view["active_version"]
                )
                semantic = {
                    "view_id": resources["view_id"],
                    "version": version["version"],
                    "fingerprint": version["fingerprint"],
                }
                for agent_id in resources["agents"].values():
                    checked = await client.request(
                        "POST",
                        f"agents/{agent_id}/access/verify",
                        {"role_name": resources["active_role"]},
                    )
                    if not checked["all_granted"]:
                        raise RuntimeError(
                            "Published definitions failed current access verification"
                        )
                reviewed = []
                for rule in RULES:
                    if rule.metric is None:
                        continue
                    agent_id = resources["agents"][rule.domain]
                    memories = await client.request("GET", f"agents/{agent_id}/memories")
                    for memory in memories["memories"]:
                        if memory["fact_key"] != rule.key:
                            continue
                        review = await client.request(
                            "POST",
                            f"agents/{agent_id}/memories/{memory['memory_id']}/review",
                            {
                                "operation_id": str(uuid4()),
                                "expected_revision": memory["knowledge"]["revision"],
                                "operation": "verify",
                                "semantic": semantic,
                                "metric_name": rule.metric,
                                "publish_shared": True,
                                "note": (
                                    "Benchmark reviewer confirms the published teaching definition."
                                ),
                            },
                        )
                        reviewed.append(
                            {
                                "memory_id": memory["memory_id"],
                                "revision": review["revision"],
                                "state": review["state"],
                            }
                        )
                save(destination / "knowledge-reviews.json", reviewed)
                published = True
            agent_id = resources["agents"][interaction["domain"]]
            turn = await client.turn(agent_id, interaction["content"], learning=True)
            if turn.finish_reason != "stop":
                raise RuntimeError("Learning turn did not complete; pending work is preserved")
            feedback = await client.like(agent_id, turn) if interaction["like"] else None
            teaching.append(
                {
                    "id": interaction["id"],
                    "thread_id": turn.thread_id,
                    "message_id": turn.message_id,
                    "feedback": feedback,
                    "latency_ms": turn.latency_ms,
                }
            )
            save(destination / "learning.json", teaching)
        save(destination / "consolidation.json", await consolidate(client, resources))
        after = await holdout("B1", "holdout_a")
        await holdout("B2", "holdout_b")
        report["paired_learning_sensitive"] = paired_improvement(before, after)
        report["status"] = "awaiting_narrative_review"
        verify(root, manifest, await verify_data(), provider)
        save(destination / "report.json", report)
        lines = [
            "# Nova business intelligence experiment",
            "",
            f"Evidence: {report['evidence_type']}.",
            "",
            "Unreviewed narrative, causal-safety and authorization dimensions remain unavailable.",
            (
                "Scripted results establish pipeline behavior and do not establish "
                "empirical LLM improvement."
            ),
            "",
        ]
        for stage, summary in report["stages"].items():
            lines.extend(
                [
                    f"## {stage}",
                    "",
                    f"Cases: {summary['cases']}; repetitions: {repetitions}.",
                    "",
                    "```json",
                    json.dumps(summary, indent=2),
                    "```",
                    "",
                ]
            )
        (destination / "report.md").write_text("\n".join(lines))
        return report
    except BaseException as exc:
        report.update(status="interrupted", failure_type=type(exc).__name__)
        save(destination / "report.json", report)
        raise
