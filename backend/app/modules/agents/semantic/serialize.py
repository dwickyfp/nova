"""Build Ossie YAML from a flat, model-friendly structure.

An LLM reliably produces a *flat* description of a semantic model (datasets with
column names, metrics, joins) but not a correctly nested Ossie document with
``expression.dialects`` arrays. Rather than teach the model the exact nesting or
repair its YAML, this module owns the serialization: the model returns simple
JSON, and Nova writes the Ossie document deterministically.

This is the same contract as the frontend builder's serializer
(``frontend/src/features/agents/semantic-draft.ts``); both produce version 0.1.1
documents the backend parser accepts. The authoritative gate remains
``semantic.ossie.parse_ossie``.
"""

from __future__ import annotations

from typing import Any

OSSIE_VERSION = "0.1.1"


def _scalar(value: Any) -> str:
    """A YAML scalar, single-quoted unless it is unambiguously plain."""
    text = "" if value is None else str(value)
    if text == "":
        return "''"
    safe = all(ch.isalnum() or ch in "_./- " for ch in text)
    reserved = text.lower() in {"true", "false", "null", "yes", "no", "on", "off", "~"}
    numeric = text.lstrip("-").replace(".", "", 1).isdigit()
    if safe and not reserved and not numeric and text[0].isalnum():
        return text
    return "'" + text.replace("'", "''") + "'"


def _lines(indent: str, key: str, value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return f"{indent}{key}: {_scalar(value)}"


def build_ossie_yaml(spec: dict[str, Any]) -> str:
    """Serialize a flat spec dict to an Ossie 0.1.1 YAML document.

    Missing or malformed sections are emitted empty rather than raising; the
    parser is the validator, and it reports a precise error if something the
    model produced is unusable.
    """
    out: list[str] = [f"version: {OSSIE_VERSION}"]
    out.append(f"name: {_scalar(spec.get('name') or 'untitled_model')}")
    desc = _lines("", "description", spec.get("description"))
    if desc:
        out.append(desc)

    datasets = spec.get("datasets") or []
    out.append("datasets:")
    if not datasets:
        out.append("  []")
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        out.append(f"  - name: {_scalar(dataset.get('name'))}")
        out.append(f"    source: {_scalar(dataset.get('source'))}")
        pk = dataset.get("primary_key")
        if pk:
            out.append(f"    primary_key: [{pk}]")
        ddesc = _lines("    ", "description", dataset.get("description"))
        if ddesc:
            out.append(ddesc)
        fields = dataset.get("fields") or []
        if fields:
            out.append("    fields:")
            for field in fields:
                if not isinstance(field, dict) or not field.get("name"):
                    continue
                out.append(f"      - name: {_scalar(field.get('name'))}")
                out.append("        expression:")
                out.append("          dialects:")
                out.append("            - dialect: ANSI_SQL")
                out.append(
                    f"              expression: {_scalar(field.get('name'))}"
                )
                if field.get("datatype"):
                    out.append(f"        datatype: {_scalar(field.get('datatype'))}")
                if field.get("is_time"):
                    out.append("        dimension:")
                    out.append("          is_time: true")
                fdesc = _lines("        ", "description", field.get("description"))
                if fdesc:
                    out.append(fdesc)

    relationships = spec.get("relationships") or []
    if relationships:
        out.append("relationships:")
        for rel in relationships:
            if not isinstance(rel, dict):
                continue
            from_cols = rel.get("from_columns") or []
            to_cols = rel.get("to_columns") or []
            if not isinstance(from_cols, list) or not isinstance(to_cols, list):
                continue
            # The spec requires equal-length key arrays; a mismatched pair is
            # dropped rather than emitted as an invalid document.
            if len(from_cols) != len(to_cols) or not from_cols:
                continue
            out.append(f"  - name: {_scalar(rel.get('name'))}")
            out.append(f"    from: {_scalar(rel.get('from'))}")
            out.append(f"    to: {_scalar(rel.get('to'))}")
            out.append(f"    from_columns: [{', '.join(str(c) for c in from_cols)}]")
            out.append(f"    to_columns: [{', '.join(str(c) for c in to_cols)}]")

    metrics = spec.get("metrics") or []
    if metrics:
        out.append("metrics:")
        for metric in metrics:
            if not isinstance(metric, dict) or not metric.get("name"):
                continue
            out.append(f"  - name: {_scalar(metric.get('name'))}")
            out.append("    expression:")
            out.append("      dialects:")
            out.append("        - dialect: ANSI_SQL")
            out.append(
                f"          expression: {_scalar(metric.get('expression'))}"
            )
            if metric.get("datatype"):
                out.append(f"    datatype: {_scalar(metric.get('datatype'))}")
            mdesc = _lines("    ", "description", metric.get("description"))
            if mdesc:
                out.append(mdesc)

    return "\n".join(out) + "\n"
