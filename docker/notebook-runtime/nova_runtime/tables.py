"""Bounded table previews for every dataframe kind a notebook produces.

A preview never materialises more than ``limit + 1`` rows: a DuckDB relation
is limited before it runs, pandas/Polars/Arrow are sliced. The row count is
reported only when it is already known (in-memory frames); a lazy relation
says ``total_rows: null`` rather than paying for ``count(*)``.
"""

from __future__ import annotations

from typing import Any

from .jsonsafe import cell_value

TABLE_MIME = "application/vnd.nova.dataframe+json"


def _arrow_preview(table: Any, limit: int) -> tuple[list[dict], list[list], bool]:
    truncated = table.num_rows > limit
    head = table.slice(0, limit)
    schema = [{"name": field.name, "type": str(field.type)} for field in head.schema]
    columns = [head.column(i).to_pylist() for i in range(head.num_columns)]
    rows = [[cell_value(col[r]) for col in columns] for r in range(head.num_rows)]
    return schema, rows, truncated


def kind_of(value: Any) -> str | None:
    module = type(value).__module__ or ""
    name = type(value).__name__
    if module.startswith("duckdb") and name == "DuckDBPyRelation":
        return "duckdb_relation"
    if module.startswith("pandas") and name == "DataFrame":
        return "pandas"
    if module.startswith("pandas") and name == "Series":
        return "pandas_series"
    if module.startswith("polars") and name == "DataFrame":
        return "polars"
    if module.startswith("polars") and name == "LazyFrame":
        return "polars_lazy"
    if module.startswith("pyarrow") and name in {"Table", "RecordBatch"}:
        return "arrow"
    return None


def preview(value: Any, *, limit: int, origin: str = "python_memory") -> dict[str, Any] | None:
    """A Nova table payload for ``value``, or ``None`` when it is not a table."""
    kind = kind_of(value)
    if kind is None:
        return None
    total: int | None = None
    if kind == "duckdb_relation":
        head = value.limit(limit + 1).to_arrow_table()
        schema, rows, truncated = _arrow_preview(head, limit)
        schema = [
            {"name": name, "type": str(dtype)}
            for name, dtype in zip(value.columns, value.types, strict=False)
        ]
        if not truncated:
            total = len(rows)
    elif kind == "pandas_series":
        return preview(value.to_frame(), limit=limit, origin=origin)
    elif kind == "pandas":
        import pyarrow as pa

        total = len(value)
        frame = value.head(limit)
        if frame.index.name is not None or not _plain_index(frame):
            frame = frame.reset_index()
        table = pa.Table.from_pandas(frame, preserve_index=False)
        schema, rows, _ = _arrow_preview(table, limit)
        schema = [
            {"name": str(name), "type": str(dtype)} for name, dtype in frame.dtypes.items()
        ]
        truncated = total > limit
    elif kind == "polars":
        total = value.height
        schema, rows, _ = _arrow_preview(value.head(limit).to_arrow(), limit)
        schema = [{"name": n, "type": str(t)} for n, t in value.schema.items()]
        truncated = total > limit
    elif kind == "polars_lazy":
        head = value.head(limit + 1).collect()
        schema, rows, truncated = _arrow_preview(head.to_arrow(), limit)
        if not truncated:
            total = len(rows)
    else:  # arrow
        total = value.num_rows
        schema, rows, truncated = _arrow_preview(value, limit)
    return {
        "kind": kind,
        "schema": schema,
        "rows": rows,
        "limit": limit,
        "total_rows": total,
        "truncated": truncated,
        "origin": origin,
    }


def _plain_index(frame: Any) -> bool:
    try:
        import pandas as pd

        index = frame.index
        return isinstance(index, pd.RangeIndex) and index.start == 0 and index.step == 1
    except Exception:  # noqa: BLE001
        return True


def describe(value: Any) -> dict[str, Any]:
    """Name-free summary of one variable for the Variables panel.

    Never runs a query or copies data: a lazy relation reports its columns,
    in-memory frames their shape.
    """
    kind = kind_of(value)
    type_name = type(value).__name__
    shape: str | None = None
    preview_text: str | None = None
    if kind == "duckdb_relation":
        shape = f"{len(value.columns)} columns, lazy"
        preview_text = ", ".join(value.columns[:8])
    elif kind in {"pandas", "polars"}:
        rows, cols = value.shape
        shape = f"{rows:,} × {cols}"
        preview_text = ", ".join(str(c) for c in list(value.columns)[:8])
    elif kind == "pandas_series":
        shape = f"{len(value):,}"
    elif kind == "arrow":
        shape = f"{value.num_rows:,} × {value.num_columns}"
        preview_text = ", ".join(value.schema.names[:8])
    elif kind == "polars_lazy":
        shape = "lazy"
    elif isinstance(value, (list, tuple, set, dict, str, bytes)):
        shape = f"{len(value):,}"
    if preview_text is None and kind is None:
        try:
            text = repr(value)
        except Exception:  # noqa: BLE001
            text = f"<{type_name}>"
        preview_text = text if len(text) <= 120 else text[:117] + "…"
    return {"type": type_name, "kind": kind, "shape": shape, "preview": preview_text}
