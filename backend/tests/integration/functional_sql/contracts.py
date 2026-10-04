from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class FunctionSignature:
    signature: str
    return_type: str
    kind: str
    fid: int | None = None

    @property
    def name(self) -> str:
        return self.signature.split("(", 1)[0].lower()

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"{self.signature}|{self.return_type}|{self.kind}".encode()
        ).hexdigest()[:20]


def normalize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"}
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat(sep=" ") if isinstance(value, dt.datetime) else value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, (tuple, list)):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    return value


def json_identity(value: Any) -> tuple:
    if isinstance(value, bool):
        return "boolean", value
    if isinstance(value, (int, float)):
        return "number", Decimal(str(value))
    if isinstance(value, list):
        return "array", tuple(json_identity(item) for item in value)
    if isinstance(value, dict):
        return "object", tuple(sorted((key, json_identity(item)) for key, item in value.items()))
    return type(value).__name__, value


def mysql_map_value(value: Any) -> dict:
    def unique_pairs(pairs):
        mapping = dict(pairs)
        assert len(mapping) == len(pairs), "Duplicate native MAP keys"
        return mapping

    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=unique_pairs)
        except json.JSONDecodeError:
            expression = ast.parse(value, mode="eval")
            assert isinstance(expression.body, ast.Dict), "Expected a native MAP dictionary"

            class NativeConstants(ast.NodeTransformer):
                def visit_Name(self, node):
                    constants = {"null": None, "true": True, "false": False}
                    if node.id not in constants:
                        raise ValueError("Nonliteral native MAP value")
                    return ast.copy_location(ast.Constant(constants[node.id]), node)

            expression = NativeConstants().visit(expression)
            value = ast.literal_eval(expression)
            assert len(value) == len(expression.body.keys), "Duplicate native MAP keys"
    assert isinstance(value, dict), "Expected a native MAP dictionary"
    normalized = normalize(value)
    assert len(normalized) == len(value), "Ambiguous native MAP keys"
    return normalized


class UnverifiedBinding(AssertionError):
    pass


