"""Result matching and structured judge calls.

Answers are judged by execution: two results match when every gold column has
a column of the same values in the answer (extra columns are fine), rows match
in any order unless the plan orders them, and numbers match to four
significant digits. Empty and failed results never match. This follows how
Databricks Genie benchmarks score results.

A model judge is used only where execution cannot decide: whether a question
asks exactly what a plan computes, and whether an ontology claim is supported
by its evidence. Every call has a strict schema, temperature is the provider's
default, and unreadable output is repaired once and then counted as unsure.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

SIGNIFICANT = 4
_MIDNIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]00:00(?::00(?:\.0+)?)?)?$")


def cell(value: Any) -> str:
    """A value as comparable text: dates as ISO days, numbers to 4 significant digits."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time() else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if _MIDNIGHT.match(text):
        return text[:10]
    try:
        number = Decimal(text.rstrip("%"))
    except InvalidOperation:
        return text
    if not number.is_finite():
        return text
    if number == 0:
        return "0"
    return f"{float(number):.{SIGNIFICANT}g}"


def same_result(
    got_columns: list[str], got_rows: list[list[Any]],
    gold_columns: list[str], gold_rows: list[list[Any]], *, ordered: bool = False,
    order_key: str | None = None,
) -> tuple[bool, str]:
    if not gold_rows:
        return False, "the gold result is empty"
    if not got_rows:
        return False, "the answer is empty"
    if len(got_rows) != len(gold_rows):
        return False, f"{len(got_rows)} rows instead of {len(gold_rows)}"
    got = [[cell(value) for value in row] for row in got_rows]
    gold = [[cell(value) for value in row] for row in gold_rows]
    width = len(gold_columns)
    mapping: list[int] = []
    for index in range(width):
        wanted = sorted(row[index] for row in gold)
        match = next(
            (
                column for column in range(len(got_columns))
                if column not in mapping and sorted(row[column] for row in got) == wanted
            ),
            None,
        )
        if match is None:
            return False, f"no column matches {gold_columns[index]}"
        mapping.append(match)
    projected = [[row[column] for column in mapping] for row in got]
    if sorted(projected) != sorted(gold):
        return False, "the values are not paired the same way"
    if ordered:
        # Rows that tie on the sort key may come in any order; the key's sequence may not.
        key = gold_columns.index(order_key) if order_key in gold_columns else None
        sequence = [row[key] for row in projected] if key is not None else projected
        expected = [row[key] for row in gold] if key is not None else gold
        if sequence != expected:
            return False, "rows are in another order"
    return True, ""


async def ask(client, system: str, payload: Any, schema: dict, name: str) -> dict:
    """One structured judge call through the configured provider; one repair."""
    from app.modules.agents.semantic.model_planner import parse_json_object

    config = await client.resolve()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    kwargs: dict[str, Any] = {"provider": config}
    if config.capabilities.supports_json_schema:
        kwargs["response_format"] = {
            "type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    else:
        messages[0]["content"] += " Return only JSON matching: " + json.dumps(schema)
    for attempt in range(2):
        message = await client.complete(messages=messages, **kwargs)
        content = message.get("content") or ""
        try:
            parsed = parse_json_object(content)
        except Exception:  # noqa: BLE001 - unreadable output is repaired once
            parsed = {}
        if parsed or attempt:
            parsed["_model"] = getattr(config, "model", None)
            return parsed
        messages = [
            *messages,
            {"role": "assistant", "content": str(content)[:2000]},
            {"role": "user", "content": "Return only one JSON object matching the schema."},
        ]
    return {"_model": getattr(config, "model", None)}


def strict(properties: dict[str, Any]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": sorted(properties), "properties": properties,
    }


def items_schema(item: dict[str, Any]) -> dict:
    return strict({"items": {"type": "array", "items": strict(item)}})


VERDICT = {"type": "string", "enum": ["yes", "no", "unsure"]}
TEXT = {"type": "string"}


def finite(value: Any) -> bool:
    return isinstance(value, int | float) and math.isfinite(value)
