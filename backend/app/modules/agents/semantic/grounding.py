"""Semantic grounding — turn a parsed Ossie model into a text-to-SQL prompt.

This is Nova's answer to Cortex Analyst's core: given a business question and a
semantic model, produce SQL that uses the model's **logical** names, grounded on
the model's datasets, fields, metrics, and relationships.

Grounding uses **metadata only** — never table rows. The model is told the
datasets, their sources, the fields (with synonyms and time role), the metrics
(with their expressions), and the joins. It returns SQL against the logical
names; the executor maps those to the physical sources.

The grounding text is deliberately explicit about a few things the engine cannot
infer and the model gets wrong without being told:

* the physical source for each dataset (``database.schema.table``);
* which fields are time dimensions;
* that only ``ANSI_SQL``-flavoured expressions are valid, matching Nova's engine;
* to return a **single** read-only statement.
"""

from __future__ import annotations

from typing import Any

#: The instruction block prepended to every semantic prompt. Kept tight so the
#: model spends its budget on the model metadata, not on prose.
_SYSTEM = """You translate a business question into one read-only SQL query for a
StarRocks warehouse, using only the semantic model below.

Rules:
- Use only the datasets, fields, and metrics defined in the model. Do not invent
  tables or columns.
- Reference each dataset by its logical name in your reasoning, and generate SQL
  against the physical source shown for it.
- Use the metric's expression for the metric, not a re-derived formula.
- Prefer the metric's `ai_context.synonyms` when the user's wording matches one.
- For time questions, use the fields marked as time dimensions.
- The query must be a single statement: SELECT or WITH ... SELECT. Never write a
  mutating statement.
- SQL dialect is StarRocks (ANSI-compatible). Do not use vendor-specific syntax
  the model does not define.

Return your answer as a JSON object with exactly these keys:
  "sql": the SQL query string,
  "explanation": one or two sentences, plain language, no SQL jargon,
  "confidence": a number from 0 to 1 for how well the question is answerable
                from this model.
If the question cannot be answered from the model, set "sql" to an empty string
and explain what is missing in "explanation".
"""


def build_grounding_prompt(model: dict[str, Any]) -> str:
    """Render the semantic model as prompt text.

    ``model`` is the parsed definition stored by ``repository`` (see
    ``ossie.parse_ossie().as_dict()``). Output is deterministic for a given
    model, so a cached prompt is safe to reuse.
    """
    lines: list[str] = ["# Semantic model: " + str(model.get("name") or "unnamed")]
    description = (model.get("description") or "").strip()
    if description:
        lines.append(description)

    ai_context = model.get("ai_context")
    instructions = _ai_instructions(ai_context)
    if instructions:
        lines.append("Model instructions: " + instructions)

    lines.append("")
    lines.append("## Datasets")
    for dataset in model.get("datasets") or []:
        lines.append(_dataset_block(dataset))

    relationships = model.get("relationships") or []
    if relationships:
        lines.append("")
        lines.append("## Relationships")
        for rel in relationships:
            lines.append(_relationship_line(rel))

    metrics = model.get("metrics") or []
    if metrics:
        lines.append("")
        lines.append("## Metrics")
        for metric in metrics:
            lines.append(_metric_line(metric))

    return "\n".join(lines)


def build_semantic_messages(model: dict[str, Any], question: str) -> list[dict[str, str]]:
    """The full message list for one semantic text-to-SQL call."""
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": build_grounding_prompt(model)},
        {"role": "user", "content": f"Question: {question}"},
    ]


def _dataset_block(dataset: dict[str, Any]) -> str:
    name = dataset.get("name") or "?"
    source = dataset.get("source") or "?"
    lines = [f"### dataset `{name}` (physical source: {source})"]
    description = (dataset.get("description") or "").strip()
    if description:
        lines.append(description)

    synonyms = _ai_synonyms(dataset.get("ai_context"))
    if synonyms:
        lines.append("Also known as: " + ", ".join(synonyms))

    primary_key = dataset.get("primary_key") or []
    if primary_key:
        lines.append("Primary key: " + ", ".join(str(c) for c in primary_key))

    fields = dataset.get("fields") or []
    if fields:
        lines.append("Fields:")
        for field in fields:
            lines.append("  - " + _field_line(field))

    return "\n".join(lines)


def _field_line(field: dict[str, Any]) -> str:
    name = field.get("name") or "?"
    expression = field.get("expression") or name
    parts = [f"`{name}` = {expression}"]
    # A field whose expression is not a plain column reference is *computed*: the
    # physical table has no such column, so the SQL must inline the expression
    # rather than select the logical name. Say so explicitly; a model that reads
    # only the logical name generates `t.full_name` and the engine rejects it.
    if not _is_plain_column(expression, name):
        parts.append(f"COMPUTED — write ({expression}), there is no column named {name}")
    datatype = field.get("datatype")
    if datatype:
        parts.append(f"type {datatype}")
    if _is_time_dimension(field):
        parts.append("TIME DIMENSION")
    description = (field.get("description") or "").strip()
    if description:
        parts.append(description)
    synonyms = _ai_synonyms(field.get("ai_context"))
    if synonyms:
        parts.append("synonyms: " + ", ".join(synonyms))
    return "; ".join(parts)


def _is_plain_column(expression: str, name: str) -> bool:
    """True when the expression is just the column name (optionally qualified).

    A computed expression (a function call, concatenation, arithmetic) is not a
    column and must be inlined by the generated SQL.
    """
    text = expression.strip().strip("`").strip()
    if text == name:
        return True
    # A dotted reference whose last segment is the field name is still a plain
    # column (`t.col`); anything containing an operator or parenthesis is not.
    if any(ch in text for ch in "()+-*/,'\""):
        return False
    last = text.split(".")[-1].strip("`")
    return last == name


def _relationship_line(rel: dict[str, Any]) -> str:
    from_cols = ", ".join(str(c) for c in (rel.get("from_columns") or []))
    to_cols = ", ".join(str(c) for c in (rel.get("to_columns") or []))
    return f"- `{rel.get('from')}` ({from_cols}) joins `{rel.get('to')}` ({to_cols})"


def _metric_line(metric: dict[str, Any]) -> str:
    name = metric.get("name") or "?"
    expression = metric.get("expression") or ""
    parts = [f"- `{name}` = {expression}"]
    description = (metric.get("description") or "").strip()
    if description:
        parts.append(description)
    synonyms = _ai_synonyms(metric.get("ai_context"))
    if synonyms:
        parts.append("synonyms: " + ", ".join(synonyms))
    return "; ".join(parts)


def _is_time_dimension(field: dict[str, Any]) -> bool:
    dimension = field.get("dimension")
    if isinstance(dimension, dict) and "is_time" in dimension:
        return bool(dimension["is_time"])
    # Default per the Ossie spec: a temporal datatype is a time dimension unless
    # explicitly opted out.
    return field.get("datatype") in {"Date", "Time", "DateTime", "DateTimeTz"}


def _ai_instructions(ai_context: Any) -> str:
    if isinstance(ai_context, str):
        return ai_context.strip()
    if isinstance(ai_context, dict):
        value = ai_context.get("instructions")
        if isinstance(value, str):
            return value.strip()
    return ""


def _ai_synonyms(ai_context: Any) -> list[str]:
    if isinstance(ai_context, dict):
        synonyms = ai_context.get("synonyms")
        if isinstance(synonyms, list):
            return [str(s) for s in synonyms if str(s).strip()]
    return []