@dataclass(frozen=True)
class EffectCheck:
    sql: str
    expected: Any
    oracle: str = "equals"
    timeout_seconds: float = 0


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    sql: str
    expected: Any = None
    oracle: str = "equals"
    error_code: int | None = None
    error_contains: str | None = None
    setup: tuple[str, ...] = ()
    signatures: tuple[str, ...] = ()
    statement_rules: tuple[str, ...] = ()
    prerequisite: str | None = None
    parameters: tuple | None = None
    contract_source: str | None = None
    effects: tuple[EffectCheck, ...] = ()
    logical_overloads: tuple[str, ...] = ()
    expected_column_types: tuple[int, ...] = ()
    binding_column_types: tuple[int, ...] = ()
    binding_source: str | None = None

    def check(self, rows: list, columns: list) -> None:
        if self.expected_column_types:
            observed = tuple(column[1] for column in columns)
            assert observed == self.expected_column_types, (
                f"expected MySQL column types {self.expected_column_types}; got {observed}"
            )
        actual = normalize(rows)
        if self.oracle == "equals":
            assert actual == normalize(self.expected), f"expected {self.expected!r}; got {actual!r}"
        elif self.oracle == "row_multiset":
            assert sorted(json.dumps(row, sort_keys=True) for row in actual) == sorted(
                json.dumps(row, sort_keys=True) for row in normalize(self.expected)
            ), actual
        elif self.oracle == "numeric_rows":
            assert len(rows) == len(self.expected), f"Unexpected row count: {actual!r}"
            for row, expected in zip(rows, self.expected, strict=True):
                assert len(row) == len(expected), f"Unexpected column count: {actual!r}"
                for value, wanted in zip(row, expected, strict=True):
                    if isinstance(wanted, float):
                        assert isinstance(value, (int, float, Decimal))
                        assert not isinstance(value, bool)
                        assert math.isfinite(float(value)) and math.isclose(
                            float(value), wanted, rel_tol=1e-6, abs_tol=1e-8
                        ), f"expected {wanted!r}; got {value!r}"
                    else:
                        assert normalize(value) == normalize(wanted), (
                            f"expected {wanted!r}; got {value!r}"
                        )
        elif self.oracle == "number":
            assert len(rows) == 1 and len(rows[0]) == 1, f"Unexpected result shape: {actual!r}"
            assert rows[0][0] is not None, f"expected {self.expected!r}; got NULL"
            assert math.isclose(
                float(normalize(rows[0][0])), float(self.expected), rel_tol=1e-6, abs_tol=1e-8
            ), f"expected {self.expected!r}; got {actual!r}"
        elif self.oracle == "nan":
            assert len(rows) == 1 and len(rows[0]) == 1
            assert math.isnan(float(rows[0][0])), f"expected NaN; got {actual!r}"
        elif self.oracle == "unix_now":
            assert len(rows) == 1 and len(rows[0]) == 1
            assert isinstance(rows[0][0], int), actual
            assert abs(rows[0][0] - dt.datetime.now(dt.UTC).timestamp()) <= 120, actual
        elif self.oracle in {"json", "json_multiset", "mysql_json_array", "mysql_map"}:
            assert len(rows) == 1 and len(rows[0]) == 1
            value = rows[0][0]
            if self.oracle == "mysql_map":
                actual_json = mysql_map_value(value)
            elif self.oracle == "mysql_json_array":
                elements = ast.literal_eval(value)
                assert isinstance(elements, list) and all(
                    isinstance(item, str) for item in elements
                )
                actual_json = [json.loads(item) for item in elements]
            else:
                actual_json = json.loads(value) if isinstance(value, str) else value
            if self.oracle == "json_multiset":
                assert isinstance(actual_json, list), actual_json
                assert Counter(json_identity(item) for item in actual_json) == Counter(
                    json_identity(item) for item in self.expected
                ), actual_json
            else:
                assert json_identity(actual_json) == json_identity(self.expected), actual_json
        elif self.oracle in {"ai_json", "sentiment", "label"}:
            assert len(rows) == 1 and len(rows[0]) == 1
            text = str(rows[0][0]).strip()
            assert not text.startswith("ERROR:"), "AI returned a placeholder"
            if self.oracle == "label":
                assert text.strip('".').casefold() == str(self.expected).casefold(), text
            else:
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(text)
                if self.oracle == "ai_json":
                    assert value == self.expected, value
                else:
                    assert value["sentiment"] == self.expected, value
                    assert 0 <= float(value["confidence"]) <= 1, value
        elif self.oracle == "nonempty":
            assert rows and columns
        elif self.oracle == "empty":
            assert not rows
        elif self.oracle == "contains":
            assert str(self.expected).lower() in str(actual).lower()
        elif self.oracle == "column_types":
            assert [column[1] for column in columns] == self.expected
        elif self.oracle == "row_count":
            assert len(rows) == self.expected
        elif self.oracle == "file_listing":
            files = {
                str(row[0]).rstrip("/").rsplit("/", 1)[-1]: int(row[1])
                for row in rows
                if not row[2]
            }
            assert files == self.expected, f"expected files {self.expected!r}; got {files!r}"
            assert len([row for row in rows if not row[2]]) == len(files), "Duplicate file rows"
        elif self.oracle == "task_metadata":
            assert len(rows) == 1 and len(rows[0]) == 8, actual
            UUID(str(rows[0][0]))
            assert actual[0][1:] == self.expected, actual
        elif self.oracle == "task_submission":
            assert len(rows) == 1 and len(rows[0]) == 2, actual
            assert str(rows[0][0]).split(".")[-1] == self.expected, actual
            assert rows[0][1] == "SUBMITTED", actual
        elif self.oracle == "forecast":
            assert [column[0] for column in columns] == [
                "timestamp",
                "series",
                "prediction",
                "lower",
                "upper",
            ], columns
            assert len(rows) == self.expected["horizon"], actual
            for index, row in enumerate(actual):
                assert str(row[0]).startswith(self.expected["dates"][index]), actual
                assert row[1] is None, actual
                assert math.isclose(float(row[2]), self.expected["value"], abs_tol=1e-8), actual
                if row[3] is not None and row[4] is not None:
                    assert float(row[3]) <= float(row[2]) <= float(row[4]), actual
        elif self.oracle == "cluster_separation":
            assert len(rows) == 2 and all(len(row) == 1 for row in rows), actual
            assert all(isinstance(row[0], int) and 0 <= row[0] < self.expected for row in rows), (
                actual
            )
            assert rows[0][0] != rows[1][0], "Separated groups received the same cluster"
        else:
            raise AssertionError(f"Unknown oracle: {self.oracle}")
        if self.binding_column_types:
            observed = tuple(column[1] for column in columns)
            if observed != self.binding_column_types:
                raise UnverifiedBinding(
                    "Value oracle passed, but this recipe does not establish the declared "
                    f"overload: expected MySQL return types {self.binding_column_types}, "
                    f"got {observed}. Verify analyzer promotion or another public binding path."
                )


@dataclass
class Outcome:
    id: str
    group: str
    status: str
    sql: str = ""
    duration_ms: float = 0
    detail: str = ""
    signatures: list[str] = field(default_factory=list)
    statement_rules: list[str] = field(default_factory=list)
    observed_rows: Any = None
    observed_columns: list = field(default_factory=list)
    verification_kind: str = "positive"
    observed_effects: list = field(default_factory=list)
    logical_overloads: list[str] = field(default_factory=list)
    observed_error: dict | None = None
