"""A task's ``WHEN`` over Streams, compiled to a small typed tree.

``WHEN SYSTEM$STREAM_HAS_DATA('db.s')`` is not engine SQL: the engine has no
streams. A ``WHEN`` that calls the function is compiled at ``CREATE TASK`` into
a tree of ``and`` / ``or`` / ``not`` over ``has_data(stream)`` leaves, and Nova
evaluates it (``evaluate``) instead of sending ``SELECT (<when>)`` to the
engine. Only that boolean subset is accepted with stream calls; mixing them
with other SQL (``… AND x > 1``) is refused at ``CREATE TASK``, not at run time.
A ``WHEN`` without stream calls keeps its existing engine evaluation.

A task with no ``SCHEDULE`` and a ``WHEN`` that can become true because a
stream has data (a stream call that is not only under ``NOT``) is a
*stream-triggered* task: it runs when its streams have data.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.modules.streams.namespace import StreamName

#: Function spellings, Snowflake's first.
FUNCTIONS = frozenset({"SYSTEM$STREAM_HAS_DATA", "NOVA_STREAM_HAS_DATA"})
_WRAPPERS = frozenset({
    "ExpressionDefaultContext",
    "BooleanExpressionDefaultContext",
    "PredicateContext",
    "ValueExpressionDefaultContext",
    "FunctionCallExpressionContext",
})


class WhenError(ValueError):
    """A stream ``WHEN`` that Nova cannot evaluate; the message is user-facing."""


def mentions_streams(expression: Any) -> bool:
    """Whether the parsed ``WHEN`` calls a stream function anywhere."""
    if type(expression).__name__ == "SimpleFunctionCallContext":
        return _function_name(expression) in FUNCTIONS
    return any(
        mentions_streams(expression.getChild(i)) for i in range(expression.getChildCount())
    )


def compile_when(expression: Any, database: str | None) -> dict[str, Any]:
    """The ``WHEN`` tree; ``WhenError`` for anything outside the boolean subset."""
    return _compile(expression, database)


def _compile(node: Any, database: str | None) -> dict[str, Any]:
    name = type(node).__name__
    if name == "LogicalBinaryContext":
        operator = node.getChild(1).getText().upper()
        if operator not in {"AND", "OR", "&&", "||"}:
            raise WhenError(f"WHEN with streams supports AND, OR and NOT, not {operator}")
        return {
            "op": "and" if operator in {"AND", "&&"} else "or",
            "args": [_compile(node.getChild(0), database), _compile(node.getChild(2), database)],
        }
    if name == "LogicalNotContext":
        return {"op": "not", "args": [_compile(node.getChild(1), database)]}
    if name == "ParenthesizedExpressionContext":
        return _compile(node.getChild(1), database)
    if name == "SimpleFunctionCallContext" and _function_name(node) in FUNCTIONS:
        return {"op": "has_data", "stream": _stream_literal(node, database)}
    if name in _WRAPPERS and node.getChildCount() == 1:
        return _compile(node.getChild(0), database)
    raise WhenError(
        "A WHEN that uses SYSTEM$STREAM_HAS_DATA may combine stream calls only with AND, OR "
        "and NOT"
    )


def _function_name(node: Any) -> str:
    from app.sql_frontend.streams import name_parts

    parts = name_parts(node.qualifiedName())
    return parts[-1].upper() if len(parts) == 1 else ""


def _stream_literal(node: Any, database: str | None) -> str:
    from app.sql_frontend.streams import parse_stream_name

    expressions = node.expression()
    if len(expressions) != 1 or node.over():
        raise WhenError("SYSTEM$STREAM_HAS_DATA takes one stream name in quotes")
    text = expressions[0].getText()
    if len(text) < 2 or text[0] not in {"'", '"'} or text[-1] != text[0] or "\\" in text:
        raise WhenError("SYSTEM$STREAM_HAS_DATA takes one stream name in quotes")
    value = text[1:-1].replace(text[0] * 2, text[0])
    try:
        name = parse_stream_name(value, database)
    except Exception as exc:  # noqa: BLE001 - a bad name is a CREATE TASK error
        raise WhenError(f"{value!r} is not a stream name") from exc
    return f"{name.database}.{name.name}"


def streams_in(tree: dict[str, Any]) -> list[str]:
    if tree["op"] == "has_data":
        return [tree["stream"]]
    return [name for arg in tree["args"] for name in streams_in(arg)]


def triggers_on_data(tree: dict[str, Any], negated: bool = False) -> bool:
    """Whether new stream data can make the tree true (a call not only under NOT)."""
    if tree["op"] == "has_data":
        return not negated
    if tree["op"] == "not":
        return triggers_on_data(tree["args"][0], not negated)
    return any(triggers_on_data(arg, negated) for arg in tree["args"])


def stream_name(text: str) -> StreamName:
    database, name = text.split(".", 1)
    return StreamName(database, name)


async def evaluate(tree: dict[str, Any], has_data: Callable[[str], Awaitable[bool]]) -> bool:
    """Evaluate with short-circuit; ``has_data`` raises on doubt, never returns FALSE."""
    op = tree["op"]
    if op == "has_data":
        return await has_data(tree["stream"])
    if op == "not":
        return not await evaluate(tree["args"][0], has_data)
    if op == "and":
        for arg in tree["args"]:
            if not await evaluate(arg, has_data):
                return False
        return True
    for arg in tree["args"]:
        if await evaluate(arg, has_data):
            return True
    return False


def load(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    tree = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(tree, dict) or tree.get("version") != 1:
        raise WhenError("The task's stream condition has an unknown format")
    return tree["tree"]


def dump(tree: dict[str, Any], dependencies: dict[str, str]) -> str:
    """Persisted form: the tree plus the stream id each name resolved to."""
    return json.dumps(
        {"version": 1, "tree": tree, "streams": dependencies}, separators=(",", ":"),
        sort_keys=True,
    )


def dependencies(raw: Any) -> dict[str, str]:
    tree = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return dict(tree.get("streams") or {})
