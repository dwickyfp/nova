"""The trusted code the worker runs in a kernel around user cells.

Every call is ``__nova__.<function>(<python string literal of JSON>)``: user
text (cell source, names) only ever travels inside a JSON document, quoted by
``repr``, never spliced into Python or SQL. Credentials never travel in code at
all: the worker writes them to a one-time file the kernel reads and deletes.
"""

from __future__ import annotations

import json
from typing import Any

_PRELUDE = "import nova_runtime as __nova__\n"


def _call(function: str, payload: dict[str, Any] | None = None) -> str:
    if payload is None:
        return f"{_PRELUDE}__nova__.{function}()"
    literal = repr(json.dumps(payload, separators=(",", ":"), default=str))
    return f"{_PRELUDE}__nova__.{function}({literal})"


def bootstrap(config: dict[str, Any]) -> str:
    return _call("bootstrap", config)


def rotate(credential_path: str) -> str:
    return _call("rotate", {"credential_path": credential_path})


def begin(execution_id: str) -> str:
    return _call("begin", {"execution_id": execution_id})


def end() -> str:
    return _call("end")


def run_sql(*, source: str, name: str | None, engine: str, preview_rows: int) -> str:
    return _call(
        "run_sql",
        {"source": source, "name": name, "engine": engine, "preview_rows": preview_rows},
    )


def install(packages: list[dict[str, Any]]) -> str:
    return _call("install", {"packages": packages})


def list_outputs() -> str:
    return _call("list_outputs", {"root": "/workspace/outputs"})
