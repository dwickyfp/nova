from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from .contracts import Outcome


def write_reports(
    directory: Path, outcomes: list[Outcome], inventory: dict, metadata: dict
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    counts = dict(Counter(item.status for item in outcomes))
    passed = [item for item in outcomes if item.status == "PASS"]
    covered = {key for item in passed for key in item.signatures}
    positive = {
        key for item in passed if item.verification_kind == "positive" for key in item.signatures
    }
    refused = {
        key for item in passed if item.verification_kind == "refusal" for key in item.signatures
    }
    covered_rules = {key for item in passed for key in item.statement_rules}
    covered_logical = {key for item in passed for key in item.logical_overloads}
    exclusions = {
        name: {item["key"] for item in inventory.get(name, []) if item.get("exclusion")}
        for name in (
            "function_signatures", "scalar_logical_overloads", "engine_logical_overloads"
        )
    }
    covered -= exclusions["function_signatures"]
    positive -= exclusions["function_signatures"]
    refused -= exclusions["function_signatures"]
    summary = {
        "status": "FAIL"
        if counts.get("FAIL")
        else "BLOCKED"
        if counts.get("BLOCKED") or not outcomes
        else "PASS",
        "counts": counts,
        "function_signatures_tested": len(covered),
        "function_signatures_positive": len(positive),
        "function_signatures_refusal": len(refused),
        "function_signatures_total": len(inventory.get("function_signatures", [])),
        "scalar_logical_overloads_tested": len(
            covered_logical
            & {item["key"] for item in inventory.get("scalar_logical_overloads", [])}
            - exclusions["scalar_logical_overloads"]
        ),
        "scalar_logical_overloads_total": len(inventory.get("scalar_logical_overloads", [])),
        "engine_logical_overloads_tested": len(
            covered_logical
            & {item["key"] for item in inventory.get("engine_logical_overloads", [])}
            - exclusions["engine_logical_overloads"]
        ),
        "engine_logical_overloads_positive": len(
            {
                key
                for item in passed
                if item.verification_kind == "positive"
                for key in item.logical_overloads
            }
            & {item["key"] for item in inventory.get("engine_logical_overloads", [])}
            - exclusions["engine_logical_overloads"]
        ),
        "engine_logical_overloads_total": len(inventory.get("engine_logical_overloads", [])),
        "statement_rules_tested": len(covered_rules),
        "statement_rules_total": len(inventory.get("statement_rules", [])),
        **metadata,
    }
    for name, keys in exclusions.items():
        summary[name + "_excluded"] = len(keys)
        summary[name + "_required"] = len(inventory.get(name, [])) - len(keys)
    report = {"summary": summary, "outcomes": [asdict(item) for item in outcomes]}
    (directory / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    )
    (directory / "inventory.json").write_text(json.dumps(inventory, indent=2))
    root = ET.Element(
        "testsuite",
        name="nova-functional-sql",
        tests=str(len(outcomes)),
        failures=str(counts.get("FAIL", 0)),
        errors=str(counts.get("BLOCKED", 0)),
        skipped="0",
    )
    for item in outcomes:
        case = ET.SubElement(
            root, "testcase", name=item.id, classname=item.group, time=str(item.duration_ms / 1000)
        )
        if item.status != "PASS":
            ET.SubElement(
                case, "error" if item.status == "BLOCKED" else "failure", message=item.detail
            ).text = item.sql
    ET.ElementTree(root).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
    rows = "".join(
        f"<tr><td>{html.escape(item.id)}</td><td>{item.status}</td>"
        f"<td><pre>{html.escape(item.sql)}</pre>{html.escape(item.detail)}</td></tr>"
        for item in outcomes
    )
    (directory / "report.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Nova SQL results</title>'
        "<style>body{font:16px system-ui;margin:2rem}td{padding:.5rem;border-bottom:1px solid #ccc}"
        "pre{white-space:pre-wrap}table{border-collapse:collapse;width:100%}</style>"
        f"<h1>Nova SQL: {summary['status']}</h1>"
        f"<pre>{html.escape(json.dumps(summary, indent=2))}</pre>"
        f"<table><tr><th>Case</th><th>Result</th><th>Evidence</th></tr>{rows}</table></html>"
    )
    return summary
