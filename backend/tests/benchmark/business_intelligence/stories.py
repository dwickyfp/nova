"""Export completed development stories without session or provider execution state."""

import json
from pathlib import Path

from app.modules.assistant.skills import contains_credential_shape


def public_records(value):
    if isinstance(value, dict):
        return {
            key: public_records(item)
            for key, item in value.items()
            if key not in {"scope", "session_id", "security_context", "reasoning_content"}
        }
    if isinstance(value, list):
        return [public_records(item) for item in value]
    return value


def write_report(destination: Path, stories: list[dict], *, observation_manifest: dict) -> None:
    if {story["title"] for story in stories} != {
        "Jakarta stockout recovery",
        "Payment-service degradation",
    } or len(stories) != 2:
        raise ValueError("Both complete development stories are required")
    for story in stories:
        cross_domain = {
            row["label"]: row
            for row in story["investigation"]["timeline"]
            if row["kind"] == "related_observation"
        }
        driver = (
            "Inventory availability"
            if story["title"] == "Jakarta stockout recovery"
            else "Payment-service latency"
        )
        independent = story.get("independent_cross_domain", {})
        if set(independent) != {driver, "Marketing spend"} or not all(
            label in cross_domain
            and cross_domain[label]["causal_status"] == "association"
            and all(cross_domain[label][period] == values[period] for period in ("before", "after"))
            for label, values in independent.items()
        ):
            raise ValueError("Story cross-domain evidence lacks independent validation")
        marketing = independent["Marketing spend"]
        actual_driver = independent[driver]
        if (
            marketing["before"] != marketing["after"]
            or actual_driver["before"] <= 0
            or (
                actual_driver["after"] != 0
                if driver == "Inventory availability"
                else actual_driver["after"] <= 10 * actual_driver["before"]
            )
        ):
            raise ValueError("Story cross-domain observations do not establish the test scenario")
        if (
            story["outcome"]["status"] != "complete"
            or story["outcome"]["actual"] != story["independent_actual"]
            or not story["retry_retained_outcome_revision"]
            or [event["event"] for event in story["events"]] != ["created", "selected", "approved"]
            or not story["evidence"]
            or not all(item["reconciled"] for item in story["investigation"]["decompositions"])
            or any(
                item["causal_status"] not in {"association", "arithmetic"}
                for item in story["investigation"]["hypotheses"]
            )
            or story["knowledge"]["state"] != "INFERRED"
            or story["knowledge"]["visibility"] != "PRIVATE"
            or len(story["decision"]["options"]) < 2
            or not story.get("studio_followup", {}).get("inferred_context_verified")
            or story["studio_followup"]["knowledge_revision"] != story["knowledge"]["revision"]
        ):
            raise ValueError("Story lineage, numerical validation or learning is incomplete")
    report = {
        "evidence_type": "development_real_engine",
        "observation_dataset_unchanged": True,
        "observation_manifest": observation_manifest,
        "synthetic_future_training_separate": True,
        "external_business_actions_executed": False,
        "empirical_llm_improvement": None,
        "stories": public_records(stories),
    }
    encoded = json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n"
    if contains_credential_shape(encoded):
        raise ValueError("Story report contains credential-shaped content")
    if destination.exists():
        raise ValueError("Story evidence cannot overwrite an earlier report")
    lines = [
        "# Development business stories",
        "",
        "These stories use real Nova APIs and a separate synthetic future-training warehouse. "
        "The held-out observation dataset remained unchanged. Inventory transfers and rollbacks "
        "were not executed. Observed changes do not establish attributable effects.",
        "",
    ]
    for story in report["stories"]:
        news, decision, outcome = story["news"], story["decision"], story["outcome"]
        driver = (
            "Inventory availability"
            if story["title"] == "Jakarta stockout recovery"
            else "Payment-service latency"
        )
        actual_driver = story["independent_cross_domain"][driver]
        marketing = story["independent_cross_domain"]["Marketing spend"]
        lines.extend(
            [
                f"## {story['title']}",
                "",
                f"The monitor recorded {news['before']} before and {news['after']} after, "
                f"a change of {news['change']} {decision['currency']}. "
                "Dimensional contributions reconciled exactly. "
                "Timeline events remained associations.",
                "",
                f"Independent SQL confirmed {driver}: "
                f"{actual_driver['before']} before and {actual_driver['after']} after. "
                f"Marketing spend stayed at {marketing['after']}. "
                "These aligned observations support a driver hypothesis; "
                "they do not establish a causal effect.",
                "",
                f"Decision `{decision['id']}` revision {decision['revision']} "
                "retained its options, "
                "assumptions, numerical method and approval events. "
                f"Its selected option is `{decision['selected_option_id']}`.",
                "",
                f"Outcome `{outcome['id']}` progressed through pending, missing data and complete. "
                f"The observed value was {outcome['actual']} {decision['currency']}, "
                "matching independent "
                "SQL. Repeated evaluation retained the outcome revision. "
                "The outcome produced a private inferred learning candidate and left verified "
                "semantic definitions unchanged.",
                "",
                "A later Studio turn received the same private inferred knowledge revision. "
                "Its scripted provider distinguished observation from causal attribution. "
                "This verifies the context path, not live model performance.",
                "",
                "The companion JSON records the hypotheses, options, intervals, assumptions, "
                "method versions, evidence references, lineage events "
                "and separate outcome dimensions.",
                "",
            ]
        )
    destination.mkdir(parents=True)
    (destination / "stories.json").write_text(encoded)
    (destination / "stories.md").write_text("\n".join(lines))
